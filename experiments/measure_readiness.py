#!/usr/bin/env python
"""T2.3.1 - pod readiness characterisation.

Measures how long new pods take to become Ready after the replica count is
patched, across a sweep of WARMUP_SECONDS. The p95 of that distribution is the
dominant term in the forecast horizon H (CLAUDE.md 3.3):

    H >= t_ready_p95 + t_decision_interval + t_safety

WHAT IS MEASURED
    Wall clock from issuing the scale patch to the pod reporting Ready, taken
    client-side at 250 ms resolution.

    Deliberately NOT `metadata.creationTimestamp` -> Ready condition
    `lastTransitionTime`. Kubernetes serialises those at 1-second resolution,
    so on a ~3 s measurement the quantisation error is ~30% - useless here.
    Patch-to-Ready is also the operationally honest number: it includes the API
    server, scheduler and kubelet latency the arbiter genuinely has to wait out
    before new capacity exists.

KNOWN OMISSION
    Endpoint propagation - kube-proxy updating iptables so the Service actually
    routes to the new pod - happens *after* Ready and is not captured here. It
    is sub-second on a single local node but non-zero. This is one reason H
    carries a safety margin instead of being set to the p95 exactly.

BATCHING
    Pods are added `--burst` at a time rather than one at a time, because that
    is what the arbiter does in practice, and because concurrent starts share
    node CPU the way a real scale-up does. Each cycle yields `--burst` samples.

USAGE
    .venv/Scripts/python experiments/measure_readiness.py --cycles 7
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from kubernetes import client, config
from kubernetes.client.rest import ApiException

NAMESPACE = "cloudpulse"
DEPLOYMENT = "target-app"
CONTAINER = "target-app"
POLL_SECONDS = 0.25


# --------------------------------------------------------------------------
# cluster helpers
# --------------------------------------------------------------------------

def connect() -> tuple[client.CoreV1Api, client.AppsV1Api]:
    config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api()


def list_pods(core: client.CoreV1Api) -> list:
    return core.list_namespaced_pod(
        NAMESPACE, label_selector=f"app={DEPLOYMENT}"
    ).items


def is_ready(pod) -> bool:
    if pod.status is None or pod.status.conditions is None:
        return False
    return any(
        c.type == "Ready" and c.status == "True" for c in pod.status.conditions
    )


def set_warmup(apps: client.AppsV1Api, seconds: int) -> None:
    """Strategic-merge patch; env merges by name, so other vars survive."""
    body = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": CONTAINER,
                            "env": [
                                {"name": "WARMUP_SECONDS", "value": str(seconds)}
                            ],
                        }
                    ]
                }
            }
        }
    }
    apps.patch_namespaced_deployment(DEPLOYMENT, NAMESPACE, body)


def scale_to(apps: client.AppsV1Api, replicas: int) -> None:
    apps.patch_namespaced_deployment_scale(
        DEPLOYMENT, NAMESPACE, {"spec": {"replicas": replicas}}
    )


def wait_stable(apps: client.AppsV1Api, replicas: int, timeout: float) -> bool:
    """Wait until the deployment is fully settled at `replicas`.

    Checks observedGeneration too, so a rollout triggered by set_warmup() is
    complete and no old-ReplicaSet pods linger to pollute the next cycle.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = apps.read_namespaced_deployment(DEPLOYMENT, NAMESPACE)
        s, m = d.status, d.metadata
        if (
            (s.observed_generation or 0) >= (m.generation or 0)
            and (s.replicas or 0) == replicas
            and (s.ready_replicas or 0) == replicas
            and (s.updated_replicas or 0) == replicas
            and (s.unavailable_replicas or 0) == 0
        ):
            return True
        time.sleep(POLL_SECONDS)
    return False


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------

def measure_cycle(
    core: client.CoreV1Api,
    apps: client.AppsV1Api,
    base: int,
    burst: int,
    timeout: float,
) -> list[float]:
    """One scale-up. Returns seconds-to-Ready for each newly created pod."""
    before = {p.metadata.name for p in list_pods(core)}

    t0 = time.monotonic()
    scale_to(apps, base + burst)

    seen: dict[str, float] = {}
    deadline = t0 + timeout
    while time.monotonic() < deadline and len(seen) < burst:
        for pod in list_pods(core):
            name = pod.metadata.name
            if name in before or name in seen:
                continue
            if is_ready(pod):
                seen[name] = time.monotonic() - t0
        if len(seen) < burst:
            time.sleep(POLL_SECONDS)

    # Restore baseline and let it settle, so the next cycle starts clean.
    scale_to(apps, base)
    wait_stable(apps, base, timeout=120)

    return sorted(seen.values())


def quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile. Honest for small n - no interpolation between
    samples we never observed."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(q * len(ordered) + 0.5)) - 1))
    return ordered[idx]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--warmups",
        type=int,
        nargs="+",
        default=[0, 5, 15, 30, 60],
        help="WARMUP_SECONDS values to sweep",
    )
    ap.add_argument(
        "--cycles",
        type=int,
        nargs="+",
        default=[10],
        help="scale-ups per setting; one value, or one per warmup",
    )
    ap.add_argument("--base", type=int, default=3, help="baseline replica count")
    ap.add_argument("--burst", type=int, default=3, help="pods added per cycle")
    args = ap.parse_args()

    core, apps = connect()

    cycles = args.cycles
    if len(cycles) == 1:
        cycles = cycles * len(args.warmups)
    if len(cycles) != len(args.warmups):
        print("ERROR: --cycles must be one value or one per warmup")
        return 2

    run_id = datetime.now(timezone.utc).strftime("readiness-%Y%m%d-%H%M%S")
    outdir = Path(__file__).resolve().parent / "results" / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    total = sum(cycles)
    print(f"run_id       : {run_id}")
    print(f"warmups      : {args.warmups}")
    print(f"cycles       : {cycles} (burst {args.burst})")
    print(f"scale-ups    : {total} total")
    print(flush=True)

    # Per-pod times within one batch are correlated - pods started together
    # become Ready together, so they are one observation, not `burst` of them.
    # `cycle_max` (time until ALL requested capacity is Ready) is the
    # independent observation, and is also the operationally meaningful one:
    # the arbiter is not done waiting until the last pod it asked for serves.
    # H is derived from cycle_max, never from the per-pod pool.
    results: dict[str, list[float]] = {}
    cycle_maxima: dict[str, list[float]] = {}
    done = 0

    for warmup, n_cycles in zip(args.warmups, cycles):
        print(f"--- WARMUP_SECONDS={warmup} ({n_cycles} cycles) ---", flush=True)
        set_warmup(apps, warmup)
        if not wait_stable(apps, args.base, timeout=300):
            print("  ERROR: deployment did not settle after warmup patch")
            return 1

        samples: list[float] = []
        maxima: list[float] = []
        for i in range(n_cycles):
            got = measure_cycle(
                core, apps, args.base, args.burst, timeout=warmup + 150
            )
            samples.extend(got)
            if len(got) == args.burst:
                maxima.append(max(got))
            done += 1
            shown = ", ".join(f"{v:.2f}" for v in got) or "NONE (timed out)"
            print(f"  cycle {i + 1}/{n_cycles} "
                  f"[{done}/{total}]: {shown}", flush=True)

        results[str(warmup)] = samples
        cycle_maxima[str(warmup)] = maxima
        if maxima:
            print(f"  -> cycles n={len(maxima)} "
                  f"p50={statistics.median(maxima):.2f}s "
                  f"max={max(maxima):.2f}s", flush=True)
        print(flush=True)

    (outdir / "samples.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "base_replicas": args.base,
                "burst": args.burst,
                "cycles": args.cycles,
                "poll_seconds": POLL_SECONDS,
                "metric": "seconds from scale patch to pod Ready (client-side)",
                "note": (
                    "per_pod samples within a batch are correlated; "
                    "cycle_maxima are the independent observations and the "
                    "basis for H"
                ),
                "per_pod_by_warmup_seconds": results,
                "cycle_maxima_by_warmup_seconds": cycle_maxima,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 70)
    print("time from scale patch until ALL requested pods are Ready")
    print(f"{'warmup':>7} {'cycles':>7} {'p50':>8} {'p95':>8} {'max':>8} "
          f"{'overhead':>10}")
    print("=" * 70)
    overheads: list[float] = []
    for warmup in args.warmups:
        m = cycle_maxima[str(warmup)]
        if not m:
            print(f"{warmup:>7} {0:>7}   no complete cycles")
            continue
        mx = max(m)
        # Cost that is NOT the app's own warmup: scheduling, container start,
        # and readiness-probe detection lag. This is the floor H cannot go
        # below no matter how fast the application itself starts.
        overhead = mx - warmup
        overheads.append(overhead)
        print(f"{warmup:>7} {len(m):>7} {statistics.median(m):>8.2f} "
              f"{quantile(m, 0.95):>8.2f} {mx:>8.2f} {overhead:>10.2f}")
    print("=" * 70)
    if overheads:
        print(f"\nplatform overhead above warmup: "
              f"min {min(overheads):.2f}s  max {max(overheads):.2f}s")
        print("(scheduling + container start + probe detection; "
              "excludes endpoint propagation)")
    print(f"\nwritten: {outdir / 'samples.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApiException as exc:
        print(f"Kubernetes API error: {exc.status} {exc.reason}", file=sys.stderr)
        sys.exit(1)
