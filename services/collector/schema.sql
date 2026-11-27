-- Phase 3, T3.1.1 — PostgreSQL 16 telemetry schema.
-- Primary key (run_id, ts) guarantees idempotent writes with zero duplicate
-- timestamps per run across retries or backfill reconciliation.

CREATE TABLE IF NOT EXISTS telemetry (
    ts                   TIMESTAMPTZ      NOT NULL,
    rps                  DOUBLE PRECISION NOT NULL,
    cpu_millicores_total DOUBLE PRECISION NOT NULL,
    mem_bytes            DOUBLE PRECISION NOT NULL,
    p50                  DOUBLE PRECISION,
    p90                  DOUBLE PRECISION,
    p99                  DOUBLE PRECISION,
    ready_replicas       INTEGER          NOT NULL,
    profile              TEXT             NOT NULL,
    run_id               TEXT             NOT NULL,
    is_imputed           BOOLEAN          NOT NULL DEFAULT FALSE,
    PRIMARY KEY (run_id, ts)
);

CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry (ts);
CREATE INDEX IF NOT EXISTS idx_telemetry_profile_ts ON telemetry (profile, ts);
