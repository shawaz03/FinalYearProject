#!/usr/bin/env python
"""Phase 2, T2.3.2 — derive forecast horizon H from T2.3.1 readiness samples.

Implements CLAUDE.md §3.3:
    H >= t_ready_p95 + t_decision_interval + t_safety

Reads `cycle_maxima_by_warmup_seconds` (never correlated per-pod batch samples)
from `experiments/results/readiness-*/samples.json`, formats `deploy/readiness.md`,
and writes `services/arbiter/config.yaml` when executed without `--dry-run`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import DECISION_INTERVAL_S, PROJECT_ROOT, SCRAPE_INTERVAL_S
from experiments.measure_readiness import quantile


def round_up_to_step(seconds: float, step_s: int = SCRAPE_INTERVAL_S) -> int:
    """Round horizon up to an exact multiple of the 5 s scrape cadence."""
    if seconds <= 0:
        raise ValueError(f"Horizon must be positive, got {seconds}")
    return int(math.ceil(seconds / step_s) * step_s)


def summarize_readiness(samples_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute per-warmup independent cycle statistics from `samples.json`."""
    cycle_map = samples_payload.get("cycle_maxima_by_warmup_seconds", {})
    if not cycle_map:
        raise ValueError("samples.json missing `cycle_maxima_by_warmup_seconds`")

    rows: list[dict[str, Any]] = []
    for warmup_str in sorted(cycle_map, key=lambda k: int(k)):
        warmup = int(warmup_str)
        maxima = sorted(float(v) for v in cycle_map[warmup_str])
        if not maxima:
            raise ValueError(f"No complete cycles recorded for WARMUP_SECONDS={warmup}")
        p50 = quantile(maxima, 0.50)
        p95 = quantile(maxima, 0.95)
        mx = max(maxima)
        rows.append(
            {
                "warmup_seconds": warmup,
                "cycles": len(maxima),
                "p50_s": round(p50, 2),
                "p95_s": round(p95, 2),
                "max_s": round(mx, 2),
                "overhead_max_s": round(mx - warmup, 2),
            }
        )
    return rows


def derive_horizon_seconds(
    t_ready_p95_s: float,
    decision_interval_s: float = float(DECISION_INTERVAL_S),
    safety_margin_s: float = 5.0,
    step_s: int = SCRAPE_INTERVAL_S,
) -> dict[str, Any]:
    """Compute raw and step-aligned forecast horizon H from §3.3."""
    raw_h = t_ready_p95_s + decision_interval_s + safety_margin_s
    aligned_h = round_up_to_step(raw_h, step_s=step_s)
    return {
        "t_ready_p95_s": round(t_ready_p95_s, 2),
        "t_decision_interval_s": decision_interval_s,
        "t_safety_s": safety_margin_s,
        "raw_horizon_s": round(raw_h, 2),
        "horizon_s": aligned_h,
        "horizon_steps": aligned_h // step_s,
    }


def render_readiness_markdown(
    run_id: str,
    measured_at: str,
    summary_rows: list[dict[str, Any]],
    horizon_by_warmup: dict[int, dict[str, Any]],
) -> str:
    lines = [
        "# Measured pod readiness & forecast horizon H",
        "",
        f"Source run: `{run_id}` (measured `{measured_at}`).",
        "Statistic: independent `cycle_maxima` (scale-patch to all burst pods Ready).",
        "",
        "| WARMUP_SECONDS | cycles | p50 (s) | p95 (s) | max (s) | platform overhead (s) | derived H (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        w = row["warmup_seconds"]
        h = horizon_by_warmup[w]["horizon_s"]
        lines.append(
            f"| {w} | {row['cycles']} | {row['p50_s']:.2f} | {row['p95_s']:.2f} | "
            f"{row['max_s']:.2f} | {row['overhead_max_s']:.2f} | {h} |"
        )
    lines.extend(
        [
            "",
            "Formula (`CLAUDE.md` §3.3): `H >= t_ready_p95 + t_decision_interval + t_safety`, "
            "rounded up to a 5 s scrape cadence multiple.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_arbiter_config(
    run_id: str,
    reference_warmup_s: int,
    summary_rows: list[dict[str, Any]],
    decision_interval_s: float = float(DECISION_INTERVAL_S),
    safety_margin_s: float = 5.0,
) -> tuple[dict[str, Any], str]:
    """Build `services/arbiter/config.yaml` payload and `deploy/readiness.md` text."""
    horizon_by_warmup = {
        row["warmup_seconds"]: derive_horizon_seconds(
            row["p95_s"],
            decision_interval_s=decision_interval_s,
            safety_margin_s=safety_margin_s,
        )
        for row in summary_rows
    }
    if reference_warmup_s not in horizon_by_warmup:
        raise ValueError(
            f"Reference WARMUP_SECONDS={reference_warmup_s} not present in samples.json"
        )
    ref = horizon_by_warmup[reference_warmup_s]
    justification = (
        f"Derived from T2.3.1 ({run_id}) at WARMUP_SECONDS={reference_warmup_s}: "
        f"p95={ref['t_ready_p95_s']}s + decision={ref['t_decision_interval_s']}s "
        f"+ safety={ref['t_safety_s']}s = {ref['raw_horizon_s']}s -> {ref['horizon_s']}s"
    )
    config = {
        "forecast_horizon_s": ref["horizon_s"],
        "horizon_steps": ref["horizon_steps"],
        "reference_warmup_seconds": reference_warmup_s,
        "decision_interval_s": int(decision_interval_s),
        "safety_margin_s": safety_margin_s,
        "justification": justification,
        "horizon_by_warmup_seconds": {
            str(w): v["horizon_s"] for w, v in horizon_by_warmup.items()
        },
        "source_run_id": run_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    md = render_readiness_markdown(
        run_id=run_id,
        measured_at=config["updated_at"],
        summary_rows=summary_rows,
        horizon_by_warmup=horizon_by_warmup,
    )
    return config, md


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Derive H from T2.3.1 samples.json.")
    ap.add_argument("--samples", type=Path, required=True, help="Path to samples.json")
    ap.add_argument("--reference-warmup", type=int, default=30)
    ap.add_argument("--decision-interval", type=float, default=float(DECISION_INTERVAL_S))
    ap.add_argument("--safety", type=float, default=5.0)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print derived table and config without writing deploy/readiness.md or arbiter config.",
    )
    args = ap.parse_args(argv)

    payload = json.loads(args.samples.read_text(encoding="utf-8"))
    summary_rows = summarize_readiness(payload)
    cfg, md = build_arbiter_config(
        run_id=payload["run_id"],
        reference_warmup_s=args.reference_warmup,
        summary_rows=summary_rows,
        decision_interval_s=args.decision_interval,
        safety_margin_s=args.safety,
    )

    print(md)
    print(yaml.safe_dump(cfg, sort_keys=False))

    if not args.dry_run:
        readiness_path = PROJECT_ROOT / "deploy" / "readiness.md"
        arbiter_cfg_path = PROJECT_ROOT / "services" / "arbiter" / "config.yaml"
        arbiter_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        readiness_path.write_text(md, encoding="utf-8")
        arbiter_cfg_path.write_text(
            yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
        )
        print(f"Wrote {readiness_path} and {arbiter_cfg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
