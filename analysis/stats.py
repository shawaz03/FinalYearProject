#!/usr/bin/env python
"""Phase 7, T7.4 & T7.5 — Statistical evaluation & README table generator.

Implements non-parametric statistical reporting matching thesis rigor (D12):
  - Median and IQR (never mean-alone for latency) with `n` stated per cell.
  - Kruskal–Wallis H-test + tie-corrected Dunn post-hoc with Holm–Bonferroni adjustment.
  - Cliff's delta effect size + seeded bootstrap 95% CI on median differences.
  - Warmup-latency break-even detector (`WARMUP_SECONDS` sweep vs tuned HPA, T7.4).
  - Guarded README benchmark table regeneration from `analysis/results.csv` (T8.1.1).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from itertools import combinations
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from statsmodels.stats.multitest import multipletests

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import PROJECT_ROOT
from cloudpulse_core.seeds import DEFAULT_SEED

SUMMARY_METRICS: Final[tuple[str, ...]] = (
    "sla_violation_pct",
    "p99_latency_ms",
    "pod_seconds",
    "actuation_count",
    "mean_time_to_ready_capacity_s",
    "coverage_pct",
)


def cliffs_delta(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    """Compute Cliff's delta dominance effect size in `[-1.0, 1.0]`."""
    arr_a = np.asarray(a, dtype=np.float64)
    arr_b = np.asarray(b, dtype=np.float64)
    if arr_a.size == 0 or arr_b.size == 0:
        raise ValueError("cliffs_delta requires non-empty groups")
    diff = arr_a[:, None] - arr_b[None, :]
    greater = float(np.sum(diff > 0.0))
    less = float(np.sum(diff < 0.0))
    return (greater - less) / float(arr_a.size * arr_b.size)


def cliffs_magnitude(delta: float) -> str:
    mag = abs(delta)
    if mag < 0.147:
        return "negligible"
    if mag < 0.33:
        return "small"
    if mag < 0.474:
        return "medium"
    return "large"


def bootstrap_median_diff_ci(
    a: list[float] | np.ndarray,
    b: list[float] | np.ndarray,
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float, float]:
    """Return `(median_diff, ci_low, ci_high)` for `median(a) - median(b)`."""
    arr_a = np.asarray(a, dtype=np.float64)
    arr_b = np.asarray(b, dtype=np.float64)
    if arr_a.size == 0 or arr_b.size == 0:
        raise ValueError("bootstrap_median_diff_ci requires non-empty groups")
    obs_diff = float(np.median(arr_a) - np.median(arr_b))
    rng = np.random.default_rng(seed)
    idx_a = rng.integers(0, arr_a.size, size=(n_boot, arr_a.size))
    idx_b = rng.integers(0, arr_b.size, size=(n_boot, arr_b.size))
    diffs = np.median(arr_a[idx_a], axis=1) - np.median(arr_b[idx_b], axis=1)
    lo = float(np.quantile(diffs, alpha / 2.0))
    hi = float(np.quantile(diffs, 1.0 - alpha / 2.0))
    return round(obs_diff, 4), round(lo, 4), round(hi, 4)


def _ablation_label(val: Any) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "none"
    text = str(val).strip()
    return text if text and text.lower() != "nan" else "none"


def summarize_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate runs by `(scaler, ablation, profile, warmup_seconds)` as median & IQR."""
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["ablation_label"] = work["ablation"].map(_ablation_label) if "ablation" in work.columns else "none"
    group_cols = ["scaler", "ablation_label", "profile", "warmup_seconds"]

    records: list[dict[str, Any]] = []
    for keys, grp in work.groupby(group_cols, sort=True):
        scaler, ablation_label, profile, warmup_s = keys
        n = int(len(grp))
        rec: dict[str, Any] = {
            "scaler": str(scaler),
            "ablation": None if ablation_label == "none" else str(ablation_label),
            "profile": str(profile),
            "warmup_seconds": int(warmup_s),
            "n": n,
            "n_sufficient": n >= 5,
            "run_ids": ";".join(sorted(str(r) for r in grp["run_id"].tolist())),
        }
        for col in SUMMARY_METRICS:
            vals = pd.to_numeric(grp[col], errors="coerce").dropna().to_numpy(dtype=np.float64)
            if vals.size == 0:
                rec[f"{col}_median"] = None
                rec[f"{col}_q1"] = None
                rec[f"{col}_q3"] = None
                rec[f"{col}_iqr"] = None
            else:
                q1, med, q3 = np.quantile(vals, [0.25, 0.50, 0.75])
                rec[f"{col}_median"] = round(float(med), 4)
                rec[f"{col}_q1"] = round(float(q1), 4)
                rec[f"{col}_q3"] = round(float(q3), 4)
                rec[f"{col}_iqr"] = round(float(q3 - q1), 4)
        records.append(rec)

    return pd.DataFrame(records)


def kruskal_dunn_posthoc(
    df: pd.DataFrame,
    *,
    group_col: str = "scaler",
    metric: str = "p99_latency_ms",
    n_boot: int = 2000,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Run Kruskal–Wallis H-test and Holm-adjusted Dunn pairwise tests."""
    if df.empty or group_col not in df.columns or metric not in df.columns:
        return {
            "metric": metric,
            "k": 0,
            "n_total": 0,
            "h_stat": 0.0,
            "p_value": 1.0,
            "epsilon_sq": 0.0,
            "pairwise": [],
            "note": "No observations available.",
        }

    clean = df[[group_col, metric]].copy()
    clean[metric] = pd.to_numeric(clean[metric], errors="coerce")
    clean = clean.dropna(subset=[group_col, metric])

    groups: list[tuple[str, np.ndarray]] = []
    for name, grp in clean.groupby(group_col, sort=True):
        arr = grp[metric].to_numpy(dtype=np.float64)
        if arr.size > 0:
            groups.append((str(name), arr))

    k = len(groups)
    n_total = int(sum(arr.size for _, arr in groups))
    if k < 2:
        return {
            "metric": metric,
            "k": k,
            "n_total": n_total,
            "h_stat": 0.0,
            "p_value": 1.0,
            "epsilon_sq": 0.0,
            "pairwise": [],
            "note": "Fewer than 2 non-empty groups available.",
        }

    arrays = [arr for _, arr in groups]
    try:
        h_stat, p_val = scipy_stats.kruskal(*arrays)
        h_val = float(h_stat) if math.isfinite(float(h_stat)) else 0.0
        p_num = float(p_val) if math.isfinite(float(p_val)) else 1.0
    except ValueError:
        h_val, p_num = 0.0, 1.0

    eps_sq = (
        max(0.0, min(1.0, (h_val - k + 1.0) / float(n_total - k)))
        if n_total > k
        else 0.0
    )

    pooled = np.concatenate(arrays)
    ranks = scipy_stats.rankdata(pooled, method="average")
    _, counts = np.unique(pooled, return_counts=True)
    tie_denom = float(n_total**3 - n_total)
    tie_corr = 1.0 - (float(np.sum(counts**3 - counts)) / tie_denom) if tie_denom > 0 else 1.0
    rank_var = (float(n_total * (n_total + 1)) / 12.0) * tie_corr

    mean_ranks: dict[str, float] = {}
    group_map: dict[str, np.ndarray] = {}
    cursor = 0
    for name, arr in groups:
        seg = ranks[cursor : cursor + arr.size]
        mean_ranks[name] = float(np.mean(seg))
        group_map[name] = arr
        cursor += arr.size

    pairs_meta: list[dict[str, Any]] = []
    p_raws: list[float] = []
    for idx, ((name_a, _), (name_b, _)) in enumerate(combinations(groups, 2)):
        arr_a = group_map[name_a]
        arr_b = group_map[name_b]
        n_a, n_b = int(arr_a.size), int(arr_b.size)
        se = math.sqrt(rank_var * (1.0 / n_a + 1.0 / n_b)) if rank_var > 0.0 else 0.0
        z = (mean_ranks[name_a] - mean_ranks[name_b]) / se if se > 0.0 else 0.0
        p_raw = float(2.0 * scipy_stats.norm.sf(abs(z))) if se > 0.0 else 1.0
        delta = cliffs_delta(arr_a, arr_b)
        med_diff, ci_lo, ci_hi = bootstrap_median_diff_ci(
            arr_a, arr_b, n_boot=n_boot, seed=seed + idx
        )
        p_raws.append(p_raw)
        pairs_meta.append(
            {
                "group_a": name_a,
                "group_b": name_b,
                "n_a": n_a,
                "n_b": n_b,
                "z_stat": round(z, 4),
                "p_raw": p_raw,
                "p_adj": p_raw,
                "cliffs_delta": round(delta, 4),
                "effect_magnitude": cliffs_magnitude(delta),
                "median_diff": med_diff,
                "ci_low": ci_lo,
                "ci_high": ci_hi,
            }
        )

    if p_raws:
        _, adjusted, _, _ = multipletests(p_raws, method="holm")
        for row, p_adj in zip(pairs_meta, adjusted):
            row["p_adj"] = float(p_adj)

    return {
        "metric": metric,
        "k": k,
        "n_total": n_total,
        "h_stat": round(h_val, 4),
        "p_value": p_num,
        "epsilon_sq": round(eps_sq, 4),
        "pairwise": pairs_meta,
    }


def analyze_warmup_breakeven(
    df: pd.DataFrame,
    *,
    profile: str = "flash",
    baseline_scaler: str = "hpa-tuned",
) -> dict[str, Any]:
    """Compute CloudPulse vs `baseline_scaler` advantage across `WARMUP_SECONDS` (T7.4)."""
    if df.empty:
        return {
            "profile": profile,
            "baseline_scaler": baseline_scaler,
            "break_even_warmup_s": None,
            "curve": [],
            "finding": "No benchmark runs available to evaluate break-even.",
        }

    work = df.copy()
    work["ablation_label"] = work["ablation"].map(_ablation_label) if "ablation" in work.columns else "none"
    sub = work[(work["profile"] == profile) & (work["ablation_label"] == "none")]
    warmups = sorted(int(w) for w in sub["warmup_seconds"].dropna().unique())

    curve: list[dict[str, Any]] = []
    for warmup_s in warmups:
        w_df = sub[sub["warmup_seconds"] == warmup_s]
        cp = w_df[w_df["scaler"] == "cloudpulse"]
        bl = w_df[w_df["scaler"] == baseline_scaler]
        if cp.empty or bl.empty:
            continue

        cp_sla = float(np.median(cp["sla_violation_pct"]))
        bl_sla = float(np.median(bl["sla_violation_pct"]))
        cp_p99 = float(np.median(cp["p99_latency_ms"]))
        bl_p99 = float(np.median(bl["p99_latency_ms"]))
        cp_cost = float(np.median(cp["pod_seconds"]))
        bl_cost = float(np.median(bl["pod_seconds"]))

        curve.append(
            {
                "warmup_seconds": warmup_s,
                "n_cloudpulse": int(len(cp)),
                "n_baseline": int(len(bl)),
                "cloudpulse_sla_median": round(cp_sla, 4),
                "baseline_sla_median": round(bl_sla, 4),
                "sla_advantage_pct": round(bl_sla - cp_sla, 4),
                "cloudpulse_p99_median": round(cp_p99, 3),
                "baseline_p99_median": round(bl_p99, 3),
                "cloudpulse_pod_s_median": round(cp_cost, 3),
                "baseline_pod_s_median": round(bl_cost, 3),
            }
        )

    break_even: int | None = None
    for idx, pt in enumerate(curve):
        if pt["sla_advantage_pct"] > 0.0 and all(
            later["sla_advantage_pct"] >= 0.0 for later in curve[idx:]
        ):
            break_even = int(pt["warmup_seconds"])
            break

    if break_even is not None:
        if any(pt["warmup_seconds"] < break_even for pt in curve):
            finding = (
                f"On `{profile}`, CloudPulse breaks even against `{baseline_scaler}` at "
                f"WARMUP_SECONDS={break_even}s; below {break_even}s predictive scaling does "
                "not pay on SLA violation rate."
            )
        else:
            finding = (
                f"On `{profile}`, CloudPulse leads `{baseline_scaler}` from the lowest tested "
                f"WARMUP_SECONDS={break_even}s."
            )
    else:
        finding = (
            f"On `{profile}`, CloudPulse showed no positive SLA advantage over "
            f"`{baseline_scaler}` across tested warmups."
        )

    return {
        "profile": profile,
        "baseline_scaler": baseline_scaler,
        "break_even_warmup_s": break_even,
        "curve": curve,
        "finding": finding,
    }


def render_benchmark_markdown(df: pd.DataFrame) -> str:
    """Render a traceable Markdown benchmark summary directly from `results.csv`."""
    summary = summarize_cells(df)
    if summary.empty:
        raise ValueError("Cannot render benchmark table from empty results")

    lines = [
        "## 📊 Measured Experimental Benchmarks (Generated from `analysis/results.csv`)",
        "",
        "| Scaler | Ablation | Profile | Warmup (s) | n | SLA Viol % (med [IQR]) | p99 ms (med [IQR]) | Cost pod-s (med) | Run IDs |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]
    for _, row in summary.iterrows():
        abl = row["ablation"] or "—"
        sla_str = f"{row['sla_violation_pct_median']:.2f} [{row['sla_violation_pct_iqr']:.2f}]"
        p99_str = f"{row['p99_latency_ms_median']:.1f} [{row['p99_latency_ms_iqr']:.1f}]"
        pod_str = f"{row['pod_seconds_median']:.1f}"
        run_ids = row["run_ids"].split(";")
        cited = run_ids[0] if len(run_ids) == 1 else f"{run_ids[0]} (+{len(run_ids) - 1} more)"
        lines.append(
            f"| `{row['scaler']}` | {abl} | `{row['profile']}` | {row['warmup_seconds']} | "
            f"{row['n']} | {sla_str} | {p99_str} | {pod_str} | `{cited}` |"
        )
    return "\n".join(lines)


def sync_readme_benchmark_table(readme_path: Path, results_df: pd.DataFrame) -> bool:
    """Replace Section 9 in `README.md` cleanly from measured `results_df` (Exit Gate 7)."""
    if results_df.empty:
        raise ValueError("Refusing to overwrite README.md with empty benchmark results")
    text = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"## 📊 Experimental Benchmarking & Target Metrics\n.*?(?=\n---)",
        flags=re.DOTALL,
    )
    if not pattern.search(text):
        raise ValueError(f"Could not locate Experimental Benchmarking section in {readme_path}")
    replacement = render_benchmark_markdown(results_df)
    updated = pattern.sub(replacement, text, count=1)
    readme_path.write_text(updated, encoding="utf-8")
    return True


def build_stats_report(df: pd.DataFrame, *, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Compute the complete structured summary, Kruskal/Dunn tests, and break-even report."""
    summary_df = summarize_cells(df)
    kw_results: dict[str, Any] = {}
    if not df.empty:
        work = df.copy()
        work["ablation_label"] = (
            work["ablation"].map(_ablation_label) if "ablation" in work.columns else "none"
        )
        base = work[work["ablation_label"] == "none"]
        for (profile, warmup_s), grp in base.groupby(["profile", "warmup_seconds"], sort=True):
            key = f"{profile}-w{int(warmup_s)}"
            kw_results[key] = {
                "p99_latency_ms": kruskal_dunn_posthoc(grp, metric="p99_latency_ms", seed=seed),
                "sla_violation_pct": kruskal_dunn_posthoc(
                    grp, metric="sla_violation_pct", seed=seed
                ),
                "pod_seconds": kruskal_dunn_posthoc(grp, metric="pod_seconds", seed=seed),
            }

    breakeven = {
        str(p): analyze_warmup_breakeven(df, profile=str(p))
        for p in sorted(df["profile"].dropna().unique())
    } if not df.empty else {}

    return {
        "summary_cells": summary_df.to_dict(orient="records"),
        "kruskal_wallis": kw_results,
        "warmup_breakeven": breakeven,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run Phase 7 statistical evaluation over analysis/results.csv."
    )
    ap.add_argument(
        "--results-csv",
        type=Path,
        default=PROJECT_ROOT / "analysis" / "results.csv",
    )
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument(
        "--sync-readme",
        action="store_true",
        help="Regenerate README.md Section 9 from measured results.csv rows.",
    )
    ap.add_argument("--readme", type=Path, default=PROJECT_ROOT / "README.md")
    args = ap.parse_args(argv)

    if not args.results_csv.exists():
        print(f"No results CSV found at {args.results_csv} (0 rows).", flush=True)
        return 0

    df = pd.read_csv(args.results_csv)
    report = build_stats_report(df, seed=args.seed)

    if args.out_json is not None:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.sync_readme:
        sync_readme_benchmark_table(args.readme, df)
        print(f"Synced measured benchmark table into {args.readme}", flush=True)

    print(
        f"Analysed {len(df)} runs across {len(report['summary_cells'])} cells "
        f"(Kruskal-Wallis slices: {len(report['kruskal_wallis'])}).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
