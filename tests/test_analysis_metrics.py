"""Tests for Phase 7 Step 7.3 primary metrics analyzer (`analysis/metrics.py`)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from experiments.run import ExperimentSpec, build_run_manifest
from services.arbiter.capacity import ArbiterLimits


def _write_run_fixture(
    root: Path,
    spec: ExperimentSpec,
    *,
    limits: ArbiterLimits,
    sla_rate: float,
    p99_ms: float,
    telemetry_rows: list[dict[str, Any]],
    decisions: list[dict[str, Any]] | None = None,
) -> Path:
    run_id = spec.default_run_id()
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    t0 = telemetry_rows[0]["ts"]
    t1 = telemetry_rows[-1]["ts"]

    k6 = {
        "profile": spec.profile,
        "slo_ms": 100.0,
        "total_duration_s": int((t1 - t0).total_seconds()),
        "target_rps": 40.0,
        "achieved_rps": 39.8,
        "tracking_error_pct": -0.5,
        "dropped_iterations": 0,
        "sla_violation_rate": sla_rate,
        "latency_ms": {"p50": 9.0, "p90": 18.0, "p99": p99_ms},
    }
    (run_dir / "k6_summary.json").write_text(json.dumps(k6, indent=2), encoding="utf-8")

    df = pd.DataFrame(telemetry_rows)
    df.to_parquet(run_dir / "telemetry.parquet", index=False)

    artifacts = {"k6_summary": "k6_summary.json", "telemetry": "telemetry.parquet"}
    if decisions is not None:
        lines = [json.dumps(d) for d in decisions]
        (run_dir / "decisions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        artifacts["decisions"] = "decisions.jsonl"

    manifest = build_run_manifest(
        spec,
        run_id=run_id,
        git_sha="53a94ee",
        git_dirty=False,
        start_time_utc=t0,
        end_time_utc=t1,
        limits=limits,
        slo_ms=100.0,
        artifacts=artifacts,
    )
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return run_dir


def test_computes_all_six_primary_metrics_for_predictive_run(tmp_path: Path) -> None:
    from analysis.metrics import compute_run_metrics

    limits = ArbiterLimits(capacity_rps=20.0, horizon_s=15, min_pods=2, max_pods=15)
    spec = ExperimentSpec(
        scaler="cloudpulse",
        profile="flash",
        warmup_seconds=15,
        seed=42,
        repetition=1,
    )
    t0 = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)
    # 5s grid, 12 steps (0..55s): replicas = 2 at 0s,5s; 4 from 10s..55s
    # Trapezoidal pod-seconds:
    #   0->5s:  0.5*(2+2)*5 = 10
    #   5->10s: 0.5*(2+4)*5 = 15
    #   10->55s (9 gaps of 5s): 9 * 4 * 5 = 180 -> total = 205.0 pod-s
    rps_series = [25.0, 28.0, 30.0, 35.0, 38.0, 40.0, 60.0, 65.0, 95.0, 100.0, 102.0, 105.0]
    reps = [2, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4]
    telemetry_rows = [
        {
            "ts": t0 + timedelta(seconds=5 * i),
            "rps": rps_series[i],
            "cpu_millicores_total": 100.0,
            "mem_bytes": 64_000_000.0,
            "p50": 8.0,
            "p90": 15.0,
            "p99": 25.0,
            "ready_replicas": reps[i],
            "profile": "flash",
            "run_id": spec.default_run_id(),
            "is_imputed": False,
        }
        for i in range(len(rps_series))
    ]

    # Decisions at t=0s, t=20s, t=30s (horizon_s = 15s -> target times 15s, 35s, 45s):
    # - t=0s: upward scale 2->4 succeeds; ready_replicas hits 4 at t=10s -> 10.0s ready latency.
    #         forecast [30, 42] vs actual at t=15s (35.0) -> COVERED.
    # - t=20s: failed actuation patch 4->6 (actuation_success=False -> NOT counted as actuation);
    #          forecast [55, 70] vs actual at t=35s (65.0) -> COVERED.
    # - t=30s: hold at 4;
    #          forecast [75, 90] vs actual at t=45s (100.0) -> MISSED -> 2/3 = 66.6667% coverage.
    decisions = [
        {
            "run_id": spec.default_run_id(),
            "ts_utc": t0.isoformat(),
            "mode": "predictive",
            "from_replicas": 2,
            "to_replicas": 4,
            "predictive_replicas": 4,
            "reactive_replicas": 2,
            "upper_bound_rps": 42.0,
            "point_rps": 36.0,
            "lower_rps": 30.0,
            "capacity_rps": 20.0,
            "per_pod_cpu_pct": 30.0,
            "degraded": False,
            "actuated": True,
            "actuation_success": True,
            "reason": "predictive scale-up",
        },
        {
            "run_id": spec.default_run_id(),
            "ts_utc": (t0 + timedelta(seconds=20)).isoformat(),
            "mode": "predictive",
            "from_replicas": 4,
            "to_replicas": 6,
            "predictive_replicas": 6,
            "reactive_replicas": 4,
            "upper_bound_rps": 70.0,
            "point_rps": 62.0,
            "lower_rps": 55.0,
            "capacity_rps": 20.0,
            "per_pod_cpu_pct": 55.0,
            "degraded": False,
            "actuated": True,
            "actuation_success": False,
            "reason": "failed patch excluded",
        },
        {
            "run_id": spec.default_run_id(),
            "ts_utc": (t0 + timedelta(seconds=30)).isoformat(),
            "mode": "hold",
            "from_replicas": 4,
            "to_replicas": 4,
            "predictive_replicas": 4,
            "reactive_replicas": 4,
            "upper_bound_rps": 90.0,
            "point_rps": 82.0,
            "lower_rps": 75.0,
            "capacity_rps": 20.0,
            "per_pod_cpu_pct": 60.0,
            "degraded": False,
            "actuated": False,
            "actuation_success": None,
            "reason": "within deadband",
        },
    ]

    run_dir = _write_run_fixture(
        tmp_path,
        spec,
        limits=limits,
        sla_rate=0.025,
        p99_ms=48.2,
        telemetry_rows=telemetry_rows,
        decisions=decisions,
    )
    metrics = compute_run_metrics(run_dir)
    assert metrics["run_id"] == spec.default_run_id()
    assert metrics["sla_violation_pct"] == pytest.approx(2.5)
    assert metrics["p99_latency_ms"] == pytest.approx(48.2)
    assert metrics["pod_seconds"] == pytest.approx(205.0)
    assert metrics["actuation_count"] == 1
    assert metrics["mean_time_to_ready_capacity_s"] == pytest.approx(10.0)
    assert metrics["matured_forecasts"] == 3
    assert metrics["coverage_pct"] == pytest.approx(200.0 / 3.0, rel=1e-4)


def test_computes_metrics_for_hpa_and_static_without_decisions_log(tmp_path: Path) -> None:
    from analysis.metrics import compute_run_metrics

    limits = ArbiterLimits(capacity_rps=20.0, horizon_s=15, min_pods=2, max_pods=15)
    t0 = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)

    # Static at 8 pods, never under-capacity -> 0 actuations, no decisions log.
    static_spec = ExperimentSpec(scaler="static", profile="flash", warmup_seconds=30, seed=42, repetition=1)
    static_rows = [
        {
            "ts": t0 + timedelta(seconds=5 * i),
            "rps": 50.0,
            "cpu_millicores_total": 100.0,
            "mem_bytes": 64_000_000.0,
            "p50": 7.0,
            "p90": 12.0,
            "p99": 18.0,
            "ready_replicas": 8,
            "profile": "flash",
            "run_id": static_spec.default_run_id(),
            "is_imputed": False,
        }
        for i in range(4)
    ]
    static_dir = _write_run_fixture(
        tmp_path, static_spec, limits=limits, sla_rate=0.0, p99_ms=18.0, telemetry_rows=static_rows
    )
    static_m = compute_run_metrics(static_dir)
    assert static_m["actuation_count"] == 0
    assert static_m["pod_seconds"] == pytest.approx(8 * 15.0)
    assert static_m["coverage_pct"] is None
    assert static_m["mean_time_to_ready_capacity_s"] is None

    # HPA tuned: enters capacity deficit at t=5s (rps=65 > 2*20=40), still short at 3 pods (60 < 65),
    # recovers when 4th pod becomes Ready at t=20s (4*20=80 >= 65) -> 15.0s recovery.
    hpa_spec = ExperimentSpec(scaler="hpa-tuned", profile="flash", warmup_seconds=15, seed=42, repetition=1)
    hpa_rows = [
        {"ts": t0 + timedelta(seconds=0), "rps": 20.0, "ready_replicas": 2},
        {"ts": t0 + timedelta(seconds=5), "rps": 65.0, "ready_replicas": 2},
        {"ts": t0 + timedelta(seconds=10), "rps": 65.0, "ready_replicas": 2},
        {"ts": t0 + timedelta(seconds=15), "rps": 65.0, "ready_replicas": 3},
        {"ts": t0 + timedelta(seconds=20), "rps": 65.0, "ready_replicas": 4},
    ]
    for r in hpa_rows:
        r.update(
            {
                "cpu_millicores_total": 200.0,
                "mem_bytes": 64_000_000.0,
                "p50": 10.0,
                "p90": 30.0,
                "p99": 65.0,
                "profile": "flash",
                "run_id": hpa_spec.default_run_id(),
                "is_imputed": False,
            }
        )
    hpa_dir = _write_run_fixture(
        tmp_path, hpa_spec, limits=limits, sla_rate=0.04, p99_ms=112.0, telemetry_rows=hpa_rows
    )
    hpa_m = compute_run_metrics(hpa_dir)
    assert hpa_m["actuation_count"] == 2
    assert hpa_m["mean_time_to_ready_capacity_s"] == pytest.approx(15.0)
    assert hpa_m["coverage_pct"] is None


def test_analyse_results_dir_skips_non_benchmark_dirs_and_writes_csv(tmp_path: Path) -> None:
    from analysis.metrics import analyse_results_dir

    # Simulate partial/readiness dir without manifest.json
    (tmp_path / "readiness-20261128-0001").mkdir()

    limits = ArbiterLimits(capacity_rps=20.0, horizon_s=15, min_pods=2, max_pods=15)
    t0 = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)
    spec = ExperimentSpec(scaler="static", profile="gradual", warmup_seconds=30, seed=42, repetition=1)
    _write_run_fixture(
        tmp_path,
        spec,
        limits=limits,
        sla_rate=0.01,
        p99_ms=22.0,
        telemetry_rows=[
            {
                "ts": t0 + timedelta(seconds=5 * i),
                "rps": 30.0,
                "cpu_millicores_total": 100.0,
                "mem_bytes": 64_000_000.0,
                "p50": 8.0,
                "p90": 14.0,
                "p99": 22.0,
                "ready_replicas": 4,
                "profile": "gradual",
                "run_id": spec.default_run_id(),
                "is_imputed": False,
            }
            for i in range(3)
        ],
    )

    out_csv = tmp_path / "results.csv"
    df = analyse_results_dir(tmp_path, out_csv=out_csv)
    assert out_csv.exists()
    assert len(df) == 1
    assert list(df["run_id"]) == [spec.default_run_id()]
    assert not any("dollar" in col or "usd" in col for col in df.columns)


def test_cpctl_analyse_runs_cleanly_on_empty_dir(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    out_csv = tmp_path / "empty_results.csv"
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "cpctl.py"),
            "analyse",
            "--results-root",
            str(tmp_path),
            "--out",
            str(out_csv),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out_csv.exists()
    assert "0 benchmark runs" in proc.stdout
