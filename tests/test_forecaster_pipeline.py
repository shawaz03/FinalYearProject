"""End-to-end Phase 4 ML pipeline tests on deterministic synthetic workloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ml.evaluate import run_full_evaluation
from ml.prepare_dataset import prepare_from_raw
from ml.train import save_bundle, train_cqr_bundle


def _synthetic_workload_df(n_runs: int = 9, rows_per_run: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    t_base = datetime(2026, 11, 27, 6, 0, 0, tzinfo=timezone.utc)
    frames = []
    for r in range(n_runs):
        start = t_base + timedelta(minutes=20 * r)
        ts = [start + timedelta(seconds=5 * i) for i in range(rows_per_run)]
        t_idx = np.arange(rows_per_run, dtype=np.float64)
        # Predictable smooth wave + deterministic flash pulse + minor noise
        wave = 45.0 + 25.0 * np.sin(2.0 * np.pi * t_idx / 72.0)
        spike = np.where((t_idx >= 90) & (t_idx < 115), 40.0 * np.sin(np.pi * (t_idx - 90) / 25.0), 0.0)
        rps = np.maximum(5.0, wave + spike + rng.normal(scale=1.5, size=rows_per_run))
        frames.append(
            pd.DataFrame(
                {
                    "ts": ts,
                    "rps": rps,
                    "cpu_millicores_total": rps * 13.0,
                    "mem_bytes": 120_000_000.0,
                    "p50": 8.0,
                    "p90": 14.0,
                    "p99": 32.0,
                    "ready_replicas": 3,
                    "profile": ("gradual", "flash", "diurnal")[r % 3],
                    "run_id": f"syn-run-{r}",
                    "is_imputed": False,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_training_produces_byte_identical_models(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    proc_dir = tmp_path / "processed"
    raw_dir.mkdir()
    _synthetic_workload_df(n_runs=6, rows_per_run=160).to_parquet(
        raw_dir / "syn.parquet", index=False
    )
    train_df, cal_df, _ = prepare_from_raw(raw_dir, proc_dir, horizon_s=30, step_s=5)

    b1 = train_cqr_bundle(train_df, cal_df, horizon_s=30, seed=42, n_estimators=40)
    b2 = train_cqr_bundle(train_df, cal_df, horizon_s=30, seed=42, n_estimators=40)

    p1 = save_bundle(b1, tmp_path / "m1.joblib")
    p2 = save_bundle(b2, tmp_path / "m2.joblib")
    assert p1.read_bytes() == p2.read_bytes()


def test_full_phase4_evaluation_pipeline(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    proc_dir = tmp_path / "processed"
    analysis_dir = tmp_path / "analysis"
    cfg_out = tmp_path / "forecaster_config.yaml"
    raw_dir.mkdir()
    _synthetic_workload_df(n_runs=10, rows_per_run=180).to_parquet(
        raw_dir / "syn.parquet", index=False
    )
    prepare_from_raw(raw_dir, proc_dir, horizon_s=30, step_s=5)

    summary = run_full_evaluation(
        processed_dir=proc_dir,
        analysis_dir=analysis_dir,
        forecaster_config_out=cfg_out,
        horizon_s=30,
        capacity_rps=15.0,
        seed=42,
        n_estimators=80,
    )

    assert summary["beats_baseline"] is True
    assert 0.85 <= summary["plain_marginal"]["coverage"] <= 0.98
    assert summary["plain_spike"]["n"] > 0
    assert (
        summary["drift"]["aci_post_shift_coverage"]
        > summary["drift"]["static_post_shift_coverage"]
    )
    assert summary["drift"]["recovery_steps"] is not None
    assert summary["drift"]["recovery_steps"] <= 20
    assert (analysis_dir / "baselines.md").exists()
    assert (analysis_dir / "coverage.md").exists()
    assert (analysis_dir / "figures" / "aci_drift_recovery.svg").exists()
    assert cfg_out.exists()
