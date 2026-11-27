#!/usr/bin/env python
"""Phase 3, T3.3.2 — chronological train / calibration / test dataset builder.

Reads raw 5 s Parquet telemetry under `ml/data/raw/`, builds causal features via
`cloudpulse_core.features`, and writes chronological splits with a strict
inter-split gap of `>= H` seconds (`max(train.ts) + H <= min(cal.ts)` and
`max(cal.ts) + H <= min(test.ts)`) to prevent temporal leakage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import PROJECT_ROOT, SCRAPE_INTERVAL_S, load_arbiter_config
from cloudpulse_core.features import build_supervised_dataset


def chronological_split_with_gap(
    supervised_df: pd.DataFrame,
    horizon_s: int,
    *,
    train_frac: float = 0.60,
    cal_frac: float = 0.20,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split a supervised dataset chronologically with gap `>= horizon_s`."""
    if not (0.0 < train_frac < 1.0 and 0.0 < cal_frac < 1.0 and train_frac + cal_frac < 1.0):
        raise ValueError("train_frac and cal_frac must be positive and sum to < 1.0")
    if horizon_s <= 0:
        raise ValueError(f"horizon_s must be positive, got {horizon_s}")

    df = supervised_df.sort_values("ts").reset_index(drop=True)
    if df.empty:
        raise ValueError("Cannot split an empty supervised dataset")

    ts = pd.to_datetime(df["ts"], utc=True)
    t_min = ts.min()
    t_max = ts.max()
    total_span_s = (t_max - t_min).total_seconds()
    if total_span_s <= 2.5 * horizon_s:
        raise ValueError(
            f"Time span ({total_span_s:.1f}s) is too short to preserve two >= {horizon_s}s gaps"
        )

    # Locate candidate boundary timestamps from row quantiles, then enforce >= H gap.
    n = len(df)
    train_end_idx = int(n * train_frac) - 1
    if train_end_idx < 0:
        raise ValueError("Dataset too small for train split")
    train_cutoff = ts.iloc[train_end_idx]
    cal_start_min = train_cutoff + pd.Timedelta(seconds=horizon_s)

    after_train = df[ts >= cal_start_min]
    if len(after_train) < 2:
        raise ValueError("Insufficient data after train + H gap for calibration/test")

    cal_target_rows = max(1, int(n * cal_frac))
    if cal_target_rows >= len(after_train):
        raise ValueError("Insufficient data after calibration for test + H gap")

    cal_end_pos = cal_target_rows - 1
    cal_cutoff = pd.to_datetime(after_train["ts"], utc=True).iloc[cal_end_pos]
    test_start_min = cal_cutoff + pd.Timedelta(seconds=horizon_s)

    train_df = df[ts <= train_cutoff].reset_index(drop=True)
    cal_df = df[(ts >= cal_start_min) & (ts <= cal_cutoff)].reset_index(drop=True)
    test_df = df[ts >= test_start_min].reset_index(drop=True)

    if train_df.empty or cal_df.empty or test_df.empty:
        raise ValueError(
            f"One or more splits became empty after enforcing >= {horizon_s}s gaps: "
            f"train={len(train_df)}, cal={len(cal_df)}, test={len(test_df)}"
        )

    assert_no_temporal_leakage(train_df, cal_df, test_df, horizon_s=horizon_s)
    return train_df, cal_df, test_df


def assert_no_temporal_leakage(
    train_df: pd.DataFrame,
    cal_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    horizon_s: int,
) -> None:
    """Assert T3.3.2 invariant: `max(prev.ts) + H <= min(next.ts)`."""
    gap = pd.Timedelta(seconds=horizon_s)
    tr_max = pd.to_datetime(train_df["ts"], utc=True).max()
    cal_min = pd.to_datetime(cal_df["ts"], utc=True).min()
    cal_max = pd.to_datetime(cal_df["ts"], utc=True).max()
    te_min = pd.to_datetime(test_df["ts"], utc=True).min()

    if tr_max + gap > cal_min:
        raise AssertionError(
            f"Temporal leakage between train and cal: {tr_max} + {gap} > {cal_min}"
        )
    if cal_max + gap > te_min:
        raise AssertionError(
            f"Temporal leakage between cal and test: {cal_max} + {gap} > {te_min}"
        )


def prepare_from_raw(
    raw_dir: Path,
    out_dir: Path,
    *,
    horizon_s: int,
    step_s: int = SCRAPE_INTERVAL_S,
    train_frac: float = 0.60,
    cal_frac: float = 0.20,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read `raw_dir/*.parquet`, build features, and write processed splits."""
    parquet_files = sorted(raw_dir.glob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No raw Parquet files found in {raw_dir}")

    raw_df = pd.concat(
        [pd.read_parquet(p) for p in parquet_files], axis=0, ignore_index=True
    )
    supervised = build_supervised_dataset(raw_df, horizon_s=horizon_s, step_s=step_s)
    train_df, cal_df, test_df = chronological_split_with_gap(
        supervised,
        horizon_s=horizon_s,
        train_frac=train_frac,
        cal_frac=cal_frac,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    train_df.to_parquet(out_dir / "train.parquet", index=False)
    cal_df.to_parquet(out_dir / "cal.parquet", index=False)
    test_df.to_parquet(out_dir / "test.parquet", index=False)
    return train_df, cal_df, test_df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build chronological train/cal/test Parquet splits (Phase 3)."
    )
    ap.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "ml" / "data" / "raw")
    ap.add_argument(
        "--out-dir", type=Path, default=PROJECT_ROOT / "ml" / "data" / "processed"
    )
    ap.add_argument(
        "--horizon-s",
        type=int,
        default=None,
        help="Forecast horizon H in seconds (defaults to services/arbiter/config.yaml)",
    )
    ap.add_argument("--train-frac", type=float, default=0.60)
    ap.add_argument("--cal-frac", type=float, default=0.20)
    args = ap.parse_args(argv)

    horizon_s = (
        args.horizon_s
        if args.horizon_s is not None
        else int(load_arbiter_config()["forecast_horizon_s"])
    )
    train_df, cal_df, test_df = prepare_from_raw(
        args.raw_dir,
        args.out_dir,
        horizon_s=horizon_s,
        train_frac=args.train_frac,
        cal_frac=args.cal_frac,
    )
    print(
        f"Prepared splits (H={horizon_s}s): train={len(train_df)}, "
        f"cal={len(cal_df)}, test={len(test_df)} -> {args.out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
