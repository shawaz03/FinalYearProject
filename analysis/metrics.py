#!/usr/bin/env python
"""Phase 7, T7.6 — Primary experimental evaluation metrics (`analysis/metrics.py`).

Computes all six locked Phase 7 metrics per benchmark `run_id`:
  1. `sla_violation_pct` — client-side `% requests > SLO` (`k6_summary.json`)
  2. `p99_latency_ms` — client-side 99th percentile latency (`k6_summary.json`)
  3. `pod_seconds` — trapezoidal time-integral of `ready_replicas` (`telemetry.parquet`)
  4. `actuation_count` — successful scale actuations (`decisions.jsonl` or `telemetry.parquet`)
  5. `mean_time_to_ready_capacity_s` — mean lag from scale-up / deficit onset to ready capacity
  6. `coverage_pct` — empirical forecast interval hit rate at `t + horizon_s` (`decisions.jsonl`)

Every output row in `analysis/results.csv` traces directly to a validated `run_id`.
Primary cost metric is `pod_seconds` (never fabricated dollar amounts — D14).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import PROJECT_ROOT, SCRAPE_INTERVAL_S
from experiments.k6_summary import parse_k6_summary
from experiments.manifest import validate_manifest
from services.arbiter.decision_log import load_decision_log

RESULTS_COLUMNS: Final[tuple[str, ...]] = (
    "run_id",
    "git_sha",
    "git_dirty",
    "scaler",
    "ablation",
    "profile",
    "warmup_seconds",
    "work_iterations",
    "seed",
    "repetition",
    "sla_violation_pct",
    "p50_latency_ms",
    "p90_latency_ms",
    "p99_latency_ms",
    "pod_seconds",
    "actuation_count",
    "mean_time_to_ready_capacity_s",
    "matured_forecasts",
    "coverage_pct",
    "achieved_rps",
    "tracking_error_pct",
)


def _normalize_telemetry_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    norm = df.copy()
    norm["ts"] = pd.to_datetime(norm["ts"], utc=True)
    norm = norm.sort_values("ts").reset_index(drop=True)
    return norm


def compute_pod_seconds(
    telemetry_df: pd.DataFrame, *, fallback_step_s: int = SCRAPE_INTERVAL_S
) -> float:
    """Integrate `ready_replicas(t)` over UTC timestamps via the trapezoidal rule."""
    df = _normalize_telemetry_df(telemetry_df)
    if df.empty:
        return 0.0
    replicas = [float(r) for r in df["ready_replicas"].tolist()]
    if len(replicas) == 1:
        return round(replicas[0] * float(fallback_step_s), 3)

    epochs = [float(ts.timestamp()) for ts in df["ts"].tolist()]
    total = 0.0
    for idx in range(1, len(epochs)):
        dt = max(0.0, epochs[idx] - epochs[idx - 1])
        total += 0.5 * (replicas[idx - 1] + replicas[idx]) * dt
    return round(total, 3)


def _match_nearest_rps(
    epochs: list[float],
    rps_values: list[float],
    target_epoch: float,
    *,
    max_delta_s: float = float(SCRAPE_INTERVAL_S),
) -> float | None:
    if not epochs:
        return None
    pos = bisect_left(epochs, target_epoch)
    candidates: list[tuple[float, float]] = []
    if pos < len(epochs):
        candidates.append((abs(epochs[pos] - target_epoch), rps_values[pos]))
    if pos > 0:
        candidates.append((abs(epochs[pos - 1] - target_epoch), rps_values[pos - 1]))
    if not candidates:
        return None
    delta, matched_rps = min(candidates, key=lambda pair: pair[0])
    if delta <= max_delta_s:
        return matched_rps
    return None


def compute_predictive_decision_metrics(
    decisions: list[dict[str, Any]],
    telemetry_df: pd.DataFrame,
    *,
    horizon_s: int | None,
    coverage_tolerance_s: float = float(SCRAPE_INTERVAL_S),
) -> tuple[int, float | None, float | None, int]:
    """Compute `(actuation_count, mean_time_to_ready_s, coverage_pct, matured_forecasts)`."""
    df = _normalize_telemetry_df(telemetry_df)
    epochs = [float(ts.timestamp()) for ts in df["ts"].tolist()] if not df.empty else []
    replicas = [int(r) for r in df["ready_replicas"].tolist()] if not df.empty else []
    rps_values = [float(v) for v in df["rps"].tolist()] if not df.empty else []

    actuations = 0
    ready_lags: list[float] = []
    covered = 0
    matured = 0

    for entry in decisions:
        act_ts = datetime.fromisoformat(str(entry["ts_utc"]).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
        act_epoch = act_ts.timestamp()
        from_rep = int(entry["from_replicas"])
        to_rep = int(entry["to_replicas"])
        applied = bool(entry.get("actuated")) and (entry.get("actuation_success") is not False)

        if applied:
            actuations += 1
            if to_rep > from_rep and epochs:
                idx = bisect_left(epochs, act_epoch)
                while idx < len(epochs):
                    if replicas[idx] >= to_rep:
                        ready_lags.append(max(0.0, epochs[idx] - act_epoch))
                        break
                    idx += 1

        if (
            horizon_s is not None
            and horizon_s > 0
            and not bool(entry.get("degraded", False))
            and "lower_rps" in entry
            and "upper_bound_rps" in entry
        ):
            lower = float(entry["lower_rps"])
            upper = float(entry["upper_bound_rps"])
            if math.isfinite(lower) and math.isfinite(upper) and upper >= lower:
                target_epoch = (act_ts + timedelta(seconds=int(horizon_s))).timestamp()
                actual_rps = _match_nearest_rps(
                    epochs,
                    rps_values,
                    target_epoch,
                    max_delta_s=coverage_tolerance_s,
                )
                if actual_rps is not None:
                    matured += 1
                    if lower <= actual_rps <= upper:
                        covered += 1

    mean_lag = round(sum(ready_lags) / len(ready_lags), 3) if ready_lags else None
    cov_pct = round(100.0 * covered / matured, 4) if matured > 0 else None
    return actuations, mean_lag, cov_pct, matured


def compute_telemetry_only_scaling_metrics(
    telemetry_df: pd.DataFrame, *, capacity_rps: float | None
) -> tuple[int, float | None]:
    """Compute `(actuation_count, mean_time_to_ready_s)` from `telemetry.parquet` alone."""
    df = _normalize_telemetry_df(telemetry_df)
    if df.empty:
        return 0, None

    replicas = [int(r) for r in df["ready_replicas"].tolist()]
    actuations = sum(1 for i in range(1, len(replicas)) if replicas[i] != replicas[i - 1])

    if capacity_rps is None or capacity_rps <= 0:
        return actuations, None

    epochs = [float(ts.timestamp()) for ts in df["ts"].tolist()]
    rps_values = [float(v) for v in df["rps"].tolist()]
    deficit_start: float | None = None
    lags: list[float] = []

    for epoch, rps, ready in zip(epochs, rps_values, replicas):
        cap = float(ready) * float(capacity_rps)
        if deficit_start is None:
            if rps > cap:
                deficit_start = epoch
        else:
            if cap >= rps:
                lags.append(max(0.0, epoch - deficit_start))
                deficit_start = None

    mean_lag = round(sum(lags) / len(lags), 3) if lags else None
    return actuations, mean_lag


def compute_run_metrics(run_dir: Path) -> dict[str, Any]:
    """Compute all six canonical Phase 7 metrics from one committed run directory."""
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Run manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)

    artifacts = manifest.get("artifacts") or {}
    k6_rel = artifacts.get("k6_summary", "k6_summary.json")
    k6 = parse_k6_summary(run_dir / k6_rel)

    tel_rel = artifacts.get("telemetry", "telemetry.parquet")
    tel_path = run_dir / tel_rel
    if not tel_path.exists():
        raise FileNotFoundError(f"Telemetry parquet missing: {tel_path}")
    telemetry_df = pd.read_parquet(tel_path)

    params = manifest.get("parameters") or {}
    capacity_rps = params.get("capacity_rps")
    horizon_s = params.get("horizon_s")

    dec_rel = artifacts.get("decisions")
    dec_path = (run_dir / dec_rel) if dec_rel else (run_dir / "decisions.jsonl")
    if dec_path.exists():
        decisions = load_decision_log(dec_path)
        actuations, ready_lag_s, coverage_pct, matured = compute_predictive_decision_metrics(
            decisions,
            telemetry_df,
            horizon_s=int(horizon_s) if horizon_s is not None else None,
        )
    else:
        actuations, ready_lag_s = compute_telemetry_only_scaling_metrics(
            telemetry_df,
            capacity_rps=float(capacity_rps) if capacity_rps is not None else None,
        )
        coverage_pct = None
        matured = 0

    latency = k6["latency_ms"]
    return {
        "run_id": manifest["run_id"],
        "git_sha": manifest["git_sha"],
        "git_dirty": bool(manifest["git_dirty"]),
        "scaler": manifest["scaler"],
        "ablation": params.get("ablation"),
        "profile": manifest["profile"],
        "warmup_seconds": int(manifest["warmup_seconds"]),
        "work_iterations": int(manifest["work_iterations"]),
        "seed": int(manifest["seed"]),
        "repetition": int(manifest["repetition"]),
        "sla_violation_pct": round(float(k6["sla_violation_rate"]) * 100.0, 4),
        "p50_latency_ms": float(latency["p50"]),
        "p90_latency_ms": float(latency["p90"]),
        "p99_latency_ms": float(latency["p99"]),
        "pod_seconds": compute_pod_seconds(telemetry_df),
        "actuation_count": int(actuations),
        "mean_time_to_ready_capacity_s": ready_lag_s,
        "matured_forecasts": int(matured),
        "coverage_pct": coverage_pct,
        "achieved_rps": float(k6["achieved_rps"]),
        "tracking_error_pct": float(k6["tracking_error_pct"]),
    }


def analyse_results_dir(
    results_root: Path = PROJECT_ROOT / "experiments" / "results",
    *,
    out_csv: Path = PROJECT_ROOT / "analysis" / "results.csv",
) -> pd.DataFrame:
    """Scan complete benchmark runs in `results_root` and write `analysis/results.csv`."""
    rows: list[dict[str, Any]] = []
    if results_root.exists():
        candidates = sorted(
            p
            for p in results_root.iterdir()
            if p.is_dir() and (p / "manifest.json").exists() and (p / "k6_summary.json").exists()
        )
        for run_dir in candidates:
            rows.append(compute_run_metrics(run_dir))

    rows.sort(
        key=lambda r: (
            str(r["scaler"]),
            str(r["ablation"] or ""),
            str(r["profile"]),
            int(r["warmup_seconds"]),
            int(r["repetition"]),
            str(r["run_id"]),
        )
    )
    df = pd.DataFrame(rows, columns=RESULTS_COLUMNS)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Compute Phase 7 metrics from experiments/results/ -> analysis/results.csv."
    )
    ap.add_argument(
        "--results-root",
        type=Path,
        default=PROJECT_ROOT / "experiments" / "results",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "analysis" / "results.csv",
    )
    ap.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Optional single run directory to inspect and print as JSON.",
    )
    args = ap.parse_args(argv)

    if args.run_dir is not None:
        metrics = compute_run_metrics(args.run_dir)
        print(json.dumps(metrics, indent=2))
        return 0

    df = analyse_results_dir(args.results_root, out_csv=args.out)
    print(f"Analysed {len(df)} benchmark runs -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
