"""Unit tests for the Phase 3 telemetry collector, gap reconciler, and Parquet export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from services.collector.collector import (
    SCHEMA_PATH,
    TelemetryRow,
    align_epoch_to_step,
    export_parquet,
    reconcile_time_grid,
    scrape_once,
)
from services.collector.promql import (
    CPU_MILLICORES_TOTAL_PROMQL,
    MEM_BYTES_PROMQL,
    P50_MS_PROMQL,
    P90_MS_PROMQL,
    P99_MS_PROMQL,
    PER_POD_CPU_PCT_PROMQL,
    RPS_PROMQL,
)


class _FakeProm:
    def __init__(self, responses: dict[str, list[dict]]) -> None:
        self.responses = responses

    def custom_query(self, query: str) -> list[dict]:
        return self.responses.get(query, [])


def test_schema_sql_has_required_columns_and_unique_pk() -> None:
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    for col in (
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
        "PRIMARY KEY (run_id, ts)",
    ):
        assert col in ddl


def test_promql_expressions_cover_queries_md() -> None:
    assert 'http_requests_total{namespace="cloudpulse",status="2xx"}' in RPS_PROMQL
    assert "container_cpu_usage_seconds_total" in CPU_MILLICORES_TOTAL_PROMQL
    assert "container_spec_cpu_quota" in PER_POD_CPU_PCT_PROMQL
    assert "container_memory_working_set_bytes" in MEM_BYTES_PROMQL
    assert "0.50" in P50_MS_PROMQL
    assert "0.90" in P90_MS_PROMQL
    assert "0.99" in P99_MS_PROMQL


def test_scrape_once_handles_nan_quantiles_and_empty_metrics() -> None:
    fake = _FakeProm(
        {
            RPS_PROMQL: [{"value": [1700000000, "12.5"]}],
            CPU_MILLICORES_TOTAL_PROMQL: [{"value": [1700000000, "180.2"]}],
            MEM_BYTES_PROMQL: [],  # empty -> 0.0
            P50_MS_PROMQL: [{"value": [1700000000, "NaN"]}],  # idle -> None
            P90_MS_PROMQL: [{"value": [1700000000, "14.1"]}],
            P99_MS_PROMQL: [{"value": [1700000000, "34.6"]}],
        }
    )
    ts = align_epoch_to_step(1700000003.9, step_s=5)
    assert ts.second % 5 == 0
    row = scrape_once(
        fake,
        lambda: 3,
        ts=ts,
        profile="flash",
        run_id="run-test-01",
    )
    assert row.rps == 12.5
    assert row.cpu_millicores_total == 180.2
    assert row.mem_bytes == 0.0
    assert row.p50 is None
    assert row.p90 == 14.1
    assert row.p99 == 34.6
    assert row.ready_replicas == 3


def test_reconcile_time_grid_removes_duplicates_and_fills_gaps(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    # 30-minute run = 360 ticks at 5 s; drop 3 ticks (gaps) and duplicate tick 10.
    dropped_indices = {15, 16, 120}
    rows: list[TelemetryRow] = []
    for i in range(360):
        if i in dropped_indices:
            continue
        ts = t0 + timedelta(seconds=5 * i)
        rows.append(
            TelemetryRow(
                ts=ts,
                rps=20.0 + i * 0.1,
                cpu_millicores_total=150.0,
                mem_bytes=120_000_000.0,
                p50=8.0,
                p90=14.0,
                p99=30.0,
                ready_replicas=3,
                profile="gradual",
                run_id="run-30m",
            )
        )
    # Inject a duplicate timestamp with updated RPS
    rows.append(
        TelemetryRow(
            ts=t0 + timedelta(seconds=5 * 10),
            rps=99.0,
            cpu_millicores_total=150.0,
            mem_bytes=120_000_000.0,
            p50=8.0,
            p90=14.0,
            p99=30.0,
            ready_replicas=3,
            profile="gradual",
            run_id="run-30m",
        )
    )

    df = reconcile_time_grid(rows, step_s=5)
    assert len(df) == 360
    assert df["ts"].is_unique
    assert df["ts"].is_monotonic_increasing
    diffs = df["ts"].diff().dropna().dt.total_seconds()
    assert (diffs == 5.0).all()
    assert df["is_imputed"].sum() == len(dropped_indices)
    assert df.loc[10, "rps"] == 99.0

    pq = export_parquet(df, tmp_path / "run-30m.parquet")
    loaded = pd.read_parquet(pq)
    assert len(loaded) == 360
    assert list(loaded.columns) == list(df.columns)
