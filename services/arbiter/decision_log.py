"""Phase 5, T5.5.1 — Decision persistence and replica timeline reconstruction."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from services.arbiter.actuator import ActuationResult
from services.arbiter.controller import DecisionRecord

DECISIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS arbiter_decisions (
    run_id            TEXT             NOT NULL,
    ts_utc            TIMESTAMPTZ      NOT NULL,
    mode              TEXT             NOT NULL,
    from_replicas     INTEGER          NOT NULL,
    to_replicas       INTEGER          NOT NULL,
    predictive_replicas INTEGER        NOT NULL,
    reactive_replicas INTEGER          NOT NULL,
    upper_bound_rps   DOUBLE PRECISION NOT NULL,
    point_rps         DOUBLE PRECISION NOT NULL,
    lower_rps         DOUBLE PRECISION NOT NULL,
    capacity_rps      DOUBLE PRECISION NOT NULL,
    per_pod_cpu_pct   DOUBLE PRECISION NOT NULL,
    degraded          BOOLEAN          NOT NULL,
    actuated          BOOLEAN          NOT NULL,
    actuation_success BOOLEAN,
    reason            TEXT             NOT NULL,
    PRIMARY KEY (run_id, ts_utc)
);
"""


def build_audit_entry(
    *,
    run_id: str,
    decision: DecisionRecord,
    actuation: ActuationResult | None = None,
) -> dict[str, Any]:
    """Combine a `DecisionRecord` with optional K8s actuation result."""
    entry = decision.to_log_dict()
    entry["run_id"] = run_id
    if actuation is not None:
        entry["actuation_success"] = actuation.success
        entry["actuation_attempts"] = actuation.attempts
        entry["actuation_error"] = actuation.error
        entry["actuation_status_code"] = actuation.status_code
    else:
        entry["actuation_success"] = None
        entry["actuation_attempts"] = 0
        entry["actuation_error"] = None
        entry["actuation_status_code"] = None
    return entry


class DecisionLogger:
    """Persists every arbiter decision to JSONL and optionally PostgreSQL."""

    def __init__(
        self,
        jsonl_path: Path,
        *,
        run_id: str,
        pg_dsn: str | None = None,
    ) -> None:
        self.jsonl_path = jsonl_path
        self.run_id = run_id
        self.pg_dsn = pg_dsn
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        if self.pg_dsn:
            self._ensure_pg_schema()

    def _ensure_pg_schema(self) -> None:
        import psycopg

        with psycopg.connect(self.pg_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(DECISIONS_SCHEMA_SQL)
            conn.commit()

    def record(
        self,
        decision: DecisionRecord,
        actuation: ActuationResult | None = None,
    ) -> dict[str, Any]:
        entry = build_audit_entry(
            run_id=self.run_id, decision=decision, actuation=actuation
        )
        with self.jsonl_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry) + "\n")

        if self.pg_dsn:
            self._upsert_pg(entry)
        return entry

    def _upsert_pg(self, entry: dict[str, Any]) -> None:
        import psycopg

        sql = """
            INSERT INTO arbiter_decisions (
                run_id, ts_utc, mode, from_replicas, to_replicas,
                predictive_replicas, reactive_replicas, upper_bound_rps,
                point_rps, lower_rps, capacity_rps, per_pod_cpu_pct,
                degraded, actuated, actuation_success, reason
            ) VALUES (
                %(run_id)s, %(ts_utc)s, %(mode)s, %(from_replicas)s, %(to_replicas)s,
                %(predictive_replicas)s, %(reactive_replicas)s, %(upper_bound_rps)s,
                %(point_rps)s, %(lower_rps)s, %(capacity_rps)s, %(per_pod_cpu_pct)s,
                %(degraded)s, %(actuated)s, %(actuation_success)s, %(reason)s
            )
            ON CONFLICT (run_id, ts_utc) DO UPDATE SET
                mode = EXCLUDED.mode,
                from_replicas = EXCLUDED.from_replicas,
                to_replicas = EXCLUDED.to_replicas,
                predictive_replicas = EXCLUDED.predictive_replicas,
                reactive_replicas = EXCLUDED.reactive_replicas,
                upper_bound_rps = EXCLUDED.upper_bound_rps,
                point_rps = EXCLUDED.point_rps,
                lower_rps = EXCLUDED.lower_rps,
                per_pod_cpu_pct = EXCLUDED.per_pod_cpu_pct,
                degraded = EXCLUDED.degraded,
                actuated = EXCLUDED.actuated,
                actuation_success = EXCLUDED.actuation_success,
                reason = EXCLUDED.reason;
        """
        row = dict(entry)
        row["ts_utc"] = datetime.fromisoformat(row["ts_utc"])
        with psycopg.connect(self.pg_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, row)
            conn.commit()


def load_decision_log(jsonl_path: Path) -> list[dict[str, Any]]:
    """Load persisted JSONL decision entries ordered chronologically."""
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Decision log not found: {jsonl_path}")
    entries = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    entries.sort(key=lambda e: e["ts_utc"])
    return entries


def reconstruct_replica_timeline(jsonl_path: Path) -> list[dict[str, Any]]:
    """Reconstruct the exact replica count timeline from a run's decision log (T5.5.1)."""
    entries = load_decision_log(jsonl_path)
    timeline: list[dict[str, Any]] = []
    for e in entries:
        applied = bool(e["actuated"]) and (e.get("actuation_success") is not False)
        effective = int(e["to_replicas"]) if applied else int(e["from_replicas"])
        timeline.append(
            {
                "ts_utc": e["ts_utc"],
                "from_replicas": int(e["from_replicas"]),
                "to_replicas": int(e["to_replicas"]),
                "effective_replicas": effective,
                "mode": e["mode"],
                "actuated": bool(e["actuated"]),
                "applied": applied,
                "degraded": bool(e["degraded"]),
                "reason": e["reason"],
            }
        )
    return timeline
