#!/usr/bin/env python
"""Phase 2, T2.2 — saturation test to measure per-pod capacity constant C.

Pins target-app to 1 replica (T2.2.1), steps offered RPS upward using open-loop
k6 (`load/k6_runner.js`), queries Prometheus per-pod CPU % using canonical query
#3 (`deploy/queries.md`), and selects the sustainable single-pod RPS at
<= 70% CPU with client-side p99 <= 100 ms.

Then repeats at 3 replicas (T2.2.2) to verify near-linear scaling within 15% of
`3 * C` before writing `deploy/capacity.yaml`.

Usage (on the 15.6 GB cluster laptop with port-forwards active):
    .venv/Scripts/python.exe experiments/measure_capacity.py `
        --target-url http://127.0.0.1:8000 `
        --prom-url http://127.0.0.1:9090
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import PROJECT_ROOT, SLO_MS
from experiments.k6_summary import parse_k6_summary

NAMESPACE = "cloudpulse"
DEPLOYMENT = "target-app"

# Canonical query #3 from deploy/queries.md (per-pod CPU utilisation % against
# container_spec_cpu_quota — never against requests).
PER_POD_CPU_PCT_PROMQL = (
    "max("
    'rate(container_cpu_usage_seconds_total{namespace="cloudpulse",container="target-app"}[1m])'
    " / on(pod) group_left "
    '(container_spec_cpu_quota{namespace="cloudpulse",container="target-app"}'
    ' / container_spec_cpu_period{namespace="cloudpulse",container="target-app"})'
    ") * 100"
)

# Fallback query when cAdvisor quota/period gauges are unavailable; divided by
# the Deployment's configured CPU limit in cores (e.g. 0.25 for 250m).
MAX_POD_CPU_CORES_PROMQL = (
    'max(rate(container_cpu_usage_seconds_total{namespace="cloudpulse",container="target-app"}[1m]))'
)


def select_capacity_point(
    steps: list[dict[str, Any]],
    target_cpu_pct: float = 70.0,
    slo_ms: float = SLO_MS,
    max_tracking_error_pct: float = 5.0,
) -> dict[str, Any]:
    """Select the highest measured sustainable RPS step inside CPU & SLO bounds.

    Every candidate must satisfy:
      1. `dropped_iterations == 0` and `|tracking_error_pct| <= max_tracking_error_pct`
      2. `client_p99_ms <= slo_ms`
      3. `per_pod_cpu_pct <= target_cpu_pct`
    """
    if not steps:
        raise ValueError("Cannot select capacity C from an empty step list")

    eligible: list[dict[str, Any]] = []
    for step in steps:
        if int(step.get("dropped_iterations", 0)) > 0:
            continue
        if abs(float(step["tracking_error_pct"])) > max_tracking_error_pct:
            continue
        if float(step["client_p99_ms"]) > slo_ms:
            continue
        if float(step["per_pod_cpu_pct"]) <= target_cpu_pct:
            eligible.append(step)

    if not eligible:
        raise ValueError(
            "No step satisfied per_pod_cpu_pct <= "
            f"{target_cpu_pct}% and client_p99_ms <= {slo_ms} ms with healthy k6 tracking"
        )

    return max(eligible, key=lambda s: float(s["achieved_rps"]))


def check_linearity(
    c_single: float,
    c_multi: float,
    replicas: int = 3,
    tolerance: float = 0.15,
) -> dict[str, Any]:
    """Verify `replicas`-pod capacity scales within `tolerance` of `replicas * C`."""
    if c_single <= 0 or c_multi <= 0 or replicas < 1:
        raise ValueError("Capacities and replica count must be positive")
    expected = replicas * c_single
    ratio = c_multi / expected
    deviation = abs(c_multi - expected) / expected
    return {
        "replicas": replicas,
        "c_single_rps": round(c_single, 3),
        "expected_multi_rps": round(expected, 3),
        "measured_multi_rps": round(c_multi, 3),
        "scaling_ratio": round(ratio, 4),
        "deviation": round(deviation, 4),
        "tolerance": tolerance,
        "within_tolerance": deviation <= tolerance,
    }


def _query_prom_scalar(prom_url: str, expr: str) -> float | None:
    from prometheus_api_client import PrometheusConnect

    prom = PrometheusConnect(url=prom_url, disable_ssl=True)
    result = prom.custom_query(query=expr)
    if not result:
        return None
    return float(result[0]["value"][1])


def _query_per_pod_cpu_pct(prom_url: str, cpu_limit_cores: float) -> float:
    val = _query_prom_scalar(prom_url, PER_POD_CPU_PCT_PROMQL)
    if val is not None:
        return val
    cores = _query_prom_scalar(prom_url, MAX_POD_CPU_CORES_PROMQL)
    if cores is None:
        raise RuntimeError(
            "Prometheus returned empty result for both per-pod utilisation and CPU cores"
        )
    return (cores / cpu_limit_cores) * 100.0


def _scale_and_wait(replicas: int, timeout_s: float = 120.0) -> None:
    from kubernetes import client, config

    config.load_kube_config()
    apps = client.AppsV1Api()
    apps.patch_namespaced_deployment_scale(
        DEPLOYMENT, NAMESPACE, {"spec": {"replicas": replicas}}
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        dep = apps.read_namespaced_deployment(DEPLOYMENT, NAMESPACE)
        st = dep.status
        if (
            (st.replicas or 0) == replicas
            and (st.ready_replicas or 0) == replicas
            and (st.unavailable_replicas or 0) == 0
        ):
            return
        time.sleep(1.0)
    raise TimeoutError(f"Deployment {DEPLOYMENT} did not stabilize at {replicas} replicas")


def _run_k6_step(
    target_url: str,
    target_rps: int,
    duration_s: int,
    summary_path: Path,
) -> dict[str, Any]:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "TARGET_URL": target_url,
            "TARGET_RPS": str(target_rps),
            "DURATION_S": str(duration_s),
            "K6_SUMMARY_OUT": str(summary_path),
        }
    )
    runner = PROJECT_ROOT / "load" / "k6_runner.js"
    proc = subprocess.run(["k6", "run", str(runner)], env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"k6 exited with status {proc.returncode}")
    return parse_k6_summary(summary_path)


def _sweep_replicas(
    replicas: int,
    rps_steps: list[int],
    step_duration_s: int,
    settle_s: int,
    target_url: str,
    prom_url: str,
    cpu_limit_cores: float,
    outdir: Path,
) -> list[dict[str, Any]]:
    print(f"\n=== Scaling {DEPLOYMENT} to {replicas} replica(s) ===", flush=True)
    _scale_and_wait(replicas)
    time.sleep(settle_s)

    records: list[dict[str, Any]] = []
    for rps in rps_steps:
        summary_file = outdir / f"r{replicas}_rps{rps}_k6.json"
        print(f"  step rps={rps} duration={step_duration_s}s ...", flush=True)
        k6 = _run_k6_step(target_url, rps, step_duration_s, summary_file)
        cpu_pct = _query_per_pod_cpu_pct(prom_url, cpu_limit_cores)
        record = {
            "replicas": replicas,
            "target_rps": rps,
            "achieved_rps": k6["achieved_rps"],
            "tracking_error_pct": k6["tracking_error_pct"],
            "dropped_iterations": k6["dropped_iterations"],
            "per_pod_cpu_pct": round(cpu_pct, 2),
            "client_p50_ms": k6["latency_ms"]["p50"],
            "client_p90_ms": k6["latency_ms"]["p90"],
            "client_p99_ms": k6["latency_ms"]["p99"],
            "sla_violation_rate": k6["sla_violation_rate"],
        }
        records.append(record)
        print(
            f"    achieved={record['achieved_rps']} RPS | cpu={record['per_pod_cpu_pct']}% | "
            f"p99={record['client_p99_ms']} ms",
            flush=True,
        )
        time.sleep(settle_s)
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure per-pod capacity C (Phase 2, T2.2).")
    ap.add_argument("--target-url", default="http://127.0.0.1:8000")
    ap.add_argument("--prom-url", default="http://127.0.0.1:9090")
    ap.add_argument("--step-duration", type=int, default=75, help="Seconds per step (> 60s for Prom rate[1m])")
    ap.add_argument("--settle", type=int, default=20, help="Cooldown seconds between steps")
    ap.add_argument("--single-steps", type=int, nargs="+", default=[4, 8, 10, 12, 14, 16, 18, 20])
    ap.add_argument("--cpu-limit-cores", type=float, default=0.25, help="Pod CPU limit in cores (fallback if quota gauge absent)")
    ap.add_argument("--restore-replicas", type=int, default=3)
    args = ap.parse_args(argv)

    run_id = datetime.now(timezone.utc).strftime("capacity-%Y%m%d-%H%M%S")
    outdir = PROJECT_ROOT / "experiments" / "results" / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        single_records = _sweep_replicas(
            1, args.single_steps, args.step_duration, args.settle,
            args.target_url, args.prom_url, args.cpu_limit_cores, outdir,
        )
        c1_step = select_capacity_point(single_records)
        c1 = float(c1_step["achieved_rps"])

        multi_steps = [3 * s for s in args.single_steps]
        multi_records = _sweep_replicas(
            3, multi_steps, args.step_duration, args.settle,
            args.target_url, args.prom_url, args.cpu_limit_cores, outdir,
        )
        c3_step = select_capacity_point(multi_records)
        c3 = float(c3_step["achieved_rps"])
        linearity = check_linearity(c1, c3, replicas=3)
    finally:
        try:
            _scale_and_wait(args.restore_replicas)
        except Exception as exc:
            print(f"WARN: failed to restore replicas to {args.restore_replicas}: {exc}", file=sys.stderr)

    curve_payload = {
        "run_id": run_id,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "target_cpu_pct": 70.0,
        "slo_ms": SLO_MS,
        "single_pod_steps": single_records,
        "three_pod_steps": multi_records,
        "selected_single": c1_step,
        "selected_three": c3_step,
        "linearity": linearity,
    }
    (outdir / "capacity_curve.json").write_text(
        json.dumps(curve_payload, indent=2), encoding="utf-8"
    )

    capacity_yaml = {
        "pod_capacity_rps": round(c1, 3),
        "target_utilisation_pct": 70.0,
        "slo_ms": SLO_MS,
        "source_run_id": run_id,
        "measured_at": curve_payload["measured_at"],
        "single_pod_measurement": {
            "achieved_rps": c1_step["achieved_rps"],
            "per_pod_cpu_pct": c1_step["per_pod_cpu_pct"],
            "client_p99_ms": c1_step["client_p99_ms"],
        },
        "linearity_check_3_pods": linearity,
    }
    cap_path = PROJECT_ROOT / "deploy" / "capacity.yaml"
    cap_path.write_text(yaml.safe_dump(capacity_yaml, sort_keys=False), encoding="utf-8")

    print(f"\nMeasured C = {capacity_yaml['pod_capacity_rps']} RPS/pod -> {cap_path}")
    print(f"3-pod linearity within 15%: {linearity['within_tolerance']} (ratio={linearity['scaling_ratio']})")
    return 0 if linearity["within_tolerance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
