"""Canonical PromQL expressions from deploy/queries.md.

Single source of truth imported by `services/collector/` and `services/arbiter/`
so training and serving telemetry never drift (CLAUDE.md §2 rule 6).
"""

from __future__ import annotations

from typing import Final

# 1. Primary forecast signal — offered load in requests/sec (replica-independent).
RPS_PROMQL: Final[str] = (
    'sum(rate(http_requests_total{namespace="cloudpulse",status="2xx"}[1m]))'
)

# 2. Secondary signal — total CPU millicores across all target-app pods.
CPU_MILLICORES_TOTAL_PROMQL: Final[str] = (
    'sum(rate(container_cpu_usage_seconds_total{namespace="cloudpulse",container="target-app"}[1m])) * 1000'
)

# 3. Per-pod CPU utilisation % — feeds the 85% emergency reactive guard only.
PER_POD_CPU_PCT_PROMQL: Final[str] = (
    "max("
    'rate(container_cpu_usage_seconds_total{namespace="cloudpulse",container="target-app"}[1m])'
    " / on(pod) group_left "
    '(container_spec_cpu_quota{namespace="cloudpulse",container="target-app"}'
    ' / container_spec_cpu_period{namespace="cloudpulse",container="target-app"})'
    ") * 100"
)

# 4. Advisory server-side latency quantiles (milliseconds).
#    Reported benchmark results use client-side k6 timings (CLAUDE.md §3.5).
P50_MS_PROMQL: Final[str] = (
    'histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="cloudpulse"}[1m]))) * 1000'
)
P90_MS_PROMQL: Final[str] = (
    'histogram_quantile(0.90, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="cloudpulse"}[1m]))) * 1000'
)
P99_MS_PROMQL: Final[str] = (
    'histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="cloudpulse"}[1m]))) * 1000'
)

# 7. Total working-set memory in bytes (matches `telemetry.mem_bytes`).
MEM_BYTES_PROMQL: Final[str] = (
    'sum(container_memory_working_set_bytes{namespace="cloudpulse",container="target-app"})'
)

TELEMETRY_QUERIES: Final[dict[str, str]] = {
    "rps": RPS_PROMQL,
    "cpu_millicores_total": CPU_MILLICORES_TOTAL_PROMQL,
    "mem_bytes": MEM_BYTES_PROMQL,
    "p50": P50_MS_PROMQL,
    "p90": P90_MS_PROMQL,
    "p99": P99_MS_PROMQL,
}
