# target-app

The workload CloudPulse scales. Not a contribution — keep it boring and stable.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /` | identity + current config |
| `GET /api/work?n=` | **the endpoint under load.** Burns CPU; `n` overrides the iteration count for calibration |
| `GET /healthz` | liveness — alive? Never depends on warmup |
| `GET /readyz` | readiness — 503 until `WARMUP_SECONDS` elapses |
| `GET /metrics` | Prometheus scrape target |

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `WARMUP_SECONDS` | `0` | how long before the pod reports Ready. Swept in Phase 7 |
| `WORK_ITERATIONS` | `15000` | CPU work per request. Fixed per experiment, recorded in the manifest |
| `POD_NAME` | hostname | injected via the downward API |

Rough cost: 15 000 iterations ~= 13 ms CPU, so one pod sustains roughly 70 req/s
at full utilisation. **This is an estimate, not the capacity constant `C`** —
`C` is measured properly in Phase 2 (T2.2) against the real container.

## Build and run

```bash
docker build -t cloudpulse/target-app:0.1.0 .
docker run --rm -p 8000:8000 -e WARMUP_SECONDS=10 cloudpulse/target-app:0.1.0
```

Then `curl localhost:8000/readyz` — it returns 503 for 10 s, then 200.

## Design notes

- `/api/work` is a sync `def`, so FastAPI runs it in a threadpool and a saturated
  CPU cannot stall the health probes.
- Probe and metrics paths are excluded from instrumentation: kubelet probes
  every few seconds, and that traffic would inflate the RPS signal we forecast.
- `--workers 1`: one pod == one unit of capacity, so `C` stays unambiguous.
- Runs as non-root uid 10001.
