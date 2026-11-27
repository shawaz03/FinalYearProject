"""Phase 4, T4.2–T4.4 — deterministic quantile LightGBM, MAPIE CQR & asymmetry ablation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from mapie.regression import ConformalizedQuantileRegressor

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import PROJECT_ROOT, SCRAPE_INTERVAL_S, load_arbiter_config
from cloudpulse_core.features import FEATURE_COLUMNS
from cloudpulse_core.seeds import DEFAULT_SEED, lightgbm_determinism_params

PLAIN_QUANTILES: tuple[float, float, float] = (0.05, 0.95, 0.50)
# 3:1 under-prediction vs over-prediction asymmetry ablation (CLAUDE.md §3.4, T4.4):
# shifts median target toward 3/(3+1) = 0.75 and expands upper quantile to 0.975.
ASYMMETRIC_QUANTILES: tuple[float, float, float] = (0.05, 0.975, 0.75)

MODEL_VERSION = "cqr-aci-lgbm-1.0.0"


def fit_quantile_lgbm(
    X_train: pd.DataFrame,
    y_train: pd.Series | np.ndarray,
    *,
    tau: float,
    seed: int = DEFAULT_SEED,
    n_estimators: int = 120,
    learning_rate: float = 0.05,
    num_leaves: int = 31,
    min_child_samples: int = 10,
) -> lgb.LGBMRegressor:
    """Fit one deterministic LightGBM quantile regressor at quantile `tau`."""
    params = lightgbm_determinism_params(seed)
    model = lgb.LGBMRegressor(
        objective="quantile",
        alpha=float(tau),
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        num_leaves=num_leaves,
        min_child_samples=min_child_samples,
        verbosity=-1,
        **params,
    )
    model.fit(X_train, y_train)
    return model


def train_cqr_bundle(
    train_df: pd.DataFrame,
    cal_df: pd.DataFrame,
    *,
    horizon_s: int,
    step_s: int = SCRAPE_INTERVAL_S,
    seed: int = DEFAULT_SEED,
    variant: str = "plain_cqr",
    alpha: float = 0.10,
    aci_gamma: float = 0.05,
    n_estimators: int = 120,
) -> dict[str, Any]:
    """Fit quantile LightGBM base learners and conformalize via MAPIE 1.6 CQR.

    All fields inside the returned bundle are deterministic functions of the
    inputs and `seed` (no wall-clock timestamps inside the serialized artifact),
    so consecutive training runs produce byte-identical `.joblib` files (T0.2.3).
    """
    if variant == "plain_cqr":
        tau_lo, tau_hi, tau_med = PLAIN_QUANTILES
    elif variant == "asymmetric_3_1":
        tau_lo, tau_hi, tau_med = ASYMMETRIC_QUANTILES
    else:
        raise ValueError(f"Unknown variant {variant!r}; use 'plain_cqr' or 'asymmetric_3_1'")

    cols = list(FEATURE_COLUMNS)
    X_train = train_df[cols].to_numpy(dtype=np.float64)
    y_train = train_df["y_target"].to_numpy(dtype=np.float64)
    X_cal = cal_df[cols].to_numpy(dtype=np.float64)
    y_cal = cal_df["y_target"].to_numpy(dtype=np.float64)

    # MAPIE 1.6 prefit=True strictly expects [lower, upper, median] order.
    m_lo = fit_quantile_lgbm(X_train, y_train, tau=tau_lo, seed=seed, n_estimators=n_estimators)
    m_hi = fit_quantile_lgbm(X_train, y_train, tau=tau_hi, seed=seed, n_estimators=n_estimators)
    m_med = fit_quantile_lgbm(X_train, y_train, tau=tau_med, seed=seed, n_estimators=n_estimators)

    cqr = ConformalizedQuantileRegressor(
        estimator=[m_lo, m_hi, m_med],
        confidence_level=1.0 - alpha,
        prefit=True,
    )
    cqr.conformalize(X_cal, y_cal)

    return {
        "model_version": f"{MODEL_VERSION}-{variant}",
        "variant": variant,
        "quantiles": {"lower": tau_lo, "upper": tau_hi, "point": tau_med},
        "alpha": float(alpha),
        "aci_gamma": float(aci_gamma),
        "horizon_s": int(horizon_s),
        "step_s": int(step_s),
        "seed": int(seed),
        "feature_columns": cols,
        "q_lo_model": m_lo,
        "q_hi_model": m_hi,
        "q_med_model": m_med,
        "cqr": cqr,
        "calib_scores": np.asarray(cqr.conformity_scores[2], dtype=np.float64),
        "train_rows": int(len(train_df)),
        "cal_rows": int(len(cal_df)),
    }


def save_bundle(bundle: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path, compress=0)
    return path


def load_bundle(path: Path) -> dict[str, Any]:
    return joblib.load(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train deterministic CQR LightGBM bundle (T4.2).")
    ap.add_argument(
        "--processed-dir",
        type=Path,
        default=PROJECT_ROOT / "ml" / "data" / "processed",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "ml" / "models" / "forecaster_bundle.joblib",
    )
    ap.add_argument("--horizon-s", type=int, default=None)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument(
        "--variant",
        default="plain_cqr",
        choices=("plain_cqr", "asymmetric_3_1"),
    )
    ap.add_argument("--n-estimators", type=int, default=120)
    args = ap.parse_args(argv)

    horizon_s = (
        args.horizon_s
        if args.horizon_s is not None
        else int(load_arbiter_config()["forecast_horizon_s"])
    )
    train_df = pd.read_parquet(args.processed_dir / "train.parquet")
    cal_df = pd.read_parquet(args.processed_dir / "cal.parquet")

    bundle = train_cqr_bundle(
        train_df,
        cal_df,
        horizon_s=horizon_s,
        seed=args.seed,
        variant=args.variant,
        n_estimators=args.n_estimators,
    )
    save_bundle(bundle, args.out)

    meta_path = args.out.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "model_version": bundle["model_version"],
                "variant": bundle["variant"],
                "horizon_s": bundle["horizon_s"],
                "seed": bundle["seed"],
                "train_rows": bundle["train_rows"],
                "cal_rows": bundle["cal_rows"],
                "trained_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved {bundle['model_version']} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
