"""Shared causal feature engineering for CloudPulse (Phase 3, T3.3.1).

Single source of truth imported by BOTH `ml/` (training) and
`services/forecaster/` (serving) so train/serve skew cannot occur.

All workload features are computed strictly on `D(t) = rps` (offered load in
requests/sec — replica-independent per CLAUDE.md §3.2). Never pass or derive
features from average per-pod CPU utilisation percent.
"""

from __future__ import annotations

import math
from typing import Final

import numpy as np
import pandas as pd

from cloudpulse_core.config import SCRAPE_INTERVAL_S

# Step sizes assuming 5 s cadence (max lookback = 60 steps = 300 s = 5 min).
LAG_STEPS: Final[tuple[int, ...]] = (1, 2, 3, 6, 12, 24, 36, 60)
ROLL_WINDOWS: Final[tuple[int, ...]] = (6, 12, 36, 60)  # 30s, 60s, 180s, 300s
DIFF_STEPS: Final[tuple[int, ...]] = (1, 6, 12)  # 5s, 30s, 60s
SLOPE_WINDOWS: Final[tuple[int, ...]] = (6, 12)  # trailing OLS slope (RPS/s)

FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    "rps_now",
    *(f"rps_lag_{k}" for k in LAG_STEPS),
    *(f"rps_roll_mean_{w}" for w in ROLL_WINDOWS),
    *(f"rps_roll_std_{w}" for w in ROLL_WINDOWS),
    *(f"rps_diff_{k}" for k in DIFF_STEPS),
    *(f"rps_slope_{w}" for w in SLOPE_WINDOWS),
    "tod_sin",
    "tod_cos",
)

MIN_HISTORY_ROWS: Final[int] = max(LAG_STEPS + ROLL_WINDOWS + SLOPE_WINDOWS) + 1


def _trailing_ols_slope(series: pd.Series, window: int, step_s: int) -> pd.Series:
    """Strictly causal OLS slope (units: RPS per second) over `window` ticks."""
    x = np.arange(window, dtype=np.float64) * float(step_s)
    x_centered = x - x.mean()
    denom = float(np.dot(x_centered, x_centered))
    if denom <= 0:
        return pd.Series(np.nan, index=series.index, dtype=np.float64)

    return series.rolling(window=window, min_periods=window).apply(
        lambda vals: float(np.dot(x_centered, vals - np.mean(vals)) / denom),
        raw=True,
    )


def _compute_run_features(
    run_df: pd.DataFrame, *, step_s: int = SCRAPE_INTERVAL_S
) -> pd.DataFrame:
    df = run_df.sort_values("ts").copy()
    ts = pd.to_datetime(df["ts"], utc=True)

    # Validate uniform grid so lags/shifts mean exact elapsed seconds.
    if len(df) > 1:
        diffs = ts.diff().dropna().dt.total_seconds()
        if not np.allclose(diffs.to_numpy(), float(step_s), atol=1e-6):
            raise ValueError(
                f"Expected uniform {step_s}s timestamp spacing; found diffs={diffs.unique().tolist()[:5]}"
            )

    rps = df["rps"].astype(np.float64)
    out = pd.DataFrame(index=df.index)
    out["rps_now"] = rps

    for k in LAG_STEPS:
        out[f"rps_lag_{k}"] = rps.shift(k)

    for w in ROLL_WINDOWS:
        # Backward-only rolling window (center=False is mandatory for causality).
        roll = rps.rolling(window=w, min_periods=w, center=False)
        out[f"rps_roll_mean_{w}"] = roll.mean()
        out[f"rps_roll_std_{w}"] = roll.std(ddof=0)

    for k in DIFF_STEPS:
        out[f"rps_diff_{k}"] = rps - rps.shift(k)

    for w in SLOPE_WINDOWS:
        out[f"rps_slope_{w}"] = _trailing_ols_slope(rps, window=w, step_s=step_s)

    seconds_in_day = (
        ts.dt.hour * 3600.0 + ts.dt.minute * 60.0 + ts.dt.second.astype(np.float64)
    )
    angle = (2.0 * math.pi / 86400.0) * seconds_in_day
    out["tod_sin"] = np.sin(angle)
    out["tod_cos"] = np.cos(angle)
    out["ts"] = ts
    if "run_id" in df.columns:
        out["run_id"] = df["run_id"].values
    if "profile" in df.columns:
        out["profile"] = df["profile"].values
    return out


def build_features(
    df: pd.DataFrame,
    *,
    step_s: int = SCRAPE_INTERVAL_S,
    dropna: bool = True,
) -> pd.DataFrame:
    """Compute strictly causal workload features grouped per `run_id`."""
    if df.empty:
        return pd.DataFrame(columns=["ts", *FEATURE_COLUMNS])

    if "ts" not in df.columns or "rps" not in df.columns:
        raise ValueError("Input DataFrame must contain `ts` and `rps` columns")

    if "run_id" in df.columns:
        parts = [
            _compute_run_features(grp, step_s=step_s)
            for _, grp in df.groupby("run_id", sort=False)
        ]
        feat_df = pd.concat(parts, axis=0)
    else:
        feat_df = _compute_run_features(df, step_s=step_s)

    if dropna:
        feat_df = feat_df.dropna(subset=list(FEATURE_COLUMNS))
    return feat_df.sort_values("ts").reset_index(drop=True)


def build_supervised_dataset(
    df: pd.DataFrame,
    horizon_s: int,
    *,
    step_s: int = SCRAPE_INTERVAL_S,
) -> pd.DataFrame:
    """Build supervised `(features, y_target)` rows per `run_id` with zero cross-run leakage.

    Target `y_target` at row timestamp `ts` equals `D(ts + horizon_s)` from the
    same `run_id`. Trailing `horizon_steps` rows per run are dropped cleanly.
    """
    if horizon_s <= 0 or horizon_s % step_s != 0:
        raise ValueError(
            f"horizon_s ({horizon_s}) must be a positive multiple of step_s ({step_s})"
        )
    horizon_steps = horizon_s // step_s

    def _per_run(grp: pd.DataFrame) -> pd.DataFrame:
        feats = _compute_run_features(grp, step_s=step_s)
        sorted_grp = grp.sort_values("ts")
        feats["y_target"] = sorted_grp["rps"].astype(np.float64).shift(-horizon_steps)
        feats["target_ts"] = pd.to_datetime(sorted_grp["ts"], utc=True).shift(
            -horizon_steps
        )
        return feats

    if "run_id" in df.columns:
        combined = pd.concat(
            [_per_run(grp) for _, grp in df.groupby("run_id", sort=False)],
            axis=0,
        )
    else:
        combined = _per_run(df)

    combined = combined.dropna(subset=[*FEATURE_COLUMNS, "y_target", "target_ts"])
    return combined.sort_values("ts").reset_index(drop=True)


def extract_latest_features(
    window_df: pd.DataFrame,
    *,
    step_s: int = SCRAPE_INTERVAL_S,
) -> pd.DataFrame:
    """Return the single 1-row causal feature vector at the latest timestamp."""
    if len(window_df) < MIN_HISTORY_ROWS:
        raise ValueError(
            f"Need at least {MIN_HISTORY_ROWS} consecutive {step_s}s rows "
            f"({(MIN_HISTORY_ROWS - 1) * step_s}s history), got {len(window_df)}"
        )
    feat_df = build_features(window_df, step_s=step_s, dropna=True)
    if feat_df.empty:
        raise ValueError("Feature matrix empty after dropping warmup rows")
    return feat_df.iloc[[-1]][list(FEATURE_COLUMNS)].reset_index(drop=True)
