"""Unit tests for chronological split and >= H leakage guard (Phase 3, T3.3.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from ml.prepare_dataset import (
    assert_no_temporal_leakage,
    chronological_split_with_gap,
    prepare_from_raw,
)


def _make_raw_runs(n_runs: int = 3, rows_per_run: int = 180) -> pd.DataFrame:
    t_base = datetime(2026, 11, 27, 8, 0, 0, tzinfo=timezone.utc)
    frames = []
    for r in range(n_runs):
        start = t_base + timedelta(minutes=25 * r)
        ts = [start + timedelta(seconds=5 * i) for i in range(rows_per_run)]
        rps = [20.0 + 5.0 * (i % 12) + r for i in range(rows_per_run)]
        frames.append(
            pd.DataFrame(
                {
                    "ts": ts,
                    "rps": rps,
                    "cpu_millicores_total": [x * 13.0 for x in rps],
                    "mem_bytes": 100_000_000.0,
                    "p50": 8.0,
                    "p90": 14.0,
                    "p99": 32.0,
                    "ready_replicas": 3,
                    "profile": ("gradual", "flash", "diurnal")[r % 3],
                    "run_id": f"run-{r}",
                    "is_imputed": False,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_chronological_split_enforces_horizon_gaps(tmp_path: Path) -> None:
    horizon_s = 70
    raw_dir = tmp_path / "raw"
    out_dir = tmp_path / "processed"
    raw_dir.mkdir()
    _make_raw_runs(n_runs=6, rows_per_run=180).to_parquet(
        raw_dir / "telemetry.parquet", index=False
    )

    train_df, cal_df, test_df = prepare_from_raw(
        raw_dir, out_dir, horizon_s=horizon_s, step_s=5
    )

    gap = pd.Timedelta(seconds=horizon_s)
    assert train_df["ts"].max() + gap <= cal_df["ts"].min()
    assert cal_df["ts"].max() + gap <= test_df["ts"].min()
    assert (out_dir / "train.parquet").exists()
    assert (out_dir / "cal.parquet").exists()
    assert (out_dir / "test.parquet").exists()


def test_assert_no_temporal_leakage_catches_gap_violation() -> None:
    t0 = datetime(2026, 11, 27, 10, 0, 0, tzinfo=timezone.utc)
    train_df = pd.DataFrame({"ts": [t0, t0 + timedelta(seconds=100)]})
    cal_df = pd.DataFrame({"ts": [t0 + timedelta(seconds=150)]})  # only 50s gap < 70s
    test_df = pd.DataFrame({"ts": [t0 + timedelta(seconds=300)]})

    with pytest.raises(AssertionError, match="Temporal leakage between train and cal"):
        assert_no_temporal_leakage(train_df, cal_df, test_df, horizon_s=70)


def test_chronological_split_rejects_too_short_timespan() -> None:
    t0 = datetime(2026, 11, 27, 10, 0, 0, tzinfo=timezone.utc)
    short = pd.DataFrame(
        {"ts": [t0 + timedelta(seconds=5 * i) for i in range(10)], "y_target": 10.0}
    )
    with pytest.raises(ValueError, match="too short"):
        chronological_split_with_gap(short, horizon_s=70)
