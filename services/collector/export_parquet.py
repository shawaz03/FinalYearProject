#!/usr/bin/env python
"""Export telemetry from PostgreSQL to `ml/data/raw/` Parquet files (T3.2.1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import PROJECT_ROOT


def fetch_runs_from_postgres(dsn: str, run_id: str | None = None) -> pd.DataFrame:
    import psycopg

    sql = (
        "SELECT ts, rps, cpu_millicores_total, mem_bytes, p50, p90, p99, "
        "ready_replicas, profile, run_id, is_imputed FROM telemetry"
    )
    params: tuple = ()
    if run_id:
        sql += " WHERE run_id = %s"
        params = (run_id,)
    sql += " ORDER BY run_id, ts ASC"

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [desc.name for desc in cur.description]

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export Postgres telemetry to Parquet.")
    ap.add_argument("--pg-dsn", required=True)
    ap.add_argument("--run-id", default=None, help="Optional single run_id filter")
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "ml" / "data" / "raw" / "telemetry_all.parquet",
    )
    args = ap.parse_args(argv)

    df = fetch_runs_from_postgres(args.pg_dsn, run_id=args.run_id)
    if df.empty:
        print("No telemetry rows found.", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(f"Exported {len(df)} rows across {df['run_id'].nunique()} run(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
