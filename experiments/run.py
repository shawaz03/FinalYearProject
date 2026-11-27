#!/usr/bin/env python
"""Phase 7, T7.1 — Sequential CloudPulse experiment runner & suite planner.

Runs one controller at a time against `cloudpulse/target-app`:
  - `T7.1.1`: Accepts `(scaler, profile, warmup, seed, rep)` (+ optional ablation),
    enforces a clean cluster state and settle period between runs, and writes a
    validated Phase-0 `manifest.json` (`experiments/manifest.py`).
  - `T7.1.2`: Sequential execution only — refuses to start if an HPA is
    currently attached to `target-app`, and prevents concurrent runner
    processes via an atomic `.runner.lock`.
  - `T7.2`–`T7.5`: Generates deterministic, deduplicated run plans across
    tuned baselines, ablations, `WARMUP_SECONDS` sweeps, and `n = 5` seeds.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Protocol

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import (
    DECISION_INTERVAL_S,
    PROJECT_ROOT,
    SCRAPE_INTERVAL_S,
    SLO_MS,
)
from cloudpulse_core.seeds import DEFAULT_SEED, set_global_seed
from experiments.k6_summary import PROFILES_DIR, load_profile, parse_k6_summary, validate_profile
from experiments.manifest import validate_manifest
from experiments.scalers import (
    VALID_ABLATIONS,
    VALID_SCALERS,
    build_forecaster_for_experiment,
    compute_static_replicas,
)
from scripts.demo import find_free_port, wait_for_http
from services.arbiter.capacity import ArbiterLimits, load_arbiter_limits

NAMESPACE: Final[str] = "cloudpulse"
MONITORING_NAMESPACE: Final[str] = "monitoring"
DEPLOYMENT: Final[str] = "target-app"
CONTAINER: Final[str] = "target-app"

MAIN_PROFILES: Final[tuple[str, ...]] = ("gradual", "flash", "diurnal")
WARMUP_SWEEP_SECONDS: Final[tuple[int, ...]] = (0, 5, 15, 30, 60)
WARMUP_SWEEP_SCALERS: Final[tuple[str, ...]] = ("cloudpulse", "hpa-tuned")
DEFAULT_CLUSTER_RESOURCES: Final[dict[str, Any]] = {
    "runtime": "k3d-v5.9.0-k3s-v1.35.5",
    "node_allocatable_cpu_cores": 8,
    "node_allocatable_memory_gib": 5.79,
    "pod_cpu_request": "250m",
    "pod_cpu_limit": "250m",
    "pod_memory_request": "128Mi",
    "pod_memory_limit": "128Mi",
}


@dataclass(frozen=True)
class ExperimentSpec:
    """Complete reproducible configuration for a single Phase 7 benchmark run."""

    scaler: str
    profile: str
    warmup_seconds: int = 30
    work_iterations: int = 15000
    seed: int = DEFAULT_SEED
    repetition: int = 1
    ablation: str | None = None

    def __post_init__(self) -> None:
        if self.scaler not in VALID_SCALERS:
            raise ValueError(
                f"Unknown scaler {self.scaler!r}; expected one of {VALID_SCALERS}"
            )
        if self.ablation is not None:
            if self.ablation not in VALID_ABLATIONS:
                raise ValueError(
                    f"Unknown ablation {self.ablation!r}; expected one of {VALID_ABLATIONS}"
                )
            if self.scaler != "cloudpulse":
                raise ValueError("Ablations apply only when scaler='cloudpulse'")
        if self.warmup_seconds < 0:
            raise ValueError(f"warmup_seconds must be >= 0, got {self.warmup_seconds!r}")
        if self.work_iterations < 0:
            raise ValueError(f"work_iterations must be >= 0, got {self.work_iterations!r}")
        if self.seed < 0:
            raise ValueError(f"seed must be >= 0, got {self.seed!r}")
        if self.repetition < 1:
            raise ValueError(f"repetition must be >= 1, got {self.repetition!r}")
        load_profile(self.profile)

    def default_run_id(self) -> str:
        prefix = f"{self.scaler}-{self.ablation}" if self.ablation else self.scaler
        return (
            f"{prefix}-{self.profile}-w{self.warmup_seconds}"
            f"-s{self.seed}-r{self.repetition}"
        )


@dataclass(frozen=True)
class RunnerPreflightResult:
    ok: bool
    missing_tools: tuple[str, ...]
    missing_artifacts: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentRunResult:
    run_id: str
    out_dir: Path
    manifest_path: Path
    artifacts: dict[str, str]


class ExperimentDriver(Protocol):
    def assert_clean(self) -> None: ...

    def configure_target(
        self, *, warmup_seconds: int, work_iterations: int, replicas: int
    ) -> None: ...

    def apply_hpa(self, yaml_path: Path) -> None: ...

    def delete_hpa(self, yaml_path: Path | None = None) -> None: ...

    def run_workload(
        self,
        *,
        spec: ExperimentSpec,
        run_id: str,
        out_dir: Path,
        limits: ArbiterLimits,
        rps_scale: float,
        duration_scale: float,
    ) -> dict[str, Path]: ...

    def sleep(self, seconds: float) -> None: ...


class SequentialRunnerLock:
    """Exclusive filesystem lock preventing overlapping benchmark runs (T7.1.2)."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._acquired = False

    def __enter__(self) -> SequentialRunnerLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.lock_path, flags)
        except FileExistsError as exc:
            raise RuntimeError(
                f"Sequential runner lock already active: {self.lock_path}. "
                "Wait for the running benchmark to finish or remove a stale lock."
            ) from exc
        try:
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        self._acquired = True
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if self._acquired:
            self.lock_path.unlink(missing_ok=True)
            self._acquired = False


def _default_cmd_runner(
    cmd: list[str], *, cwd: Path = PROJECT_ROOT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def find_competing_hpas(
    hpa_json_text: str, *, deployment: str = DEPLOYMENT
) -> tuple[list[str], str | None]:
    """Return `(matching_hpa_names, parse_error)` from `kubectl get hpa -o json`."""
    if not hpa_json_text.strip():
        return [], None
    try:
        data = json.loads(hpa_json_text)
    except json.JSONDecodeError as exc:
        return [], f"Could not parse HPA JSON output: {exc}"

    names: list[str] = []
    for item in data.get("items", []):
        ref = item.get("spec", {}).get("scaleTargetRef", {})
        if ref.get("name") == deployment:
            names.append(str(item.get("metadata", {}).get("name", "<unknown>")))
    return names, None


def default_required_files_for_run(
    *,
    scaler: str,
    ablation: str | None = None,
    asymmetric_model_rel: str = "ml/models/forecaster_asymmetric.joblib",
) -> tuple[str, ...]:
    files = [
        "deploy/capacity.yaml",
        "deploy/limits.md",
        "services/arbiter/config.yaml",
        "load/k6_runner.js",
    ]
    if scaler in ("hpa-default", "hpa-tuned"):
        files.append(f"deploy/baselines/{scaler}.yaml")
    elif scaler == "cloudpulse":
        files.append("ml/models/forecaster_bundle.joblib")
        if ablation == "asymmetric-on":
            files.append(asymmetric_model_rel)
    return tuple(files)


def check_runner_prerequisites(
    root: Path = PROJECT_ROOT,
    *,
    scaler: str = "cloudpulse",
    ablation: str | None = None,
    which_fn: Callable[[str], str | None] = shutil.which,
    cmd_runner: Callable[..., subprocess.CompletedProcess[str]] = _default_cmd_runner,
    required_files: tuple[str, ...] | None = None,
) -> RunnerPreflightResult:
    """Validate CLI tools, artifacts, target deployment health, and zero HPAs."""
    missing_tools = tuple(t for t in ("kubectl", "k6") if which_fn(t) is None)
    req = (
        default_required_files_for_run(scaler=scaler, ablation=ablation)
        if required_files is None
        else required_files
    )
    missing_artifacts = tuple(rel for rel in req if not (root / rel).exists())

    errors: list[str] = []
    if not missing_tools:
        dep_check = cmd_runner(
            ["kubectl", "get", "deployment", DEPLOYMENT, "-n", NAMESPACE],
            cwd=root,
        )
        if dep_check.returncode != 0:
            errors.append(
                f"Kubernetes deployment {NAMESPACE}/{DEPLOYMENT} unavailable: "
                f"{(dep_check.stderr or dep_check.stdout).strip()}"
            )
        else:
            hpa_check = cmd_runner(
                ["kubectl", "get", "hpa", "-n", NAMESPACE, "-o", "json"],
                cwd=root,
            )
            if hpa_check.returncode != 0:
                errors.append(
                    f"Unable to verify HPA isolation in `{NAMESPACE}`: "
                    f"{(hpa_check.stderr or hpa_check.stdout).strip()}"
                )
            else:
                names, parse_err = find_competing_hpas(hpa_check.stdout)
                if parse_err:
                    errors.append(parse_err)
                for hpa_name in names:
                    errors.append(
                        f"Refusing experiment start: HPA `{hpa_name}` is attached to "
                        f"`{DEPLOYMENT}` in `{NAMESPACE}` (T7.1.2)."
                    )

    return RunnerPreflightResult(
        ok=not (missing_tools or missing_artifacts or errors),
        missing_tools=missing_tools,
        missing_artifacts=missing_artifacts,
        errors=tuple(errors),
    )


def read_git_info(root: Path = PROJECT_ROOT) -> tuple[str, bool]:
    """Return `(short_sha, dirty)` from git for Phase-0 manifest provenance."""
    sha_proc = _default_cmd_runner(["git", "rev-parse", "--short=7", "HEAD"], cwd=root)
    if sha_proc.returncode != 0 or not sha_proc.stdout.strip():
        raise RuntimeError(
            f"Could not determine git SHA in {root}: {sha_proc.stderr.strip()}"
        )
    status_proc = _default_cmd_runner(["git", "status", "--porcelain"], cwd=root)
    if status_proc.returncode != 0:
        raise RuntimeError(
            f"Could not inspect git status in {root}: {status_proc.stderr.strip()}"
        )
    return sha_proc.stdout.strip(), bool(status_proc.stdout.strip())


def build_run_manifest(
    spec: ExperimentSpec,
    *,
    run_id: str,
    git_sha: str,
    git_dirty: bool,
    start_time_utc: datetime,
    end_time_utc: datetime,
    limits: ArbiterLimits,
    slo_ms: float = SLO_MS,
    artifacts: dict[str, str] | None = None,
    notes: str | None = None,
    cluster_resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble and schema-validate a `manifest.json` payload (T0.3.1 / T7.1.1)."""
    res = dict(DEFAULT_CLUSTER_RESOURCES)
    if cluster_resources:
        res.update(cluster_resources)
    res["min_pods"] = int(limits.min_pods)
    res["max_pods"] = int(limits.max_pods)

    def _iso_z(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "git_sha": git_sha,
        "git_dirty": bool(git_dirty),
        "scaler": spec.scaler,
        "profile": spec.profile,
        "warmup_seconds": spec.warmup_seconds,
        "work_iterations": spec.work_iterations,
        "seed": spec.seed,
        "repetition": spec.repetition,
        "start_time_utc": _iso_z(start_time_utc),
        "end_time_utc": _iso_z(end_time_utc),
        "cluster_resources": res,
        "parameters": {
            "capacity_rps": float(limits.capacity_rps),
            "horizon_s": int(limits.horizon_s),
            "slo_ms": float(slo_ms),
            "ablation": spec.ablation,
        },
        "artifacts": dict(artifacts or {}),
    }
    if notes is not None:
        payload["notes"] = notes

    validate_manifest(payload)
    return payload


def spec_from_manifest(manifest_path: Path) -> ExperimentSpec:
    """Reconstruct an exact `ExperimentSpec` from a committed `manifest.json`."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(data)
    params = data.get("parameters") or {}
    return ExperimentSpec(
        scaler=str(data["scaler"]),
        profile=str(data["profile"]),
        warmup_seconds=int(data["warmup_seconds"]),
        work_iterations=int(data["work_iterations"]),
        seed=int(data["seed"]),
        repetition=int(data["repetition"]),
        ablation=params.get("ablation"),
    )


def build_benchmark_matrix(
    *,
    suite: str = "all",
    profiles: tuple[str, ...] = MAIN_PROFILES,
    sweep_profiles: tuple[str, ...] = ("flash",),
    warmups: tuple[int, ...] = WARMUP_SWEEP_SECONDS,
    default_warmup: int = 30,
    work_iterations: int = 15000,
    base_seed: int = DEFAULT_SEED,
    reps: int = 5,
) -> list[ExperimentSpec]:
    """Build deterministic sequential Phase 7 run matrices without duplicates."""
    valid_suites = ("baselines", "ablations", "warmup-sweep", "all")
    if suite not in valid_suites:
        raise ValueError(f"Unknown suite {suite!r}; expected one of {valid_suites}")
    if reps < 1:
        raise ValueError(f"reps must be >= 1, got {reps!r}")

    ordered: list[ExperimentSpec] = []
    seen_ids: set[str] = set()

    def _add(spec: ExperimentSpec) -> None:
        rid = spec.default_run_id()
        if rid not in seen_ids:
            seen_ids.add(rid)
            ordered.append(spec)

    if suite in ("baselines", "all"):
        for scaler in VALID_SCALERS:
            for profile in profiles:
                for rep in range(1, reps + 1):
                    _add(
                        ExperimentSpec(
                            scaler=scaler,
                            profile=profile,
                            warmup_seconds=default_warmup,
                            work_iterations=work_iterations,
                            seed=base_seed + rep - 1,
                            repetition=rep,
                        )
                    )

    if suite in ("ablations", "all"):
        for ablation in VALID_ABLATIONS:
            for profile in profiles:
                for rep in range(1, reps + 1):
                    _add(
                        ExperimentSpec(
                            scaler="cloudpulse",
                            profile=profile,
                            warmup_seconds=default_warmup,
                            work_iterations=work_iterations,
                            seed=base_seed + rep - 1,
                            repetition=rep,
                            ablation=ablation,
                        )
                    )

    if suite in ("warmup-sweep", "all"):
        for scaler in WARMUP_SWEEP_SCALERS:
            for warmup in warmups:
                for profile in sweep_profiles:
                    for rep in range(1, reps + 1):
                        _add(
                            ExperimentSpec(
                                scaler=scaler,
                                profile=profile,
                                warmup_seconds=warmup,
                                work_iterations=work_iterations,
                                seed=base_seed + rep - 1,
                                repetition=rep,
                            )
                        )

    return ordered


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


def _kubectl_checked(cmd: list[str], *, cwd: Path = PROJECT_ROOT) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {detail}")


class KubernetesClusterDriver:
    """Live cluster driver executing k6 + collector + arbiter/HPA sequentially."""

    def __init__(
        self,
        *,
        root: Path = PROJECT_ROOT,
        scaler: str = "cloudpulse",
        ablation: str | None = None,
        pg_dsn: str | None = None,
        asymmetric_model_path: Path | None = None,
        dry_run_actuation: bool = False,
    ) -> None:
        self.root = root
        self.scaler = scaler
        self.ablation = ablation
        self.pg_dsn = pg_dsn
        self.asymmetric_model_path = asymmetric_model_path or (
            root / "ml" / "models" / "forecaster_asymmetric.joblib"
        )
        self.dry_run_actuation = dry_run_actuation

    def assert_clean(self) -> None:
        pre = check_runner_prerequisites(
            self.root,
            scaler=self.scaler,
            ablation=self.ablation,
            required_files=default_required_files_for_run(
                scaler=self.scaler,
                ablation=self.ablation,
                asymmetric_model_rel=self.asymmetric_model_path.relative_to(self.root).as_posix()
                if self.asymmetric_model_path.is_relative_to(self.root)
                else str(self.asymmetric_model_path),
            ),
        )
        if not pre.ok:
            details: list[str] = []
            if pre.missing_tools:
                details.append(f"missing tools: {', '.join(pre.missing_tools)}")
            if pre.missing_artifacts:
                details.append(f"missing artifacts: {', '.join(pre.missing_artifacts)}")
            details.extend(pre.errors)
            raise RuntimeError("Preflight failed: " + "; ".join(details))

    def configure_target(
        self, *, warmup_seconds: int, work_iterations: int, replicas: int
    ) -> None:
        patch = {
            "spec": {
                "replicas": replicas,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": CONTAINER,
                                "env": [
                                    {
                                        "name": "WARMUP_SECONDS",
                                        "value": str(warmup_seconds),
                                    },
                                    {
                                        "name": "WORK_ITERATIONS",
                                        "value": str(work_iterations),
                                    },
                                ],
                            }
                        ]
                    }
                },
            }
        }
        _kubectl_checked(
            [
                "kubectl",
                "patch",
                "deployment",
                DEPLOYMENT,
                "-n",
                NAMESPACE,
                "--type=strategic",
                "-p",
                json.dumps(patch),
            ],
            cwd=self.root,
        )
        _kubectl_checked(
            [
                "kubectl",
                "rollout",
                "status",
                f"deployment/{DEPLOYMENT}",
                "-n",
                NAMESPACE,
                "--timeout=180s",
            ],
            cwd=self.root,
        )

    def apply_hpa(self, yaml_path: Path) -> None:
        _kubectl_checked(["kubectl", "apply", "-f", str(yaml_path)], cwd=self.root)

    def delete_hpa(self, yaml_path: Path | None = None) -> None:
        if yaml_path is not None:
            subprocess.run(
                ["kubectl", "delete", "-f", str(yaml_path), "--ignore-not-found"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
            )
            return
        subprocess.run(
            ["kubectl", "delete", "hpa", "--all", "-n", NAMESPACE, "--ignore-not-found"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def run_workload(
        self,
        *,
        spec: ExperimentSpec,
        run_id: str,
        out_dir: Path,
        limits: ArbiterLimits,
        rps_scale: float,
        duration_scale: float,
    ) -> dict[str, Path]:
        from prometheus_api_client import PrometheusConnect

        from services.arbiter.actuator import KubernetesScaleActuator
        from services.arbiter.decision_log import DecisionLogger
        from services.arbiter.loop import ArbiterLoop, PrometheusTelemetrySource
        from services.collector.collector import (
            align_epoch_to_step,
            export_parquet,
            reconcile_time_grid,
            scrape_once,
            upsert_dataframe_postgres,
        )

        profile_data = load_profile(spec.profile)
        summary = validate_profile(profile_data)
        k6_duration_s = max(
            1, int(round(float(summary["total_duration_s"]) * duration_scale))
        )

        k6_summary_path = out_dir / "k6_summary.json"
        telemetry_path = out_dir / "telemetry.parquet"
        decisions_path = out_dir / "decisions.jsonl"

        prom_port = find_free_port()
        app_port = find_free_port()
        fc_port = find_free_port()

        procs: list[subprocess.Popen[Any]] = []
        stop_event = threading.Event()
        collector_errors: list[Exception] = []
        arbiter_errors: list[Exception] = []
        collected_rows: list[Any] = []

        try:
            procs.append(
                subprocess.Popen(
                    [
                        "kubectl",
                        "port-forward",
                        "-n",
                        MONITORING_NAMESPACE,
                        "svc/prometheus",
                        f"{prom_port}:9090",
                    ],
                    cwd=self.root,
                )
            )
            procs.append(
                subprocess.Popen(
                    [
                        "kubectl",
                        "port-forward",
                        "-n",
                        NAMESPACE,
                        f"svc/{DEPLOYMENT}",
                        f"{app_port}:8000",
                    ],
                    cwd=self.root,
                )
            )
            if not wait_for_http(f"http://127.0.0.1:{prom_port}/-/ready", timeout_s=30.0):
                raise RuntimeError("Prometheus port-forward did not become ready")
            if not wait_for_http(f"http://127.0.0.1:{app_port}/healthz", timeout_s=30.0):
                raise RuntimeError("target-app port-forward did not become ready")

            prom_url = f"http://127.0.0.1:{prom_port}"
            if spec.scaler == "cloudpulse":
                env = os.environ.copy()
                if spec.ablation == "asymmetric-on":
                    env["MODEL_PATH"] = str(self.asymmetric_model_path)
                else:
                    env["MODEL_PATH"] = str(
                        self.root / "ml" / "models" / "forecaster_bundle.joblib"
                    )
                procs.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "services.forecaster.app:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(fc_port),
                            "--workers",
                            "1",
                        ],
                        cwd=self.root,
                        env=env,
                    )
                )
                if not wait_for_http(f"http://127.0.0.1:{fc_port}/healthz", timeout_s=30.0):
                    raise RuntimeError("Forecaster service failed to become ready")

            prom_client = PrometheusConnect(url=prom_url, disable_ssl=True)
            actuator = KubernetesScaleActuator()

            def _collect_worker() -> None:
                try:
                    tick = align_epoch_to_step(time.time(), step_s=SCRAPE_INTERVAL_S)
                    while not stop_event.is_set():
                        sleep_s = tick.timestamp() - time.time()
                        if sleep_s > 0:
                            if stop_event.wait(sleep_s):
                                break
                        try:
                            row = scrape_once(
                                prom_client,
                                lambda: actuator.read_replica_state().ready_replicas,
                                ts=tick,
                                profile=spec.profile,
                                run_id=run_id,
                            )
                            collected_rows.append(row)
                        except Exception as exc:
                            collector_errors.append(exc)
                        tick = align_epoch_to_step(
                            tick.timestamp() + SCRAPE_INTERVAL_S,
                            step_s=SCRAPE_INTERVAL_S,
                        )
                except Exception as exc:
                    collector_errors.append(exc)

            collect_thread = threading.Thread(
                target=_collect_worker,
                name=f"collector-{run_id}",
                daemon=True,
            )
            collect_thread.start()

            run_start_utc = datetime.now(timezone.utc)
            forecaster_client = build_forecaster_for_experiment(
                scaler=spec.scaler,
                profile=spec.profile,
                limits=limits,
                run_start_utc=run_start_utc,
                forecaster_url=f"http://127.0.0.1:{fc_port}",
                ablation=spec.ablation,
                rps_scale=rps_scale,
                duration_scale=duration_scale,
            )

            arb_thread: threading.Thread | None = None
            if forecaster_client is not None:
                telemetry_src = PrometheusTelemetrySource(prom_url)
                dlogger = DecisionLogger(decisions_path, run_id=run_id, pg_dsn=self.pg_dsn)
                arb_loop = ArbiterLoop(
                    limits=limits,
                    telemetry=telemetry_src,
                    forecaster=forecaster_client,
                    actuator=actuator,
                    decision_logger=dlogger,
                    dry_run=self.dry_run_actuation,
                )

                def _arbiter_worker() -> None:
                    try:
                        while not stop_event.is_set():
                            t0 = time.monotonic()
                            arb_loop.step()
                            rem = DECISION_INTERVAL_S - (time.monotonic() - t0)
                            if rem > 0 and stop_event.wait(rem):
                                break
                    except Exception as exc:
                        arbiter_errors.append(exc)
                    finally:
                        forecaster_client.close()

                arb_thread = threading.Thread(
                    target=_arbiter_worker,
                    name=f"arbiter-{run_id}",
                    daemon=True,
                )
                arb_thread.start()

            k6_cmd = [
                "k6",
                "run",
                "-e",
                f"TARGET_URL=http://127.0.0.1:{app_port}",
                "-e",
                f"PROFILE={spec.profile}",
                "-e",
                f"RPS_SCALE={rps_scale}",
                "-e",
                f"DURATION_SCALE={duration_scale}",
                "-e",
                f"K6_SUMMARY_OUT={k6_summary_path.as_posix()}",
                "load/k6_runner.js",
            ]
            k6_proc = subprocess.run(k6_cmd, cwd=self.root, check=False)
            stop_event.set()
            collect_thread.join(timeout=10.0)
            if arb_thread is not None:
                arb_thread.join(timeout=10.0)

            if k6_proc.returncode != 0:
                raise RuntimeError(
                    f"k6 workload failed with exit code {k6_proc.returncode} (run_id={run_id})"
                )
            if arbiter_errors:
                raise RuntimeError(
                    f"Arbiter worker error during run {run_id}: {arbiter_errors[0]}"
                )

            parse_k6_summary(k6_summary_path)
            if collected_rows:
                df = reconcile_time_grid(collected_rows, step_s=SCRAPE_INTERVAL_S)
                if not df.empty:
                    if self.pg_dsn:
                        upsert_dataframe_postgres(df, self.pg_dsn)
                    export_parquet(df, telemetry_path)

            artifacts: dict[str, Path] = {"k6_summary": k6_summary_path}
            if telemetry_path.exists():
                artifacts["telemetry"] = telemetry_path
            if decisions_path.exists():
                artifacts["decisions"] = decisions_path
            elif forecaster_client is not None:
                raise RuntimeError(
                    f"Expected decisions log at {decisions_path} for {spec.scaler}"
                )
            if collector_errors and not telemetry_path.exists() and k6_duration_s >= 5:
                raise RuntimeError(
                    f"Telemetry collection failed for {run_id}: {collector_errors[0]}"
                )
            return artifacts
        finally:
            stop_event.set()
            _terminate_processes(procs)


def run_experiment(
    spec: ExperimentSpec,
    *,
    run_id: str | None = None,
    results_root: Path | None = None,
    limits: ArbiterLimits | None = None,
    driver: ExperimentDriver | None = None,
    baselines_dir: Path | None = None,
    settle_seconds: float = 30.0,
    rps_scale: float = 1.0,
    duration_scale: float = 1.0,
    git_info: tuple[str, bool] | None = None,
    clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    pg_dsn: str | None = None,
) -> ExperimentRunResult:
    """Execute one sequential benchmark run and emit a validated Phase-0 manifest."""
    if settle_seconds < 0.0:
        raise ValueError(f"settle_seconds must be >= 0, got {settle_seconds!r}")
    if rps_scale <= 0.0 or duration_scale <= 0.0:
        raise ValueError("rps_scale and duration_scale must be > 0")

    root = PROJECT_ROOT
    resolved_results = results_root or (root / "experiments" / "results")
    resolved_baselines = baselines_dir or (root / "deploy" / "baselines")
    resolved_limits = limits or load_arbiter_limits()
    resolved_run_id = run_id or spec.default_run_id()
    active_driver: ExperimentDriver = driver or KubernetesClusterDriver(
        root=root,
        scaler=spec.scaler,
        ablation=spec.ablation,
        pg_dsn=pg_dsn,
    )

    with SequentialRunnerLock(resolved_results / ".runner.lock"):
        active_driver.assert_clean()
        set_global_seed(spec.seed)
        profile_info = validate_profile(load_profile(spec.profile, profiles_dir=PROFILES_DIR))
        git_sha, git_dirty = git_info if git_info is not None else read_git_info(root)

        initial_replicas = (
            compute_static_replicas(spec.profile, limits=resolved_limits, rps_scale=rps_scale)
            if spec.scaler == "static"
            else resolved_limits.min_pods
        )
        hpa_yaml = (
            resolved_baselines / f"{spec.scaler}.yaml"
            if spec.scaler in ("hpa-default", "hpa-tuned")
            else None
        )

        out_dir = resolved_results / resolved_run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            active_driver.configure_target(
                warmup_seconds=spec.warmup_seconds,
                work_iterations=spec.work_iterations,
                replicas=initial_replicas,
            )
            if hpa_yaml is not None:
                active_driver.apply_hpa(hpa_yaml)

            t_start = clock_fn().astimezone(timezone.utc)
            raw_artifacts = active_driver.run_workload(
                spec=spec,
                run_id=resolved_run_id,
                out_dir=out_dir,
                limits=resolved_limits,
                rps_scale=rps_scale,
                duration_scale=duration_scale,
            )
            t_end = clock_fn().astimezone(timezone.utc)
            if t_end < t_start:
                t_end = t_start

            rel_artifacts: dict[str, str] = {}
            for key, path in raw_artifacts.items():
                rel_artifacts[key] = (
                    path.relative_to(out_dir).as_posix()
                    if path.is_relative_to(out_dir)
                    else path.name
                )

            manifest = build_run_manifest(
                spec,
                run_id=resolved_run_id,
                git_sha=git_sha,
                git_dirty=git_dirty,
                start_time_utc=t_start,
                end_time_utc=t_end,
                limits=resolved_limits,
                slo_ms=float(profile_info["slo_ms"]),
                artifacts=rel_artifacts,
            )
            manifest_path = out_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            return ExperimentRunResult(
                run_id=resolved_run_id,
                out_dir=out_dir,
                manifest_path=manifest_path,
                artifacts=rel_artifacts,
            )
        finally:
            if hpa_yaml is not None:
                active_driver.delete_hpa(hpa_yaml)
            active_driver.configure_target(
                warmup_seconds=0,
                work_iterations=spec.work_iterations,
                replicas=resolved_limits.min_pods,
            )
            if settle_seconds > 0.0:
                active_driver.sleep(settle_seconds)


def _format_plan(specs: list[ExperimentSpec]) -> str:
    lines = [f"{len(specs)} runs planned (sequential):"]
    for idx, spec in enumerate(specs, start=1):
        abl = f" ablation={spec.ablation}" if spec.ablation else ""
        lines.append(
            f"  [{idx:03d}] {spec.default_run_id()} "
            f"(scaler={spec.scaler}{abl}, profile={spec.profile}, "
            f"warmup={spec.warmup_seconds}s, seed={spec.seed}, rep={spec.repetition})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="CloudPulse Phase 7 sequential experiment runner (T7.1)."
    )
    ap.add_argument(
        "--suite",
        choices=("single", "baselines", "ablations", "warmup-sweep", "all"),
        default="single",
    )
    ap.add_argument("--scaler", choices=VALID_SCALERS, default="cloudpulse")
    ap.add_argument("--profile", choices=MAIN_PROFILES, default="flash")
    ap.add_argument("--warmup", type=int, default=30)
    ap.add_argument("--work-iterations", type=int, default=15000)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--ablation", choices=VALID_ABLATIONS, default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--from-manifest", type=Path, default=None)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--settle-seconds", type=float, default=30.0)
    ap.add_argument("--rps-scale", type=float, default=1.0)
    ap.add_argument("--duration-scale", type=float, default=1.0)
    ap.add_argument("--pg-dsn", default=None)
    ap.add_argument(
        "--results-root",
        type=Path,
        default=PROJECT_ROOT / "experiments" / "results",
    )
    ap.add_argument(
        "--plan",
        action="store_true",
        help="Print the sequential execution plan without mutating cluster or results.",
    )
    args = ap.parse_args(argv)

    if args.from_manifest is not None:
        specs = [spec_from_manifest(args.from_manifest)]
    elif args.suite == "single":
        specs = [
            ExperimentSpec(
                scaler=args.scaler,
                profile=args.profile,
                warmup_seconds=args.warmup,
                work_iterations=args.work_iterations,
                seed=args.seed,
                repetition=args.rep,
                ablation=args.ablation,
            )
        ]
    else:
        specs = build_benchmark_matrix(
            suite=args.suite,
            default_warmup=args.warmup,
            work_iterations=args.work_iterations,
            base_seed=args.seed,
            reps=args.reps,
        )

    if args.plan:
        print(_format_plan(specs))
        return 0

    for spec in specs:
        explicit_id = args.run_id if len(specs) == 1 else None
        try:
            res = run_experiment(
                spec,
                run_id=explicit_id,
                results_root=args.results_root,
                settle_seconds=args.settle_seconds,
                rps_scale=args.rps_scale,
                duration_scale=args.duration_scale,
                pg_dsn=args.pg_dsn,
            )
        except Exception as exc:
            print(f"ERROR: Experiment failed ({spec.default_run_id()}): {exc}", file=sys.stderr)
            return 2
        print(f"COMPLETED {res.run_id} -> {res.manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
