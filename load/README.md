# load

Traffic generation for every experiment (Phase 2, T2.1).

**k6 with the `constant-arrival-rate` executor — open loop.** Locust is
closed-loop by default: virtual users wait for a response before sending the
next request, so under overload the offered load quietly drops and the latency
damage we exist to measure disappears (coordinated omission). CLAUDE.md §3.5.

## Files

- `profiles/gradual.json` — 15 min progressive climb & unwind
- `profiles/flash.json` — 15 min calm warmup, sharp surge, plateau, recovery
- `profiles/diurnal.json` — 15 min compressed bimodal rush-hour waves
- `k6_runner.js` — reads a profile JSON (or `TARGET_RPS` / `DURATION_S` for
  constant-step calibration in T2.2) and runs chained `constant-arrival-rate`
  scenarios while recording client-side `p50`/`p90`/`p99` and `sla_violation_rate`

## Running on the 15.6 GB cluster laptop

1. Port-forward the target Service (and Prometheus for `T2.2`):
   ```powershell
   kubectl port-forward -n cloudpulse svc/target-app 8000:8000
   ```
2. Create the output directory first (`k6` does not mkdir parent folders):
   ```powershell
   New-Item -ItemType Directory -Force experiments/results/t21-flash-smoke | Out-Null
   k6 run -e TARGET_URL=http://127.0.0.1:8000 -e PROFILE=flash `
     -e K6_SUMMARY_OUT=experiments/results/t21-flash-smoke/k6_summary.json `
     load/k6_runner.js
   ```
3. Optional scaling overrides (for quick smoke checks or scaling after `T2.2`
   measures `C`): `RPS_SCALE` and `DURATION_SCALE`.

Latency is recorded **client-side**, here, not from server metrics.
Phase 2 (T2.1). Owner: M3.
