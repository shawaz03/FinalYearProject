# Canonical PromQL

The authoritative query set. The collector, arbiter and dashboard all use
**these exact expressions** — copies that drift are how train/serve skew gets
in. Verified against a live cluster 2026-11-26; sample values are from a short
600-request burst across 3 pods.

Prometheus is at `http://prometheus.monitoring:9090` inside the cluster. From
the host: `kubectl port-forward -n monitoring svc/prometheus 9090:9090`.

---

## 1. Primary forecast signal — offered load (requests/sec)

```promql
sum(rate(http_requests_total{namespace="cloudpulse",status="2xx"}[1m]))
```

**This is what the model forecasts.** It is replica-independent: adding pods
does not change how many requests arrive. Never forecast CPU *percentage* —
see CLAUDE.md §3.2 for why that is both dimensionally wrong and endogenous.

Probe traffic is already excluded at the application, so this counts real work
only. Sample: `5.593`.

Per-pod breakdown, for checking that load actually spreads:

```promql
sum by (pod) (rate(http_requests_total{namespace="cloudpulse",status="2xx"}[1m]))
```

## 2. Secondary signal — total CPU millicores

```promql
sum(rate(container_cpu_usage_seconds_total{namespace="cloudpulse",container="target-app"}[1m])) * 1000
```

Also replica-independent: the *total* work being done, not the average
utilisation per pod. Sample: `182.978`.

`rate()` is mandatory — `container_cpu_usage_seconds_total` is a monotonic
counter, so its raw value is meaningless.

## 3. Per-pod utilisation — reactive guard only

```promql
max(
  rate(container_cpu_usage_seconds_total{namespace="cloudpulse",container="target-app"}[1m])
  / on(pod) group_left
  (container_spec_cpu_quota{namespace="cloudpulse",container="target-app"} / container_spec_cpu_period)
) * 100
```

Feeds the 85% emergency guard (T5.2.1) and **nothing else**. It must never
reach the forecaster.

## 4. Latency quantiles

```promql
histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="cloudpulse"}[1m]))) * 1000
histogram_quantile(0.90, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="cloudpulse"}[1m]))) * 1000
histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="cloudpulse"}[1m]))) * 1000
```

Samples: p50 `8.124`, p90 `14.108`, p99 `34.631` ms.

> **These are server-side and advisory.** Reported results use **client-side k6
> timings** (CLAUDE.md §3.5) — server histograms cannot see time spent queued
> before the server accepted the request.

> **Bucket warning.** The library default gives `http_request_duration_seconds`
> only three buckets (0.1, 0.5, 1.0). Every request this service serves lands
> in the first one, so `histogram_quantile` interpolates across 0–100 ms and
> reports a fixed p50 = 50 ms and p99 = 99 ms regardless of reality. Measured
> on this cluster: the fake p99 was 99 ms against a true 34.6 ms — a 2.9x
> error that looks entirely plausible. `LATENCY_BUCKETS` in
> `apps/target-app/app/main.py` replaces them. **Never remove that override.**

## 5. SLA violation rate (requests slower than 100 ms)

```promql
100 * (1 -
  sum(rate(http_request_duration_seconds_bucket{namespace="cloudpulse",le="0.1"}[1m]))
  /
  sum(rate(http_request_duration_seconds_count{namespace="cloudpulse"}[1m]))
)
```

Exact, not interpolated, because `0.1` is a real bucket boundary — which is why
it is pinned in `LATENCY_BUCKETS`. Sample: `0.0`.

## 6. Pod counts

```promql
count(up{job="cloudpulse-pods"} == 1)
```

Sample: `3.0`. Counts pods being **scraped**, which includes pods that are
running but not yet Ready.

> For the authoritative Ready-replica count the arbiter reads the Kubernetes
> API (`status.readyReplicas`) directly, rather than running kube-state-metrics
> for one number at a cost of ~150 MB. Phase 7 pod-seconds accounting uses the
> API value.

## 7. Memory

```promql
sum(container_memory_working_set_bytes{namespace="cloudpulse",container="target-app"}) / 1024 / 1024
```

---

## Scrape configuration

| Setting | Value | Why |
|---|---|---|
| `scrape_interval` | 5s | fine enough to watch a flash spike develop |
| retention | 2h | runs are ~15 min; Postgres is the system of record |
| retention size | 512MB | backstop against runaway cardinality |
| discovery | pod annotations | no operator, no CRDs (CLAUDE.md §3.7) |
| cAdvisor filtering | 2 series, `cloudpulse` ns only | keeps Prometheus near 300Mi instead of >1Gi |
