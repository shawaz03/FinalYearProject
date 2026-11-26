# ml

Offline training and evaluation. Nothing here runs in the live loop — the
trained artifact is loaded by `services/forecaster/`.

- `data/raw/` — telemetry parquet from `make collect` (gitignored)
- `data/processed/` — train / calibration / test splits (gitignored)
- `models/` — trained artifacts (gitignored; regenerate with `make train`)
- `notebooks/` — exploration only; anything load-bearing moves into a module

**Forecast target is demand (RPS), not CPU utilisation %.** CLAUDE.md §3.2
explains why utilisation is endogenous and must not be used.

Splits are chronological with a gap of >= H (T3.3.2). Phase 4. Owner: M1.
