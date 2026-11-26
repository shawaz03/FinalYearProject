"""CloudPulse target application — the workload under test.

Deliberately simple. Its only jobs are:

1. Burn a *tunable, deterministic* amount of CPU per request, so that per-pod
   capacity (Phase 2, T2.2) is measurable and repeatable. Capacity is the
   constant `C` the arbiter divides by; if the work per request drifts, every
   downstream number is wrong.
2. Expose Prometheus metrics so the collector can observe offered load.
3. Simulate a slow-starting container via WARMUP_SECONDS, which Phase 7 sweeps
   to show how the predictive advantage depends on startup cost (CLAUDE.md §3.6).

Nothing here is a research contribution. Keep it boring.
"""

from __future__ import annotations

import hashlib
import os
import socket
import time
from typing import Final

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator, metrics


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# Seconds before this instance reports Ready. 0 = ready immediately.
WARMUP_SECONDS: Final[float] = _env_float("WARMUP_SECONDS", 0.0)

# CPU work per request. Fixed per experiment; recorded in the run manifest.
WORK_ITERATIONS: Final[int] = _env_int("WORK_ITERATIONS", 15_000)

# Populated from the Kubernetes downward API; useful for proving load spread.
POD_NAME: Final[str] = os.getenv("POD_NAME", socket.gethostname())

_STARTED_AT: Final[float] = time.monotonic()

app = FastAPI(title="CloudPulse target app", version="0.1.0")


def _uptime() -> float:
    return time.monotonic() - _STARTED_AT


def _is_warm() -> bool:
    return _uptime() >= WARMUP_SECONDS


def _burn(iterations: int) -> str:
    """Deterministic CPU-bound work.

    Repeated SHA-256 over its own output: cannot be cached, cannot be optimised
    away, and costs the same every time for a given iteration count.
    """
    digest = b"cloudpulse"
    for _ in range(iterations):
        digest = hashlib.sha256(digest).digest()
    return digest.hex()[:16]


@app.get("/")
def root() -> dict:
    return {
        "service": "cloudpulse-target-app",
        "pod": POD_NAME,
        "uptime_s": round(_uptime(), 2),
        "warmup_seconds": WARMUP_SECONDS,
        "work_iterations": WORK_ITERATIONS,
        "ready": _is_warm(),
    }


# NOTE: defined with `def`, not `async def`, on purpose. FastAPI runs sync
# handlers in a threadpool, so this CPU-bound call cannot block the event loop
# and stall the health probes under saturation.
@app.get("/api/work")
def work(n: int | None = Query(default=None, ge=0, le=5_000_000)) -> dict:
    """The endpoint under load. `n` overrides WORK_ITERATIONS for calibration."""
    iterations = WORK_ITERATIONS if n is None else n
    started = time.perf_counter()
    digest = _burn(iterations)
    return {
        "pod": POD_NAME,
        "iterations": iterations,
        "digest": digest,
        "compute_ms": round((time.perf_counter() - started) * 1000, 3),
    }


@app.get("/healthz")
def healthz() -> dict:
    """Liveness: is the process alive? Must NOT depend on warmup, or Kubernetes
    would kill the container before it ever finishes starting."""
    return {"status": "alive", "uptime_s": round(_uptime(), 2)}


@app.get("/readyz")
def readyz(response: Response) -> JSONResponse:
    """Readiness: may this pod receive traffic yet?

    Returns 503 until WARMUP_SECONDS has elapsed. This is the knob that makes
    the cold-start problem real and measurable instead of asserted.
    """
    warm = _is_warm()
    payload = {
        "status": "ready" if warm else "warming",
        "uptime_s": round(_uptime(), 2),
        "warmup_seconds": WARMUP_SECONDS,
        "remaining_s": max(0.0, round(WARMUP_SECONDS - _uptime(), 2)),
    }
    return JSONResponse(content=payload, status_code=200 if warm else 503)


# Latency histogram buckets, in seconds.
#
# The library default for http_request_duration_seconds is (0.1, 0.5, 1.0) —
# only three buckets. Every request we serve lands under 100 ms, so all of them
# fall in the first bucket and histogram_quantile() has nothing to work with:
# it interpolates linearly across 0–100 ms and reports p50 = 50 ms, p99 = 99 ms
# no matter what the real latencies are. Those are artifacts, not measurements.
#
# These buckets are dense between 5 ms and 300 ms, which is where this service
# actually lives, and place an exact boundary at 0.1 so the Phase 7 SLO
# ("requests slower than 100 ms") is counted rather than estimated.
#
# Client-side k6 timings remain the authority for reported results (§3.5);
# these drive the live dashboard and make it trustworthy.
LATENCY_BUCKETS: Final[tuple] = (
    0.005, 0.010, 0.015, 0.020, 0.030, 0.040, 0.050, 0.075,
    0.100,  # the SLO boundary — keep this one
    0.150, 0.200, 0.300, 0.500, 1.000, 2.500, 5.000,
    float("inf"),
)

# Probe and metrics endpoints are excluded: kubelet probes every few seconds,
# and that traffic would otherwise inflate the RPS signal we forecast on.
_instrumentator = Instrumentator(
    excluded_handlers=["/healthz", "/readyz", "/metrics"],
)
# Added explicitly so the buckets above replace the library defaults. Calling
# .add() before .instrument() suppresses the implicit default metric set.
_instrumentator.add(
    metrics.default(
        latency_highr_buckets=LATENCY_BUCKETS,
        latency_lowr_buckets=LATENCY_BUCKETS,
    )
)
_instrumentator.instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False
)
