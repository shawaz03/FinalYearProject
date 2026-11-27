"""Unit tests for shared causal workload features (Phase 3, T3.3.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from cloudpulse_core.features import (
    FEATURE_COLUMNS,
    MIN_HISTORY_ROWS,
    build_features,
    build_supervised_dataset,
    extract_latest_features,
)


def _make_series(
    n_rows: int = 120,
    *,
    run_id: str = "r1",
    start: datetime | None = None,
    slope_per_sec: float = 0.5,
) -> pd.DataFrame:
    t0 = start or datetime(2026, 11, 27, 10, 0, 0, tzinfo=timezone.utc)
    ts = [t0 + timedelta(seconds=5 * i) for i in range(n_rows)]
    rps = [10.0 + slope_per_sec * (5.0 * i) for i in range(n_rows)]
    return pd.DataFrame(
        {
            "ts": ts,
            "rps": rps,
            "cpu_millicores_total": [r * 13.0 for r in rps],
            "ready_replicas": 3,
            "profile": "gradual",
            "run_id": run_id,
        }
    )


def test_build_features_exact_columns_slope_and_cyclical_time() -> None:
    df = _make_series(100, slope_per_sec=0.4)
    feats = build_features(df, step_s=5, dropna=True)
    # First 60 rows dropped because lag_60 / roll_60 require 60 prior points
    assert len(feats) == 40
    for col in FEATURE_COLUMNS:
        assert col in feats.columns
        assert not feats[col].isna().any()

    assert np.allclose(feats["rps_slope_6"], 0.4, atol=1e-6)
    assert np.allclose(feats["rps_slope_12"], 0.4, atol=1e-6)
    norm_sq = feats["tod_sin"] ** 2 + feats["tod_cos"] ** 2
    assert np.allclose(norm_sq, 1.0, atol=1e-9)


def test_build_features_is_strictly_causal() -> None:
    df1 = _make_series(90, slope_per_sec=0.2)
    df2 = df1.copy()
    # Spike future RPS at index >= 80; features for rows < 80 must not change at all.
    df2.loc[80:, "rps"] = 9999.0

    f1 = build_features(df1, step_s=5, dropna=True)
    f2 = build_features(df2, step_s=5, dropna=True)

    cutoff_ts = df1.loc[79, "ts"]
    left = f1[f1["ts"] <= cutoff_ts][list(FEATURE_COLUMNS)].reset_index(drop=True)
    right = f2[f2["ts"] <= cutoff_ts][list(FEATURE_COLUMNS)].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)


def test_build_supervised_dataset_never_leaks_across_run_boundaries() -> None:
    t0 = datetime(2026, 11, 27, 10, 0, 0, tzinfo=timezone.utc)
    r1 = _make_series(90, run_id="run-a", start=t0, slope_per_sec=0.0)
    r1["rps"] = 20.0
    # Second run starts 1 hour later at completely different load
    r2 = _make_series(90, run_id="run-b", start=t0 + timedelta(hours=1), slope_per_sec=0.0)
    r2["rps"] = 200.0
    combined = pd.concat([r1, r2], ignore_index=True)

    sup = build_supervised_dataset(combined, horizon_s=60, step_s=5)
    run_a_targets = sup[sup["run_id"] == "run-a"]["y_target"]
    run_b_targets = sup[sup["run_id"] == "run-b"]["y_target"]
    assert (run_a_targets == 20.0).all()
    assert (run_b_targets == 200.0).all()


def test_extract_latest_features_requires_minimum_history() -> None:
    short = _make_series(MIN_HISTORY_ROWS - 1)
    with pytest.raises(ValueError, match="Need at least"):
        extract_latest_features(short)

    exact = _make_series(MIN_HISTORY_ROWS)
    latest = extract_latest_features(exact)
    assert latest.shape == (1, len(FEATURE_COLUMNS))
    assert list(latest.columns) == list(FEATURE_COLUMNS)
