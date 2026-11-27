# Datasets (`ml/data/`)

Phase 3 dataset specifications and resolution decisions (resolves D16, D17).

## 1. Primary Dataset — Live Cluster Telemetry (`raw/*.parquet`)

- **Source:** `services/collector/collector.py` scraping Prometheus (`deploy/queries.md`)
  and Kubernetes `status.readyReplicas` on the `cloudpulse` k3d cluster.
- **Native resolution:** **5 seconds** (`SCRAPE_INTERVAL_S = 5`), aligned to UTC 5 s ticks.
- **Forecast target:** `rps` (`D(t)` = aggregate 2xx offered requests/sec across pods,
  replica-independent; never CPU utilisation percent).
- **Collection protocol (`T3.2.1`):** Run `gradual`, `flash`, and `diurnal` × 3
  repetitions (`>= 6 hours` total = `>= 4,320` samples) at a **fixed** replica count
  (so demand is unconfounded by autoscaler actions).
  **Interleaving rule:** Run in 3 chronological rounds
  (`Round 1: gradual -> flash -> diurnal`, `Round 2: ...`, `Round 3: ...`) so the
  strict chronological split (`train | >= H gap | cal | >= H gap | test` in
  `ml/prepare_dataset.py`) contains all three traffic shapes in every split without
  temporal leakage.

## 2. Processed Splits (`processed/{train,cal,test}.parquet`)

- Generated deterministically from `raw/*.parquet` by `ml/prepare_dataset.py` using
  `cloudpulse_core/features.py` (shared verbatim with `services/forecaster/`).
- Enforces `max(train.ts) + H <= min(cal.ts)` and `max(cal.ts) + H <= min(test.ts)`.

## 3. Alibaba Cluster Trace Decision (`T3.2.2`)

- **Decision:** **Dropped from the primary training, calibration, and evaluation pipeline**
  (optional supplementary pre-training/exploratory reference only).
- **Why (`CLAUDE.md` §4 finding D16):**
  1. **Resolution mismatch:** Alibaba traces are aggregated at **1-minute (60 s)**
     intervals, whereas CloudPulse operates on a **5-second** control telemetry grid
     and forecasts `H` seconds ahead over sub-minute flash surges. Resampling 60 s
     averages down to 5 s fabricates 11 interpolated points per minute and destroys
     the true `dD/dt` leading edge.
  2. **Domain mismatch:** Alibaba records heterogeneous batch/service CPU/memory
     utilisation rather than our `target-app` HTTP `D(t)` RPS signal.
