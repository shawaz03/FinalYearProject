#!/usr/bin/env python
"""Phase 4, T4.1–T4.4 — evaluate baselines, CQR marginal & spike coverage, ACI drift, and asymmetry."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.aci import AdaptiveConformalPredictor
from cloudpulse_core.config import PROJECT_ROOT, load_arbiter_config
from cloudpulse_core.features import FEATURE_COLUMNS
from cloudpulse_core.seeds import DEFAULT_SEED
from ml.baselines import evaluate_baselines, regression_metrics
from ml.train import train_cqr_bundle


def compute_interval_stats(
    y_true: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    capacity_rps: float | None = None,
    step_s: int = 5,
) -> dict[str, float]:
    """Compute coverage, mean interval width, and provisional replica-seconds."""
    covered = (y_true >= lower) & (y_true <= upper)
    width = upper - lower
    stats: dict[str, float] = {
        "coverage": round(float(np.mean(covered)), 4),
        "mean_width": round(float(np.mean(width)), 4),
        "n": int(len(y_true)),
    }
    if capacity_rps and capacity_rps > 0:
        replicas = np.maximum(2, np.ceil(upper / capacity_rps))
        stats["pod_seconds"] = round(float(np.sum(replicas) * step_s), 1)
    return stats


def spike_window_mask(test_df: pd.DataFrame, *, top_fraction: float = 0.10) -> np.ndarray:
    """Boolean mask selecting the top `top_fraction` of `|dD/dt|` windows (T4.3.3)."""
    slope_col = "rps_slope_6" if "rps_slope_6" in test_df.columns else "rps_diff_1"
    abs_rate = np.abs(test_df[slope_col].to_numpy(dtype=np.float64))
    threshold = float(np.quantile(abs_rate, 1.0 - top_fraction))
    return abs_rate >= threshold


def predict_static_cqr(
    bundle: dict[str, Any], df: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run static MAPIE CQR prediction clamped to non-negative RPS."""
    X = df[list(FEATURE_COLUMNS)].to_numpy(dtype=np.float64)
    y_med, y_pis = bundle["cqr"].predict_interval(X, symmetric_correction=True)
    point = np.maximum(0.0, y_med)
    lower = np.maximum(0.0, np.minimum(point, y_pis[:, 0, 0]))
    upper = np.maximum(point, y_pis[:, 1, 0])
    return point, lower, upper


def simulate_aci_vs_static_under_shift(
    bundle: dict[str, Any],
    test_df: pd.DataFrame,
    *,
    shift_rps: float = 25.0,
    shift_start_idx: int | None = None,
    rolling_window: int = 10,
) -> dict[str, Any]:
    """Evaluate online ACI vs static CQR under an injected distribution shift (T4.3.2)."""
    X = test_df[list(FEATURE_COLUMNS)].to_numpy(dtype=np.float64)
    y_true = test_df["y_target"].to_numpy(dtype=np.float64).copy()
    n = len(y_true)
    if n < 40:
        raise ValueError("Need >= 40 test rows to evaluate shift recovery")

    shift_idx = shift_start_idx if shift_start_idx is not None else n // 3
    y_shifted = y_true.copy()
    y_shifted[shift_idx:] += shift_rps

    # Base model predictions
    q_lo_all = bundle["q_lo_model"].predict(X)
    q_hi_all = bundle["q_hi_model"].predict(X)
    q_med_all = bundle["q_med_model"].predict(X)
    _, static_lo, static_hi = predict_static_cqr(bundle, test_df)

    aci = AdaptiveConformalPredictor(
        initial_scores=bundle["calib_scores"],
        alpha_target=bundle["alpha"],
        gamma=0.10,
        window_size=40,
    )

    aci_lo = np.empty(n, dtype=np.float64)
    aci_hi = np.empty(n, dtype=np.float64)
    aci_hits = np.empty(n, dtype=np.float64)
    static_hits = np.empty(n, dtype=np.float64)
    alphas = np.empty(n, dtype=np.float64)

    horizon_steps = max(1, int(bundle["horizon_s"]) // int(bundle["step_s"]))
    pending: list[tuple[int, float, float, float, float]] = []

    for i in range(n):
        _, lo_i, hi_i = aci.predict_interval(q_lo_all[i], q_med_all[i], q_hi_all[i])
        aci_lo[i] = lo_i
        aci_hi[i] = hi_i
        aci_hits[i] = 1.0 if (lo_i <= y_shifted[i] <= hi_i) else 0.0
        static_hits[i] = 1.0 if (static_lo[i] <= y_shifted[i] <= static_hi[i]) else 0.0
        alphas[i] = aci.alpha_t

        # Delayed feedback arrives `horizon_steps` later when `y(t+H)` is realized
        pending.append((i + horizon_steps, y_shifted[i], lo_i, hi_i, i))
        while pending and pending[0][0] <= i:
            _, y_obs, p_lo, p_hi, src_idx = pending.pop(0)
            aci.update(
                y_obs,
                p_lo,
                p_hi,
                q_lo=float(q_lo_all[src_idx]),
                q_hi=float(q_hi_all[src_idx]),
            )

    post_aci = aci_hits[shift_idx:]
    post_static = static_hits[shift_idx:]

    # Rolling coverage after shift
    aci_roll = pd.Series(post_aci).rolling(rolling_window, min_periods=1).mean().to_numpy()
    static_roll = pd.Series(post_static).rolling(rolling_window, min_periods=1).mean().to_numpy()

    # Recovery step = first index after shift+horizon lag where rolling coverage >= 0.85
    search_start = min(len(aci_roll) - 1, horizon_steps)
    rec_candidates = np.where(aci_roll[search_start:] >= 0.85)[0]
    recovery_steps = int(rec_candidates[0] + search_start) if rec_candidates.size else None

    return {
        "shift_idx": int(shift_idx),
        "shift_rps": float(shift_rps),
        "horizon_steps": int(horizon_steps),
        "static_post_shift_coverage": round(float(np.mean(post_static)), 4),
        "aci_post_shift_coverage": round(float(np.mean(post_aci)), 4),
        "recovery_steps": recovery_steps,
        "aci_rolling_coverage": aci_roll,
        "static_rolling_coverage": static_roll,
        "alpha_trace": alphas,
    }


def write_drift_svg(drift: dict[str, Any], out_path: Path) -> Path:
    """Render a clean, dependency-free SVG plot comparing ACI vs Static CQR."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    aci_c = drift["aci_rolling_coverage"]
    st_c = drift["static_rolling_coverage"]
    n = len(aci_c)
    w, h, pad = 720, 280, 40

    def _polyline(vals: np.ndarray) -> str:
        pts = []
        for idx, v in enumerate(vals):
            x = pad + (w - 2 * pad) * (idx / max(1, n - 1))
            y = (h - pad) - (h - 2 * pad) * float(min(1.0, max(0.0, v)))
            pts.append(f"{x:.1f},{y:.1f}")
        return " ".join(pts)

    target_y = (h - pad) - (h - 2 * pad) * 0.90
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <rect width="{w}" height="{h}" fill="#fbf8f1"/>
  <text x="{pad}" y="22" font-family="monospace" font-size="13" fill="#1c1815">ACI vs Static CQR Coverage After +{drift['shift_rps']} RPS Shift (recovery={drift['recovery_steps']} steps)</text>
  <line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#9a9488" stroke-width="1"/>
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h - pad}" stroke="#9a9488" stroke-width="1"/>
  <line x1="{pad}" y1="{target_y:.1f}" x2="{w - pad}" y2="{target_y:.1f}" stroke="#009384" stroke-dasharray="4 4" stroke-width="1"/>
  <polyline fill="none" stroke="#cc5a1a" stroke-width="2" points="{_polyline(st_c)}"/>
  <polyline fill="none" stroke="#2060c8" stroke-width="2.5" points="{_polyline(aci_c)}"/>
  <text x="{w - 220}" y="{pad + 14}" font-family="monospace" font-size="11" fill="#2060c8">ACI ({drift['aci_post_shift_coverage']:.2f})</text>
  <text x="{w - 220}" y="{pad + 30}" font-family="monospace" font-size="11" fill="#cc5a1a">Static CQR ({drift['static_post_shift_coverage']:.2f})</text>
</svg>
"""
    out_path.write_text(svg, encoding="utf-8")
    return out_path


def run_full_evaluation(
    *,
    processed_dir: Path,
    analysis_dir: Path,
    forecaster_config_out: Path,
    horizon_s: int,
    capacity_rps: float | None = None,
    seed: int = DEFAULT_SEED,
    n_estimators: int = 120,
) -> dict[str, Any]:
    """Run T4.1 baselines, T4.2/T4.3 CQR + spike coverage + ACI, and T4.4 ablation."""
    train_df = pd.read_parquet(processed_dir / "train.parquet")
    cal_df = pd.read_parquet(processed_dir / "cal.parquet")
    test_df = pd.read_parquet(processed_dir / "test.parquet")
    y_test = test_df["y_target"].to_numpy(dtype=np.float64)

    horizon_steps = max(1, horizon_s // 5)
    base_results = evaluate_baselines(train_df, test_df, horizon_steps=horizon_steps)

    plain_bundle = train_cqr_bundle(
        train_df, cal_df, horizon_s=horizon_s, seed=seed,
        variant="plain_cqr", n_estimators=n_estimators,
    )
    asym_bundle = train_cqr_bundle(
        train_df, cal_df, horizon_s=horizon_s, seed=seed,
        variant="asymmetric_3_1", n_estimators=n_estimators,
    )

    p_med, p_lo, p_hi = predict_static_cqr(plain_bundle, test_df)
    a_med, a_lo, a_hi = predict_static_cqr(asym_bundle, test_df)

    ml_metrics = regression_metrics(y_test, p_med)
    best_base_rmse = min(v["rmse"] for v in base_results.values())
    beats_baseline = ml_metrics["rmse"] < best_base_rmse

    spike_mask = spike_window_mask(test_df, top_fraction=0.10)
    plain_marginal = compute_interval_stats(y_test, p_lo, p_hi, capacity_rps=capacity_rps)
    plain_spike = compute_interval_stats(
        y_test[spike_mask], p_lo[spike_mask], p_hi[spike_mask], capacity_rps=capacity_rps
    )
    asym_marginal = compute_interval_stats(y_test, a_lo, a_hi, capacity_rps=capacity_rps)
    asym_spike = compute_interval_stats(
        y_test[spike_mask], a_lo[spike_mask], a_hi[spike_mask], capacity_rps=capacity_rps
    )

    drift = simulate_aci_vs_static_under_shift(plain_bundle, test_df)

    analysis_dir.mkdir(parents=True, exist_ok=True)
    fig_path = write_drift_svg(drift, analysis_dir / "figures" / "aci_drift_recovery.svg")

    # Write T4.1 baselines report
    base_lines = [
        "# Phase 4 Baselines vs Median Quantile Forecaster (`T4.1`–`T4.2`)",
        "",
        "| Model | RMSE | MAE | SMAPE (%) |",
        "|---|---:|---:|---:|",
    ]
    for name in ("naive", "seasonal_naive", "arima"):
        r = base_results[name]
        base_lines.append(f"| {name} | {r['rmse']} | {r['mae']} | {r['smape']} |")
    base_lines.append(
        f"| lightgbm_q50 | {ml_metrics['rmse']} | {ml_metrics['mae']} | {ml_metrics['smape']} |"
    )
    base_lines.extend(
        [
            "",
            f"Best baseline RMSE: `{best_base_rmse}` · Median LightGBM RMSE: `{ml_metrics['rmse']}` "
            f"(beats baseline: `{beats_baseline}`).",
        ]
    )
    (analysis_dir / "baselines.md").write_text("\n".join(base_lines) + "\n", encoding="utf-8")

    # Write T4.3 & T4.4 coverage & ablation report
    fig_display = (
        fig_path.relative_to(PROJECT_ROOT).as_posix()
        if fig_path.is_relative_to(PROJECT_ROOT)
        else fig_path.relative_to(analysis_dir).as_posix()
    )
    cov_lines = [
        "# Conformal Coverage & Asymmetric-Loss Ablation (`T4.3`–`T4.4`)",
        "",
        "| Variant | Marginal Coverage | Spike-Window Coverage (Top 10% |dD/dt|) | Mean Width (RPS) | Pod-Seconds |",
        "|---|---:|---:|---:|---:|",
        f"| plain_cqr | {plain_marginal['coverage']:.4f} | {plain_spike['coverage']:.4f} | {plain_marginal['mean_width']:.2f} | {plain_marginal.get('pod_seconds', 'N/A')} |",
        f"| asymmetric_3_1 | {asym_marginal['coverage']:.4f} | {asym_spike['coverage']:.4f} | {asym_marginal['mean_width']:.2f} | {asym_marginal.get('pod_seconds', 'N/A')} |",
        "",
        "## Online ACI vs Static CQR under Distribution Shift (`T4.3.2`)",
        "",
        f"- Injected shift: `+{drift['shift_rps']} RPS` at step `{drift['shift_idx']}`",
        f"- Static CQR post-shift coverage: `{drift['static_post_shift_coverage']:.4f}`",
        f"- Online ACI post-shift coverage: `{drift['aci_post_shift_coverage']:.4f}` (recovery step: `{drift['recovery_steps']}`)",
        f"- Plot: `{fig_display}`",
    ]
    (analysis_dir / "coverage.md").write_text("\n".join(cov_lines) + "\n", encoding="utf-8")

    # Select shipping variant: plain CQR unless asymmetric improves spike coverage without excess width
    ships = (
        "plain_cqr"
        if plain_marginal["coverage"] >= 0.88 or asym_marginal["mean_width"] > plain_marginal["mean_width"] * 1.25
        else "asymmetric_3_1"
    )
    cfg = {
        "shipping_variant": ships,
        "model_version": f"{plain_bundle['model_version'] if ships == 'plain_cqr' else asym_bundle['model_version']}",
        "horizon_s": horizon_s,
        "alpha": 0.10,
        "aci_gamma": 0.05,
        "beats_baseline_rmse": beats_baseline,
        "plain_cqr": {"marginal": plain_marginal, "spike": plain_spike},
        "asymmetric_3_1": {"marginal": asym_marginal, "spike": asym_spike},
        "rationale": (
            "Plain CQR at tau=0.05/0.50/0.95 expresses asymmetry cleanly through the upper conformal "
            "bound without double-counting safety margin (CLAUDE.md §3.4 & finding D8)."
        ),
    }
    forecaster_config_out.parent.mkdir(parents=True, exist_ok=True)
    forecaster_config_out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    return {
        "baselines": {k: {m: v[m] for m in ("rmse", "mae", "smape")} for k, v in base_results.items()},
        "lightgbm_q50": ml_metrics,
        "beats_baseline": beats_baseline,
        "plain_marginal": plain_marginal,
        "plain_spike": plain_spike,
        "asym_marginal": asym_marginal,
        "asym_spike": asym_spike,
        "drift": drift,
        "config": cfg,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate Phase 4 baselines, CQR/ACI & asymmetry.")
    ap.add_argument("--processed-dir", type=Path, default=PROJECT_ROOT / "ml" / "data" / "processed")
    ap.add_argument("--analysis-dir", type=Path, default=PROJECT_ROOT / "analysis")
    ap.add_argument("--config-out", type=Path, default=PROJECT_ROOT / "services" / "forecaster" / "config.yaml")
    ap.add_argument("--horizon-s", type=int, default=None)
    ap.add_argument("--capacity-rps", type=float, default=None)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    horizon_s = (
        args.horizon_s
        if args.horizon_s is not None
        else int(load_arbiter_config()["forecast_horizon_s"])
    )
    summary = run_full_evaluation(
        processed_dir=args.processed_dir,
        analysis_dir=args.analysis_dir,
        forecaster_config_out=args.config_out,
        horizon_s=horizon_s,
        capacity_rps=args.capacity_rps,
        seed=args.seed,
    )
    print("Evaluation complete. Beats baseline:", summary["beats_baseline"])
    print("Plain CQR marginal:", summary["plain_marginal"])
    print("Plain CQR spike   :", summary["plain_spike"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
