"""Tests for Phase 7 Step 7.2 sequential experiment runner (`experiments/run.py`)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from experiments.manifest import validate_manifest
from experiments.scalers import VALID_ABLATIONS, VALID_SCALERS
from services.arbiter.capacity import ArbiterLimits


def _fake_k6_summary(profile: str) -> dict[str, Any]:
    return {
        "profile": profile,
        "slo_ms": 100.0,
        "total_duration_s": 900,
        "target_rps": 30.0,
        "achieved_rps": 29.9,
        "tracking_error_pct": 0.33,
        "dropped_iterations": 0,
        "sla_violation_rate": 0.012,
        "latency_ms": {"p50": 9.2, "p90": 18.4, "p99": 34.1},
    }


def _fake_telemetry_df(profile: str, run_id: str) -> pd.DataFrame:
    t0 = datetime(2026, 11, 28, 0, 0, 0, tzinfo=timezone.utc)
    return pd.DataFrame(
        [
            {
                "ts": t0 + timedelta(seconds=5 * i),
                "rps": 25.0 + i,
                "cpu_millicores_total": 120.0,
                "mem_bytes": 64_000_000.0,
                "p50": 8.0,
                "p90": 14.0,
                "p99": 22.0,
                "ready_replicas": 2,
                "profile": profile,
                "run_id": run_id,
                "is_imputed": False,
            }
            for i in range(6)
        ]
    )


def test_refuses_to_start_when_hpa_exists_on_target(tmp_path: Path) -> None:
    from experiments.run import check_runner_prerequisites

    def fake_runner(cmd: list[str], *, cwd: Path | None = None):
        del cwd
        if cmd[:3] == ["kubectl", "get", "deployment"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="target-app\n", stderr="")
        if cmd[:3] == ["kubectl", "get", "hpa"]:
            payload = {
                "items": [
                    {
                        "metadata": {"name": "target-app-hpa"},
                        "spec": {"scaleTargetRef": {"name": "target-app"}},
                    }
                ]
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        raise AssertionError(f"Unexpected cmd: {cmd}")

    res = check_runner_prerequisites(
        root=tmp_path,
        scaler="cloudpulse",
        which_fn=lambda tool: f"/usr/bin/{tool}",
        cmd_runner=fake_runner,
        required_files=(),
    )
    assert res.ok is False
    assert any("target-app-hpa" in err for err in res.errors)


def test_concurrent_runner_lock_blocks_overlapping_runs(tmp_path: Path) -> None:
    from experiments.run import SequentialRunnerLock

    lock_path = tmp_path / ".runner.lock"
    with SequentialRunnerLock(lock_path):
        assert lock_path.exists()
        with pytest.raises(RuntimeError, match="already active"):
            with SequentialRunnerLock(lock_path):
                pass
    assert not lock_path.exists()

    # Exception inside guarded block still releases the lock.
    with pytest.raises(ValueError, match="boom"):
        with SequentialRunnerLock(lock_path):
            raise ValueError("boom")
    assert not lock_path.exists()


def test_build_manifest_validates_and_roundtrips_reproduction_spec(tmp_path: Path) -> None:
    from experiments.run import (
        ExperimentSpec,
        build_run_manifest,
        spec_from_manifest,
    )

    spec = ExperimentSpec(
        scaler="cloudpulse",
        profile="flash",
        warmup_seconds=30,
        work_iterations=15000,
        seed=42,
        repetition=1,
        ablation="point-only",
    )
    t0 = datetime(2026, 11, 28, 0, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=15)
    limits = ArbiterLimits(capacity_rps=18.0, horizon_s=45, min_pods=2, max_pods=15)

    manifest = build_run_manifest(
        spec,
        run_id=spec.default_run_id(),
        git_sha="53a94ee",
        git_dirty=False,
        start_time_utc=t0,
        end_time_utc=t1,
        limits=limits,
        slo_ms=100.0,
        artifacts={"k6_summary": "k6_summary.json", "decisions": "decisions.jsonl"},
    )
    validate_manifest(manifest)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    loaded = spec_from_manifest(manifest_path)
    assert loaded == spec


def test_benchmark_matrix_planning_covers_t72_t73_t74_and_t75() -> None:
    from experiments.run import build_benchmark_matrix

    baselines = build_benchmark_matrix(suite="baselines")
    assert len(baselines) == len(VALID_SCALERS) * 3 * 5
    assert {s.scaler for s in baselines} == set(VALID_SCALERS)
    assert {s.profile for s in baselines} == {"gradual", "flash", "diurnal"}
    assert {s.repetition for s in baselines} == {1, 2, 3, 4, 5}
    assert len({s.default_run_id() for s in baselines}) == len(baselines)

    ablations = build_benchmark_matrix(suite="ablations")
    assert len(ablations) == len(VALID_ABLATIONS) * 3 * 5
    assert {s.ablation for s in ablations} == set(VALID_ABLATIONS)
    assert all(s.scaler == "cloudpulse" for s in ablations)

    sweep = build_benchmark_matrix(suite="warmup-sweep")
    assert len(sweep) == 2 * 5 * 5
    assert {s.scaler for s in sweep} == {"cloudpulse", "hpa-tuned"}
    assert {s.warmup_seconds for s in sweep} == {0, 5, 15, 30, 60}

    combined = build_benchmark_matrix(suite="all")
    ids = [s.default_run_id() for s in combined]
    assert len(ids) == len(set(ids))
    # w=30 flash cloudpulse/hpa-tuned reps overlap baselines and warmup-sweep and dedupe once.
    assert len(combined) < len(baselines) + len(ablations) + len(sweep)


class _RecordingDriver:
    def __init__(self, *, fail_k6: bool = False) -> None:
        self.events: list[tuple[str, Any]] = []
        self.hpa_applied = False
        self.fail_k6 = fail_k6
        self.slept: list[float] = []

    def assert_clean(self) -> None:
        if self.hpa_applied:
            raise RuntimeError("Competing HPA present on target-app")
        self.events.append(("assert_clean", None))

    def configure_target(
        self, *, warmup_seconds: int, work_iterations: int, replicas: int
    ) -> None:
        self.events.append(
            (
                "configure_target",
                {
                    "warmup_seconds": warmup_seconds,
                    "work_iterations": work_iterations,
                    "replicas": replicas,
                },
            )
        )

    def apply_hpa(self, yaml_path: Path) -> None:
        self.hpa_applied = True
        self.events.append(("apply_hpa", yaml_path.name))

    def delete_hpa(self, yaml_path: Path | None = None) -> None:
        self.hpa_applied = False
        self.events.append(("delete_hpa", yaml_path.name if yaml_path else None))

    def run_workload(
        self,
        *,
        spec: Any,
        run_id: str,
        out_dir: Path,
        limits: ArbiterLimits,
        rps_scale: float,
        duration_scale: float,
    ) -> dict[str, Path]:
        del rps_scale, duration_scale, limits
        self.events.append(("run_workload", spec.scaler))
        if self.fail_k6:
            raise RuntimeError("k6 exited with 99")

        k6_path = out_dir / "k6_summary.json"
        k6_path.write_text(
            json.dumps(_fake_k6_summary(spec.profile), indent=2), encoding="utf-8"
        )
        tel_path = out_dir / "telemetry.parquet"
        _fake_telemetry_df(spec.profile, run_id).to_parquet(tel_path, index=False)

        artifacts = {"k6_summary": k6_path, "telemetry": tel_path}
        if spec.scaler in ("cloudpulse", "naive-predictive", "oracle"):
            dec_path = out_dir / "decisions.jsonl"
            dec_path.write_text(
                json.dumps({"run_id": run_id, "mode": "predictive", "to_replicas": 4}) + "\n",
                encoding="utf-8",
            )
            artifacts["decisions"] = dec_path
        return artifacts

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def test_run_experiment_lifecycle_and_hpa_cleanup_on_failure(tmp_path: Path) -> None:
    from experiments.run import ExperimentSpec, run_experiment

    limits = ArbiterLimits(capacity_rps=18.0, horizon_s=45, min_pods=2, max_pods=15)

    # 1. Tuned HPA run applies hpa-tuned.yaml, runs k6, cleans up HPA, writes manifest.
    driver_hpa = _RecordingDriver()
    hpa_spec = ExperimentSpec(
        scaler="hpa-tuned",
        profile="flash",
        warmup_seconds=15,
        work_iterations=15000,
        seed=42,
        repetition=1,
    )
    result = run_experiment(
        hpa_spec,
        results_root=tmp_path,
        limits=limits,
        driver=driver_hpa,
        settle_seconds=12.0,
        git_info=("53a94ee", False),
    )
    assert result.manifest_path.exists()
    assert not driver_hpa.hpa_applied
    event_names = [name for name, _ in driver_hpa.events]
    assert event_names[0] == "assert_clean"
    assert ("apply_hpa", "hpa-tuned.yaml") in driver_hpa.events
    assert ("delete_hpa", "hpa-tuned.yaml") in driver_hpa.events
    assert driver_hpa.slept == [12.0]

    manifest_data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest_data)
    assert manifest_data["artifacts"] == {
        "k6_summary": "k6_summary.json",
        "telemetry": "telemetry.parquet",
    }

    # 2. Static baseline sizes replicas directly from compute_static_replicas (140 / 18 = 8).
    driver_static = _RecordingDriver()
    static_spec = ExperimentSpec(
        scaler="static",
        profile="flash",
        warmup_seconds=30,
        work_iterations=15000,
        seed=43,
        repetition=2,
    )
    run_experiment(
        static_spec,
        results_root=tmp_path,
        limits=limits,
        driver=driver_static,
        settle_seconds=0.0,
        git_info=("53a94ee", False),
    )
    initial_cfg = [p for name, p in driver_static.events if name == "configure_target"][0]
    assert initial_cfg["replicas"] == 8

    # 3. Failure during k6 MUST still delete HPA and release the sequential runner lock!
    driver_fail = _RecordingDriver(fail_k6=True)
    with pytest.raises(RuntimeError, match="k6 exited with 99"):
        run_experiment(
            hpa_spec,
            run_id="hpa-fail-cleanup-test",
            results_root=tmp_path,
            limits=limits,
            driver=driver_fail,
            settle_seconds=0.0,
            git_info=("53a94ee", False),
        )
    assert not driver_fail.hpa_applied
    assert not (tmp_path / ".runner.lock").exists()


def test_cpctl_bench_plan_runs_cleanly_offline(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "cpctl.py"),
            "bench",
            "--suite",
            "baselines",
            "--plan",
            "--results-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "90 runs planned" in out.stdout
    assert list(tmp_path.iterdir()) == []
