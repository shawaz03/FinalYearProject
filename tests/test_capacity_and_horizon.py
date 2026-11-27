"""Unit tests for Phase 2 saturation capacity C (T2.2) and horizon H (T2.3)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiments.measure_capacity import check_linearity, select_capacity_point
from scripts.derive_horizon import (
    build_arbiter_config,
    derive_horizon_seconds,
    round_up_to_step,
    summarize_readiness,
)

ROOT = Path(__file__).resolve().parent.parent


def test_select_capacity_point_respects_cpu_slo_and_tracking() -> None:
    steps = [
        {
            "achieved_rps": 8.0,
            "per_pod_cpu_pct": 42.0,
            "client_p99_ms": 22.0,
            "tracking_error_pct": 0.2,
            "dropped_iterations": 0,
        },
        {
            "achieved_rps": 13.8,
            "per_pod_cpu_pct": 68.5,
            "client_p99_ms": 48.0,
            "tracking_error_pct": -0.4,
            "dropped_iterations": 0,
        },
        {
            "achieved_rps": 16.0,
            "per_pod_cpu_pct": 79.0,  # exceeds 70% target
            "client_p99_ms": 62.0,
            "tracking_error_pct": 0.0,
            "dropped_iterations": 0,
        },
        {
            "achieved_rps": 18.0,
            "per_pod_cpu_pct": 69.0,
            "client_p99_ms": 135.0,  # violates 100 ms SLO
            "tracking_error_pct": 0.0,
            "dropped_iterations": 0,
        },
    ]
    chosen = select_capacity_point(steps, target_cpu_pct=70.0, slo_ms=100.0)
    assert chosen["achieved_rps"] == 13.8


def test_select_capacity_point_fails_loudly_if_no_step_healthy() -> None:
    bad = [
        {
            "achieved_rps": 20.0,
            "per_pod_cpu_pct": 85.0,
            "client_p99_ms": 150.0,
            "tracking_error_pct": 0.0,
            "dropped_iterations": 0,
        }
    ]
    with pytest.raises(ValueError, match="No step satisfied"):
        select_capacity_point(bad)


def test_check_linearity_within_and_outside_15pct() -> None:
    ok = check_linearity(c_single=14.0, c_multi=40.0, replicas=3)
    assert ok["within_tolerance"] is True
    assert ok["expected_multi_rps"] == 42.0

    bad = check_linearity(c_single=14.0, c_multi=30.0, replicas=3)
    assert bad["within_tolerance"] is False


def test_derive_horizon_rounds_up_to_5s_cadence() -> None:
    assert round_up_to_step(69.7, step_s=5) == 70
    out = derive_horizon_seconds(34.7, decision_interval_s=30.0, safety_margin_s=5.0)
    assert out["raw_horizon_s"] == 69.7
    assert out["horizon_s"] == 70
    assert out["horizon_steps"] == 14


def test_summarize_readiness_on_preliminary_samples() -> None:
    samples_path = (
        ROOT
        / "experiments"
        / "results"
        / "readiness-20261126-140654"
        / "samples.json"
    )
    payload = json.loads(samples_path.read_text(encoding="utf-8"))
    rows = summarize_readiness(payload)
    assert [r["warmup_seconds"] for r in rows] == [0, 5, 15, 30, 60]
    cfg, md = build_arbiter_config(
        run_id=payload["run_id"],
        reference_warmup_s=30,
        summary_rows=rows,
    )
    assert cfg["forecast_horizon_s"] == 70
    assert "T2.3.1" in cfg["justification"]
    assert "| 30 | 8 |" in md


def test_derive_horizon_cli_dry_run_succeeds() -> None:
    samples_path = (
        ROOT
        / "experiments"
        / "results"
        / "readiness-20261126-140654"
        / "samples.json"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "derive_horizon.py"),
            "--samples",
            str(samples_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "forecast_horizon_s: 70" in proc.stdout
    assert "| WARMUP_SECONDS |" in proc.stdout

