"""Tests for Phase 7 Step 7.4 non-parametric statistics and break-even analysis (`analysis/stats.py`)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


def _synthetic_results_df() -> pd.DataFrame:
    """Create a synthetic multi-seed (n=5) benchmark table matching `results.csv` schema."""
    rows = []
    # Flash profile, warmup=30s across 3 scalers (n=5 each):
    # - hpa-default: highest latency / SLA violations
    # - hpa-tuned: medium latency / SLA violations
    # - cloudpulse: lowest latency / SLA violations
    group_specs = [
        ("hpa-default", None, 30, [210.0, 225.0, 240.0, 215.0, 230.0], [18.0, 19.5, 21.0, 17.5, 20.0], 4100.0),
        ("hpa-tuned", None, 30, [135.0, 142.0, 150.0, 138.0, 145.0], [9.0, 10.2, 11.0, 8.8, 9.8], 4600.0),
        ("cloudpulse", None, 30, [62.0, 68.0, 74.0, 65.0, 70.0], [1.1, 1.4, 1.8, 1.0, 1.5], 4700.0),
    ]
    for scaler, abl, warmup, p99s, slas, pod_s in group_specs:
        for rep in range(1, 6):
            rows.append(
                {
                    "run_id": f"{scaler}-flash-w{warmup}-s{41 + rep}-r{rep}",
                    "git_sha": "53a94ee",
                    "git_dirty": False,
                    "scaler": scaler,
                    "ablation": abl,
                    "profile": "flash",
                    "warmup_seconds": warmup,
                    "work_iterations": 15000,
                    "seed": 41 + rep,
                    "repetition": rep,
                    "sla_violation_pct": slas[rep - 1],
                    "p50_latency_ms": 12.0,
                    "p90_latency_ms": 28.0,
                    "p99_latency_ms": p99s[rep - 1],
                    "pod_seconds": pod_s + rep * 10.0,
                    "actuation_count": 4,
                    "mean_time_to_ready_capacity_s": 15.0,
                    "matured_forecasts": 20 if scaler == "cloudpulse" else 0,
                    "coverage_pct": 91.0 if scaler == "cloudpulse" else None,
                    "achieved_rps": 30.0,
                    "tracking_error_pct": 0.1,
                }
            )

    # Warmup sweep on flash for T7.4 (w=0, 5, 15):
    # At w=0s and w=5s, hpa-tuned wins slightly or ties; at w=15s & w=30s cloudpulse wins -> break-even = 15s!
    sweep_specs = [
        ("hpa-tuned", 0, [2.0] * 5, [48.0] * 5),
        ("cloudpulse", 0, [2.4] * 5, [52.0] * 5),
        ("hpa-tuned", 5, [3.1] * 5, [64.0] * 5),
        ("cloudpulse", 5, [3.2] * 5, [66.0] * 5),
        ("hpa-tuned", 15, [6.8] * 5, [118.0] * 5),
        ("cloudpulse", 15, [1.5] * 5, [68.0] * 5),
    ]
    for scaler, warmup, slas, p99s in sweep_specs:
        for rep in range(1, 6):
            rows.append(
                {
                    "run_id": f"{scaler}-flash-w{warmup}-s{41 + rep}-r{rep}",
                    "git_sha": "53a94ee",
                    "git_dirty": False,
                    "scaler": scaler,
                    "ablation": None,
                    "profile": "flash",
                    "warmup_seconds": warmup,
                    "work_iterations": 15000,
                    "seed": 41 + rep,
                    "repetition": rep,
                    "sla_violation_pct": slas[rep - 1],
                    "p50_latency_ms": 10.0,
                    "p90_latency_ms": 20.0,
                    "p99_latency_ms": p99s[rep - 1],
                    "pod_seconds": 4500.0,
                    "actuation_count": 3,
                    "mean_time_to_ready_capacity_s": 10.0,
                    "matured_forecasts": 20 if scaler == "cloudpulse" else 0,
                    "coverage_pct": 90.0 if scaler == "cloudpulse" else None,
                    "achieved_rps": 30.0,
                    "tracking_error_pct": 0.1,
                }
            )
    return pd.DataFrame(rows)


def test_summarize_cells_computes_median_iqr_and_run_id_provenance() -> None:
    from analysis.stats import summarize_cells

    df = _synthetic_results_df()
    summary = summarize_cells(df[df["warmup_seconds"] == 30])
    assert len(summary) == 3
    cp = summary[summary["scaler"] == "cloudpulse"].iloc[0]
    assert cp["n"] == 5
    assert bool(cp["n_sufficient"]) is True
    assert cp["p99_latency_ms_median"] == pytest.approx(68.0)
    assert cp["p99_latency_ms_iqr"] == pytest.approx(5.0)
    assert "cloudpulse-flash-w30-s42-r1" in cp["run_ids"]


def test_kruskal_dunn_and_cliffs_delta_detect_ordered_groups() -> None:
    from analysis.stats import cliffs_delta, kruskal_dunn_posthoc

    assert cliffs_delta([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(-1.0)

    df = _synthetic_results_df()
    w30 = df[df["warmup_seconds"] == 30]
    res = kruskal_dunn_posthoc(w30, metric="p99_latency_ms", seed=42)
    assert res["k"] == 3
    assert res["n_total"] == 15
    assert res["p_value"] < 0.01
    assert res["epsilon_sq"] > 0.8

    pairs = res["pairwise"]
    assert len(pairs) == 3
    cp_vs_def = [
        r for r in pairs if {r["group_a"], r["group_b"]} == {"cloudpulse", "hpa-default"}
    ][0]
    assert cp_vs_def["p_adj"] < 0.05
    assert abs(cp_vs_def["cliffs_delta"]) == pytest.approx(1.0)
    assert cp_vs_def["ci_low"] <= cp_vs_def["median_diff"] <= cp_vs_def["ci_high"]


def test_warmup_breakeven_identifies_crossover_and_disadvantage_below() -> None:
    from analysis.stats import analyze_warmup_breakeven

    df = _synthetic_results_df()
    sweep = analyze_warmup_breakeven(df, profile="flash")
    assert sweep["break_even_warmup_s"] == 15
    assert "below 15s" in sweep["finding"].lower()
    assert len(sweep["curve"]) == 4  # warmups 0, 5, 15, 30


def test_stats_cli_and_notebook_structure(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    csv_path = tmp_path / "results.csv"
    _synthetic_results_df().to_csv(csv_path, index=False)
    out_json = tmp_path / "stats_report.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(root / "analysis" / "stats.py"),
            "--results-csv",
            str(csv_path),
            "--out-json",
            str(out_json),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out_json.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert "summary_cells" in payload
    assert "kruskal_wallis" in payload
    assert "warmup_breakeven" in payload
    assert "Kruskal" in proc.stdout or "Analysed" in proc.stdout

    nb_path = root / "analysis" / "stats.ipynb"
    assert nb_path.exists()
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4
    joined_src = "\n".join("".join(c.get("source", [])) for c in nb["cells"])
    assert "kruskal_dunn_posthoc" in joined_src
    assert "analyze_warmup_breakeven" in joined_src


def test_sync_readme_benchmark_table_refuses_empty_and_updates_copy(tmp_path: Path) -> None:
    from analysis.stats import sync_readme_benchmark_table

    root = Path(__file__).resolve().parent.parent
    readme_copy = tmp_path / "README.md"
    readme_copy.write_text((root / "README.md").read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        sync_readme_benchmark_table(readme_copy, pd.DataFrame())

    sync_readme_benchmark_table(readme_copy, _synthetic_results_df())
    updated = readme_copy.read_text(encoding="utf-8")
    assert "TARGET VALUES — NOT YET MEASURED" not in updated
    assert "Measured Experimental Benchmarks" in updated
    assert "cloudpulse-flash-w30-s42-r1" in updated

