"""Phase 4, T4.1 — classical forecasting baselines (naive, seasonal naive, ARIMA)."""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA


def regression_metrics(
    y_true: np.ndarray | pd.Series, y_pred: np.ndarray | pd.Series
) -> dict[str, float]:
    """Compute RMSE, MAE, and SMAPE (%) consistently across models."""
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    if yt.shape != yp.shape or yt.size == 0:
        raise ValueError("y_true and y_pred must have matching non-empty shape")

    err = yp - yt
    rmse = float(np.sqrt(np.mean(err**2)))
    mae = float(np.mean(np.abs(err)))
    denom = np.abs(yt) + np.abs(yp)
    smape_terms = np.where(denom > 1e-12, 200.0 * np.abs(err) / denom, 0.0)
    smape = float(np.mean(smape_terms))
    return {
        "rmse": round(rmse, 4),
        "mae": round(mae, 4),
        "smape": round(smape, 4),
    }


def predict_naive(test_df: pd.DataFrame) -> np.ndarray:
    """Last-value baseline: `D_hat(t + H) = D(t)`."""
    return test_df["rps_now"].to_numpy(dtype=np.float64)


def predict_seasonal_naive(
    test_df: pd.DataFrame, *, lag_col: str = "rps_lag_60"
) -> np.ndarray:
    """Seasonal-naive baseline using trailing 5-minute cycle (`rps_lag_60`)."""
    col = lag_col if lag_col in test_df.columns else "rps_now"
    return test_df[col].to_numpy(dtype=np.float64)


def fit_and_predict_arima(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    horizon_steps: int,
    order: tuple[int, int, int] = (2, 1, 1),
) -> np.ndarray:
    """Fit ARIMA on training RPS and forecast `horizon_steps` ahead over test.

    Uses fixed fitted parameters applied sequentially (`apply()`) across test
    observations so multi-step forecasting uses strictly causal history without
    leaking future test points or re-estimating parameters per tick.
    """
    train_series = train_df["rps_now"].to_numpy(dtype=np.float64)
    test_series = test_df["rps_now"].to_numpy(dtype=np.float64)
    if train_series.size < 20:
        raise ValueError("Need >= 20 training points to fit ARIMA baseline")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitted = ARIMA(
            train_series,
            order=order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit()

        # Walk forward across test history in a single filtered state pass when
        # possible; fall back cleanly per observation if state filter fails.
        history = list(train_series[-min(len(train_series), 240) :])
        preds = np.empty(len(test_series), dtype=np.float64)
        for idx, obs in enumerate(test_series):
            history.append(float(obs))
            if len(history) > 240:
                history.pop(0)
            applied = fitted.apply(np.asarray(history, dtype=np.float64), refit=False)
            fc = applied.forecast(steps=horizon_steps)
            preds[idx] = max(0.0, float(fc[-1]))
    return preds


def evaluate_baselines(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    horizon_steps: int,
    arima_order: tuple[int, int, int] = (2, 1, 1),
) -> dict[str, dict[str, Any]]:
    """Run naive, seasonal naive, and ARIMA baselines on `test_df`."""
    y_test = test_df["y_target"].to_numpy(dtype=np.float64)
    preds = {
        "naive": predict_naive(test_df),
        "seasonal_naive": predict_seasonal_naive(test_df),
        "arima": fit_and_predict_arima(
            train_df, test_df, horizon_steps=horizon_steps, order=arima_order
        ),
    }
    return {
        name: {**regression_metrics(y_test, p), "predictions": p}
        for name, p in preds.items()
    }
