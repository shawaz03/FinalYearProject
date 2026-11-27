#!/usr/bin/env python
"""Phase 3, T3.1 — Prometheus -> PostgreSQL / Parquet telemetry collector.

Polls canonical PromQL (`services/collector/promql.py`) on an exact 5-second
grid, queries Kubernetes `status.ready_replicas` directly, reconciles missing
intervals without creating duplicates, and persists rows to PostgreSQL and/or
Parquet under `ml/data/raw/`.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import PROJECT_ROOT, SCRAPE_INTERVAL_S
from services.collector.promql import TELEMETRY_QUERIES

logger = logging.getLogger("cloudpulse.collector")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
NAMESPACE = "cloudpulse"
DEPLOYMENT = "target-app"


class PromClientProtocol(Protocol):
    def custom_query(self, query: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class TelemetryRow:
    ts: datetime
    rps: float
    cpu_millicores_total: float
    mem_bytes: float
    p50: float | None
    p90: float | None
    p99: float | None
    ready_replicas: int
    profile: str
    run_id: str
    is_imputed: bool = False


def align_epoch_to_step(epoch_s: float, step_s: int = SCRAPE_INTERVAL_S) -> datetime:
    """Snap a Unix epoch timestamp down to the canonical `step_s` UTC grid."""
    snapped = int(math.floor(epoch_s / step_s) * step_s)
    return datetime.fromtimestamp(snapped, tz=timezone.utc)


def _parse_prom_scalar(
    result: list[dict[str, Any]], *, allow_nan: bool = False
) -> float | None:
    if not result:
        return None if allow_nan else 0.0
    raw = result[0].get("value")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None if allow_nan else 0.0
    val = float(raw[1])
    if not math.isfinite(val):
        return None if allow_nan else 0.0
    return val


def scrape_once(
    prom: PromClientProtocol,
    ready_replicas_fn: Callable[[], int],
    *,
    ts: datetime,
    profile: str,
    run_id: str,
) -> TelemetryRow:
    """Execute one canonical Prometheus + Kubernetes scrape at aligned time `ts`."""
    rps = _parse_prom_scalar(prom.custom_query(TELEMETRY_QUERIES["rps"]))
    cpu = _parse_prom_scalar(
        prom.custom_query(TELEMETRY_QUERIES["cpu_millicores_total"])
    )
    mem = _parse_prom_scalar(prom.custom_query(TELEMETRY_QUERIES["mem_bytes"]))
    p50 = _parse_prom_scalar(
        prom.custom_query(TELEMETRY_QUERIES["p50"]), allow_nan=True
    )
    p90 = _parse_prom_scalar(
        prom.custom_query(TELEMETRY_QUERIES["p90"]), allow_nan=True
    )
    p99 = _parse_prom_scalar(
        prom.custom_query(TELEMETRY_QUERIES["p99"]), allow_nan=True
    )
    replicas = int(ready_replicas_fn())

    return TelemetryRow(
        ts=ts.astimezone(timezone.utc),
        rps=float(rps if rps is not None else 0.0),
        cpu_millicores_total=float(cpu if cpu is not None else 0.0),
        mem_bytes=float(mem if mem is not None else 0.0),
        p50=p50,
        p90=p90,
        p99=p99,
        ready_replicas=replicas,
        profile=profile,
        run_id=run_id,
        is_imputed=False,
    )


def rows_to_dataframe(rows: list[TelemetryRow]) -> pd.DataFrame:
    """Convert telemetry rows to a sorted, schema-typed DataFrame."""
    if not rows:
        return pd.DataFrame(
            columns=[
                "ts",
                "rps",
                "cpu_millicores_total",
                "mem_bytes",
                "p50",
                "p90",
                "p99",
                "ready_replicas",
                "profile",
                "run_id",
                "is_imputed",
            ]
        )
    df = pd.DataFrame([asdict(r) for r in rows])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts", kind="mergesort").drop_duplicates(
        subset=["run_id", "ts"], keep="last"
    )
    return df.reset_index(drop=True)


def reconcile_time_grid(
    rows: list[TelemetryRow],
    *,
    step_s: int = SCRAPE_INTERVAL_S,
) -> pd.DataFrame:
    """Deduplicate and fill any missing `step_s` timestamps cleanly (T3.1.2).

    Missing ticks inside a run are forward-filled (or back-filled at the head)
    and explicitly flagged with `is_imputed = True` so downstream steps have
    no missing timestamps and zero duplicates while staying transparent.
    """
    df = rows_to_dataframe(rows)
    if df.empty:
        return df

    run_ids = df["run_id"].unique()
    if len(run_ids) != 1:
        raise ValueError(
            f"reconcile_time_grid expects a single run_id, got {list(run_ids)}"
        )

    start_ts = df["ts"].min()
    end_ts = df["ts"].max()
    full_index = pd.date_range(start=start_ts, end=end_ts, freq=f"{step_s}s", tz="UTC")

    indexed = df.set_index("ts")
    missing_count = int(len(full_index.difference(indexed.index)))
    if missing_count > 0:
        logger.warning(
            "run_id=%s had %d missing %ds timestamps; forward-filling gap ticks",
            run_ids[0],
            missing_count,
            step_s,
        )

    reindexed = indexed.reindex(full_index)
    imputed_mask = reindexed["rps"].isna()
    reindexed["is_imputed"] = imputed_mask | reindexed["is_imputed"].fillna(False)

    for col in ("rps", "cpu_millicores_total", "mem_bytes", "ready_replicas", "profile", "run_id"):
        reindexed[col] = reindexed[col].ffill().bfill()
    for lat_col in ("p50", "p90", "p99"):
        reindexed[lat_col] = reindexed[lat_col].ffill()

    reindexed["ready_replicas"] = reindexed["ready_replicas"].astype(int)
    reindexed["is_imputed"] = reindexed["is_imputed"].astype(bool)
    out = reindexed.reset_index().rename(columns={"index": "ts"})
    return out[
        [
            "ts",
            "rps",
            "cpu_millicores_total",
            "mem_bytes",
            "p50",
            "p90",
            "p99",
            "ready_replicas",
            "profile",
            "run_id",
            "is_imputed",
        ]
    ]


def ensure_postgres_schema(dsn: str) -> None:
    import psycopg

    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()


def upsert_dataframe_postgres(df: pd.DataFrame, dsn: str) -> int:
    """Idempotently upsert reconciled telemetry rows into PostgreSQL."""
    if df.empty:
        return 0
    import psycopg

    sql = """
        INSERT INTO telemetry (
            ts, rps, cpu_millicores_total, mem_bytes,
            p50, p90, p99, ready_replicas, profile, run_id, is_imputed
        ) VALUES (
            %(ts)s, %(rps)s, %(cpu_millicores_total)s, %(mem_bytes)s,
            %(p50)s, %(p90)s, %(p99)s, %(ready_replicas)s, %(profile)s, %(run_id)s, %(is_imputed)s
        )
        ON CONFLICT (run_id, ts) DO UPDATE SET
            rps = EXCLUDED.rps,
            cpu_millicores_total = EXCLUDED.cpu_millicores_total,
            mem_bytes = EXCLUDED.mem_bytes,
            p50 = EXCLUDED.p50,
            p90 = EXCLUDED.p90,
            p99 = EXCLUDED.p99,
            ready_replicas = EXCLUDED.ready_replicas,
            profile = EXCLUDED.profile,
            is_imputed = EXCLUDED.is_imputed;
    """
    records = df.to_dict(orient="records")
    for rec in records:
        rec["ts"] = pd.Timestamp(rec["ts"]).to_pydatetime()
        for lat_col in ("p50", "p90", "p99"):
            if pd.isna(rec[lat_col]):
                rec[lat_col] = None
            else:
                rec[lat_col] = float(rec[lat_col])

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        conn.commit()
    return len(records)


def export_parquet(df: pd.DataFrame, out_path: Path) -> Path:
    """Persist reconciled telemetry DataFrame to Parquet for `ml/`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return out_path


def _k8s_ready_replicas_provider(namespace: str = NAMESPACE, deployment: str = DEPLOYMENT) -> Callable[[], int]:
    from kubernetes import client, config

    config.load_kube_config()
    apps = client.AppsV1Api()

    def _read() -> int:
        dep = apps.read_namespaced_deployment(deployment, namespace)
        return int(dep.status.ready_replicas or 0)

    return _read


def collect_run(
    *,
    prom: PromClientProtocol,
    ready_replicas_fn: Callable[[], int],
    profile: str,
    run_id: str,
    duration_s: int,
    step_s: int = SCRAPE_INTERVAL_S,
    pg_dsn: str | None = None,
    parquet_out: Path | None = None,
) -> pd.DataFrame:
    """Run aligned 5 s telemetry collection for `duration_s` seconds."""
    if pg_dsn:
        ensure_postgres_schema(pg_dsn)

    rows: list[TelemetryRow] = []
    start_wall = time.time()
    next_tick = align_epoch_to_step(start_wall, step_s=step_s)
    end_tick = next_tick + timedelta(seconds=duration_s)

    while next_tick < end_tick:
        now = time.time()
        sleep_s = next_tick.timestamp() - now
        if sleep_s > 0:
            time.sleep(sleep_s)

        try:
            row = scrape_once(
                prom,
                ready_replicas_fn,
                ts=next_tick,
                profile=profile,
                run_id=run_id,
            )
            rows.append(row)
        except Exception as exc:
            logger.warning("Scrape failed at %s: %s", next_tick.isoformat(), exc)

        next_tick += timedelta(seconds=step_s)

    df = reconcile_time_grid(rows, step_s=step_s)
    if pg_dsn and not df.empty:
        upsert_dataframe_postgres(df, pg_dsn)
    if parquet_out and not df.empty:
        export_parquet(df, parquet_out)
    return df


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description="CloudPulse 5 s telemetry collector (T3.1).")
    ap.add_argument("--prom-url", default="http://127.0.0.1:9090")
    ap.add_argument("--profile", required=True, choices=("gradual", "flash", "diurnal"))
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--duration", type=int, default=900, help="Collection duration in seconds")
    ap.add_argument("--step", type=int, default=SCRAPE_INTERVAL_S)
    ap.add_argument("--pg-dsn", default=None, help="Optional PostgreSQL DSN")
    ap.add_argument(
        "--parquet-out",
        type=Path,
        default=None,
        help="Optional Parquet output path (defaults to ml/data/raw/<run_id>.parquet)",
    )
    args = ap.parse_args(argv)

    from prometheus_api_client import PrometheusConnect

    prom = PrometheusConnect(url=args.prom_url, disable_ssl=True)
    ready_fn = _k8s_ready_replicas_provider()
    out_path = args.parquet_out or (
        PROJECT_ROOT / "ml" / "data" / "raw" / f"{args.run_id}.parquet"
    )

    df = collect_run(
        prom=prom,
        ready_replicas_fn=ready_fn,
        profile=args.profile,
        run_id=args.run_id,
        duration_s=args.duration,
        step_s=args.step,
        pg_dsn=args.pg_dsn,
        parquet_out=out_path,
    )
    logger.info("Collected %d rows -> %s", len(df), out_path)
    return 0 if not df.empty else 1


if __name__ == "__main__":
    raise SystemExit(main())
