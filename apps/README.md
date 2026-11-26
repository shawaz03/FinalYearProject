# apps

Workloads that get scaled — not part of the scaler itself.

- `target-app/` — FastAPI service CloudPulse scales. Exposes `/`, `/healthz`,
  `/readyz`, `/metrics`. Implements `WARMUP_SECONDS`, the declared startup
  delay that Phase 7 sweeps (CLAUDE.md §3.6).

Built in Phase 1 (T1.1). Owner: M3.
