#!/usr/bin/env python
"""Phase 6, T6.3.1 — Single-command CloudPulse closed-loop `flash` demo runner.

Supports two explicit modes so dev laptops and cluster laptops stay strictly
separated and never mislabelled:
  - `cluster` (default for `make demo` / Exit Gate 6):
      Verifies Phase 2/4 artifacts and live k3d/Kubernetes health, refuses to
      start if an HPA is attached to `target-app`, resets `target-app` to
      `MIN_PODS`, brings up Prometheus/target-app port-forwards + forecaster +
      arbiter/WebSocket streamer, and drives the `flash` profile via `k6`.
  - `replay` (`--mode replay`, explicitly `live=False`):
      Runs an offline WebSocket replay rehearsal on a local dev machine without
      Docker/k3d/k6, validating `/healthz`, `/api/snapshot`, and `/ws/stream`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import MIN_PODS, PROJECT_ROOT

NAMESPACE = "cloudpulse"
MONITORING_NAMESPACE = "monitoring"
DEPLOYMENT = "target-app"
DEFAULT_RUN_PREFIX = "demo-flash"


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    missing_tools: tuple[str, ...]
    missing_artifacts: tuple[str, ...]
    errors: tuple[str, ...]


def find_free_port() -> int:
    """Reserve and release an ephemeral localhost TCP port for local rehearsals."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, *, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.5) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (URLError, OSError):
            time.sleep(0.2)
    return False


def wait_for_tcp(host: str, port: int, *, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _default_cmd_runner(
    cmd: list[str], *, cwd: Path = PROJECT_ROOT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def check_cluster_prerequisites(
    root: Path = PROJECT_ROOT,
    *,
    build_images: bool = False,
    which_fn: Callable[[str], str | None] = shutil.which,
    cmd_runner: Callable[..., subprocess.CompletedProcess[str]] = _default_cmd_runner,
) -> PreflightResult:
    """Validate CLI tools, Phase 2/4 artifacts, cluster access, and no competing HPA."""
    required_tools = ["kubectl", "k6"]
    if build_images:
        required_tools.extend(["docker", "k3d"])

    missing_tools = tuple(tool for tool in required_tools if which_fn(tool) is None)

    required_files = [
        "deploy/capacity.yaml",
        "deploy/limits.md",
        "services/arbiter/config.yaml",
        "ml/models/forecaster_bundle.joblib",
        "load/k6_runner.js",
        "load/profiles/flash.json",
    ]
    missing_artifacts = tuple(
        rel for rel in required_files if not (root / rel).exists()
    )

    errors: list[str] = []
    if not missing_tools:
        ns_check = cmd_runner(
            ["kubectl", "get", "deployment", DEPLOYMENT, "-n", NAMESPACE],
            cwd=root,
        )
        if ns_check.returncode != 0:
            errors.append(
                f"Kubernetes deployment {NAMESPACE}/{DEPLOYMENT} unavailable: "
                f"{(ns_check.stderr or ns_check.stdout).strip()}"
            )
        else:
            hpa_check = cmd_runner(
                ["kubectl", "get", "hpa", "-n", NAMESPACE, "-o", "json"],
                cwd=root,
            )
            if hpa_check.returncode == 0 and hpa_check.stdout.strip():
                try:
                    data = json.loads(hpa_check.stdout)
                    items = data.get("items", [])
                    for item in items:
                        ref = item.get("spec", {}).get("scaleTargetRef", {})
                        if ref.get("name") == DEPLOYMENT:
                            hpa_name = item.get("metadata", {}).get("name", "<unknown>")
                            errors.append(
                                f"Refusing demo: HPA `{hpa_name}` is attached to "
                                f"`{DEPLOYMENT}` in `{NAMESPACE}` (delete HPA before running arbiter)."
                            )
                except json.JSONDecodeError as exc:
                    errors.append(f"Could not parse HPA JSON output: {exc}")

    return PreflightResult(
        ok=not (missing_tools or missing_artifacts or errors),
        missing_tools=missing_tools,
        missing_artifacts=missing_artifacts,
        errors=tuple(errors),
    )


def build_and_import_images(root: Path = PROJECT_ROOT) -> int:
    """Build service container images and import them into `k3d` cluster."""
    images = (
        ("cloudpulse/target-app:0.1.1", "apps/target-app/Dockerfile", root / "apps" / "target-app"),
        ("cloudpulse/collector:latest", "services/collector/Dockerfile", root),
        ("cloudpulse/forecaster:latest", "services/forecaster/Dockerfile", root),
        ("cloudpulse/arbiter:latest", "services/arbiter/Dockerfile", root),
    )
    for tag, dockerfile, context in images:
        cmd = ["docker", "build", "-t", tag, "-f", str(root / dockerfile), str(context)]
        print("+", " ".join(cmd), flush=True)
        if subprocess.run(cmd, cwd=root, check=False).returncode != 0:
            return 1
    for tag, _, _ in images:
        cmd = ["k3d", "image", "import", tag, "-c", "cloudpulse"]
        print("+", " ".join(cmd), flush=True)
        if subprocess.run(cmd, cwd=root, check=False).returncode != 0:
            return 1
    return 0


def _reset_target_replicas(replicas: int = MIN_PODS) -> int:
    cmd = [
        "kubectl",
        "scale",
        f"deployment/{DEPLOYMENT}",
        "-n",
        NAMESPACE,
        f"--replicas={replicas}",
    ]
    print("+", " ".join(cmd), flush=True)
    if subprocess.run(cmd, cwd=PROJECT_ROOT, check=False).returncode != 0:
        return 1
    wait_cmd = [
        "kubectl",
        "rollout",
        "status",
        f"deployment/{DEPLOYMENT}",
        "-n",
        NAMESPACE,
        "--timeout=90s",
    ]
    print("+", " ".join(wait_cmd), flush=True)
    return subprocess.run(wait_cmd, cwd=PROJECT_ROOT, check=False).returncode


def _terminate_processes(procs: list[subprocess.Popen[Any]]) -> None:
    for proc in reversed(procs):
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 5.0
    for proc in reversed(procs):
        if proc.poll() is not None:
            continue
        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


def run_cluster_demo(args: argparse.Namespace) -> int:
    """Bring up port-forwards, forecaster, arbiter+WS, and drive `flash` via k6."""
    pre = check_cluster_prerequisites(PROJECT_ROOT, build_images=args.build_images)
    if not pre.ok:
        print("Cluster demo preflight failed:", file=sys.stderr)
        if pre.missing_tools:
            print(f"  Missing CLI tools: {', '.join(pre.missing_tools)}", file=sys.stderr)
        if pre.missing_artifacts:
            print(
                f"  Missing Phase 2/4 artifacts: {', '.join(pre.missing_artifacts)}",
                file=sys.stderr,
            )
        for err in pre.errors:
            print(f"  Error: {err}", file=sys.stderr)
        print(
            "Run `--mode replay` for offline WebSocket rehearsal on this machine, "
            "or run on the 15.6 GB cluster laptop after Phases 2 and 4.",
            file=sys.stderr,
        )
        return 2

    if args.build_images and build_and_import_images(PROJECT_ROOT) != 0:
        return 1

    if _reset_target_replicas(MIN_PODS) != 0:
        print("ERROR: Failed to reset target-app replicas to baseline.", file=sys.stderr)
        return 1

    run_id = args.run_id or f"{DEFAULT_RUN_PREFIX}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    out_dir = PROJECT_ROOT / "experiments" / "results" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    k6_summary_path = out_dir / "k6_summary.json"
    decisions_path = out_dir / "decisions.jsonl"

    procs: list[subprocess.Popen[Any]] = []
    try:
        pf_prom = subprocess.Popen(
            ["kubectl", "port-forward", "-n", MONITORING_NAMESPACE, "svc/prometheus", "9090:9090"],
            cwd=PROJECT_ROOT,
        )
        procs.append(pf_prom)
        pf_app = subprocess.Popen(
            ["kubectl", "port-forward", "-n", NAMESPACE, f"svc/{DEPLOYMENT}", "8000:8000"],
            cwd=PROJECT_ROOT,
        )
        procs.append(pf_app)

        if not wait_for_http("http://127.0.0.1:9090/-/ready", timeout_s=30.0):
            print("ERROR: Prometheus port-forward did not become ready on :9090.", file=sys.stderr)
            return 1
        if not wait_for_http("http://127.0.0.1:8000/healthz", timeout_s=30.0):
            print("ERROR: target-app port-forward did not become ready on :8000.", file=sys.stderr)
            return 1

        forecaster = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "services.forecaster.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8001",
                "--workers",
                "1",
            ],
            cwd=PROJECT_ROOT,
        )
        procs.append(forecaster)
        if not wait_for_http("http://127.0.0.1:8001/healthz", timeout_s=30.0):
            print("ERROR: Forecaster failed to become healthy on :8001.", file=sys.stderr)
            return 1

        arbiter_cmd = [
            sys.executable,
            "-m",
            "services.arbiter.loop",
            "--run-id",
            run_id,
            "--prom-url",
            "http://127.0.0.1:9090",
            "--forecaster-url",
            "http://127.0.0.1:8001",
            "--ws-host",
            "127.0.0.1",
            "--ws-port",
            str(args.ws_port),
            "--log-path",
            str(decisions_path),
        ]
        if args.pg_dsn:
            arbiter_cmd.extend(["--pg-dsn", args.pg_dsn])
        arbiter = subprocess.Popen(arbiter_cmd, cwd=PROJECT_ROOT)
        procs.append(arbiter)

        if not wait_for_http(f"http://127.0.0.1:{args.ws_port}/healthz", timeout_s=30.0):
            print(f"ERROR: Arbiter WS server failed on :{args.ws_port}.", file=sys.stderr)
            return 1

        if args.with_dashboard:
            npm_bin = shutil.which("npm")
            if npm_bin:
                dash = subprocess.Popen(
                    [npm_bin, "run", "dev"],
                    cwd=PROJECT_ROOT / "dashboard",
                )
                procs.append(dash)
                print("Dashboard starting at http://127.0.0.1:3000", flush=True)

        print(
            f"Demo loop active (run_id={run_id}, live=True, ws=ws://127.0.0.1:{args.ws_port}/ws/stream).",
            flush=True,
        )

        k6_cmd = [
            "k6",
            "run",
            "-e",
            "TARGET_URL=http://127.0.0.1:8000",
            "-e",
            "PROFILE=flash",
            "-e",
            f"RPS_SCALE={args.rps_scale}",
            "-e",
            f"DURATION_SCALE={args.duration_scale}",
            "-e",
            f"K6_SUMMARY_OUT={k6_summary_path.as_posix()}",
            "load/k6_runner.js",
        ]
        print("+", " ".join(k6_cmd), flush=True)
        k6_rc = subprocess.run(k6_cmd, cwd=PROJECT_ROOT, check=False).returncode
        return k6_rc
    finally:
        _terminate_processes(procs)
        _reset_target_replicas(MIN_PODS)


async def _verify_replay_ws_stream(port: int, *, min_messages: int = 3) -> dict[str, Any]:
    import websockets

    uri = f"ws://127.0.0.1:{port}/ws/stream"
    seen_types: list[str] = []
    run_id = ""
    live_flag = True
    async with websockets.connect(uri) as ws:
        for _ in range(max(25, min_messages)):
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            msg_type = msg.get("type", "")
            seen_types.append(msg_type)
            if msg_type == "snapshot":
                meta = msg.get("payload", {}).get("meta", {})
                run_id = str(meta.get("runId", ""))
                live_flag = bool(meta.get("live", True))
            if {"snapshot", "telemetry", "forecast", "decision"}.issubset(seen_types):
                break
    return {"seen_types": seen_types, "run_id": run_id, "live": live_flag}


def run_replay_demo(args: argparse.Namespace) -> int:
    """Run offline WebSocket replay rehearsal (`live=False`) without cluster deps."""
    for iteration in range(1, args.rehearsals + 1):
        port = args.ws_port if (args.ws_port != 8002 and args.rehearsals == 1) else find_free_port()
        run_id = f"{args.run_id or 'ws-replay-demo'}-{iteration:02d}"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "services.arbiter.ws",
                "--replay",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--replay-tick-s",
                str(args.replay_tick_s),
                "--run-id",
                run_id,
            ],
            cwd=PROJECT_ROOT,
        )
        try:
            if not wait_for_http(f"http://127.0.0.1:{port}/healthz", timeout_s=20.0):
                print(f"ERROR: Replay WS server failed on port {port}.", file=sys.stderr)
                return 1
            with urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=3.0) as resp:
                if resp.status != 200:
                    print(f"ERROR: Unexpected /api/snapshot status: {resp.status}", file=sys.stderr)
                    return 1
            summary = asyncio.run(_verify_replay_ws_stream(port, min_messages=10))
            required = {"snapshot", "telemetry", "forecast", "decision"}
            if not required.issubset(summary["seen_types"]):
                print(
                    f"ERROR: Missing WS event types in rehearsal {iteration}: {summary}",
                    file=sys.stderr,
                )
                return 1
            if summary["live"] is not False:
                print("ERROR: Replay stream illegally reported live=True.", file=sys.stderr)
                return 1
            print(
                f"[rehearsal {iteration}/{args.rehearsals}] PASS "
                f"(run_id={summary['run_id']}, live={summary['live']}, "
                f"events={len(summary['seen_types'])}, port={port})",
                flush=True,
            )
        finally:
            _terminate_processes([proc])

    print(
        "NOTE: Offline replay rehearsal completed (live=False). "
        "Run `make demo` (`--mode cluster`) twice on the 15.6 GB cluster laptop for Exit Gate 6.",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CloudPulse Phase 6 end-to-end demo runner (T6.3).")
    ap.add_argument(
        "--mode",
        choices=("cluster", "replay"),
        default="cluster",
        help="`cluster` for real k3d+k6 flash demo; `replay` for offline WS rehearsal (`live=False`).",
    )
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--ws-port", type=int, default=8002)
    ap.add_argument("--pg-dsn", default=None)
    ap.add_argument("--build-images", action="store_true")
    ap.add_argument("--with-dashboard", action="store_true")
    ap.add_argument("--rps-scale", type=float, default=1.0)
    ap.add_argument("--duration-scale", type=float, default=1.0)
    ap.add_argument("--replay-tick-s", type=float, default=0.05)
    ap.add_argument("--rehearsals", type=int, default=1)
    args = ap.parse_args(argv)

    if args.mode == "replay":
        return run_replay_demo(args)
    return run_cluster_demo(args)


if __name__ == "__main__":
    raise SystemExit(main())
