#!/usr/bin/env python
"""Phase 5, T5.4 & T5.5 — Closed-loop CloudPulse Decision Arbiter runner.

On every control tick (default 30 s):
  1. Queries Prometheus for the trailing 5 s RPS history (`MIN_HISTORY_ROWS`)
     and per-pod CPU utilisation (`deploy/queries.md` #3).
  2. Reads authoritative `spec.replicas` from Kubernetes via `KubernetesScaleActuator`.
  3. Calls the forecaster (`POST /predict`) with a hard 2.0 s timeout,
     forwarding matured ACI feedback when past predictions reach `t + H`.
  4. On forecaster timeout, HTTP/network failure, or stale prediction (> 2
     intervals), degrades cleanly to reactive-only (`mode="degraded"`) without
     stopping and recovers automatically when the forecaster returns.
  5. Applies scale changes via `KubernetesScaleActuator` and logs every decision
     via `DecisionLogger`.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import DECISION_INTERVAL_S, PROJECT_ROOT, SCRAPE_INTERVAL_S
from cloudpulse_core.features import MIN_HISTORY_ROWS
from services.arbiter.actuator import ActuationResult, KubernetesScaleActuator
from services.arbiter.capacity import ArbiterLimits, load_arbiter_limits
from services.arbiter.controller import ArbiterController, DecisionRecord, ForecastInput
from services.arbiter.decision_log import DecisionLogger
from services.collector.promql import PER_POD_CPU_PCT_PROMQL, RPS_PROMQL

logger = logging.getLogger("cloudpulse.arbiter.loop")

MAX_POD_CPU_CORES_PROMQL = (
    'max(rate(container_cpu_usage_seconds_total{namespace="cloudpulse",container="target-app"}[1m]))'
)


class TelemetrySourceProtocol(Protocol):
    def read_rps_window(self, now_utc: datetime, n_rows: int) -> list[dict[str, Any]]: ...

    def read_per_pod_cpu_pct(self) -> float: ...


class PrometheusTelemetrySource:
    """Reads canonical Prometheus RPS history and per-pod CPU % (`queries.md`)."""

    def __init__(self, prom_url: str, *, cpu_limit_cores: float = 0.25) -> None:
        from prometheus_api_client import PrometheusConnect

        self.prom = PrometheusConnect(url=prom_url, disable_ssl=True)
        self.cpu_limit_cores = cpu_limit_cores

    def read_rps_window(
        self, now_utc: datetime, n_rows: int = MIN_HISTORY_ROWS
    ) -> list[dict[str, Any]]:
        step_s = SCRAPE_INTERVAL_S
        end_epoch = int(math.floor(now_utc.timestamp() / step_s) * step_s)
        start_epoch = end_epoch - (n_rows - 1) * step_s

        end_dt = datetime.fromtimestamp(end_epoch, tz=timezone.utc)
        start_dt = datetime.fromtimestamp(start_epoch, tz=timezone.utc)

        res = self.prom.custom_query_range(
            query=RPS_PROMQL,
            start_time=start_dt,
            end_time=end_dt,
            step=f"{step_s}s",
        )
        by_epoch: dict[int, float] = {}
        if res and "values" in res[0]:
            for ts_raw, val_raw in res[0]["values"]:
                v = float(val_raw)
                snapped = int(round(float(ts_raw) / step_s) * step_s)
                by_epoch[snapped] = v if math.isfinite(v) else 0.0

        observations: list[dict[str, Any]] = []
        last_val = 0.0
        for epoch in range(start_epoch, end_epoch + 1, step_s):
            if epoch in by_epoch:
                last_val = by_epoch[epoch]
            observations.append(
                {
                    "ts": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
                    "rps": last_val,
                }
            )
        return observations

    def read_per_pod_cpu_pct(self) -> float:
        res = self.prom.custom_query(query=PER_POD_CPU_PCT_PROMQL)
        if res:
            val = float(res[0]["value"][1])
            if math.isfinite(val):
                return max(0.0, val)
        fallback = self.prom.custom_query(query=MAX_POD_CPU_CORES_PROMQL)
        if fallback:
            cores = float(fallback[0]["value"][1])
            if math.isfinite(cores):
                return max(0.0, (cores / self.cpu_limit_cores) * 100.0)
        return 0.0


class HttpForecasterClient:
    """HTTP client calling `services/forecaster` `/predict` with a hard 2 s timeout."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 2.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = http_client or httpx.Client(timeout=timeout_s)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def predict(
        self,
        observations: list[dict[str, Any]],
        *,
        feedback: dict[str, Any] | None = None,
    ) -> ForecastInput:
        body: dict[str, Any] = {"observations": observations}
        if feedback is not None:
            body["feedback"] = feedback
        resp = self._client.post(
            f"{self.base_url}/predict",
            json=body,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        computed_at = datetime.fromisoformat(
            str(data["computed_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        return ForecastInput(
            point_rps=float(data["point"]),
            lower_rps=float(data["lower"]),
            upper_rps=float(data["upper"]),
            horizon_s=int(data["horizon_s"]),
            model_version=str(data["model_version"]),
            computed_at=computed_at,
        )


class ArbiterLoop:
    """Coordinates one closed-loop control tick and handles outage degradation."""

    def __init__(
        self,
        *,
        limits: ArbiterLimits,
        telemetry: TelemetrySourceProtocol,
        forecaster: HttpForecasterClient,
        actuator: KubernetesScaleActuator,
        decision_logger: DecisionLogger,
        controller: ArbiterController | None = None,
        dry_run: bool = False,
        on_forecast: Callable[[ForecastInput], None] | None = None,
        on_decision: Callable[[DecisionRecord, ActuationResult | None], None] | None = None,
        clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self.telemetry = telemetry
        self.forecaster = forecaster
        self.actuator = actuator
        self.decision_logger = decision_logger
        self.controller = controller or ArbiterController(limits)
        self.dry_run = dry_run
        self._on_forecast = on_forecast
        self._on_decision = on_decision
        self._now_utc = clock_fn
        self._monotonic = monotonic_fn
        self._pending_feedback: deque[tuple[datetime, float, float]] = deque(maxlen=64)

    def step(self) -> tuple[DecisionRecord, ActuationResult | None]:
        now_utc = self._now_utc().astimezone(timezone.utc)
        now_mono = self._monotonic()

        state = self.actuator.read_replica_state()
        cpu_pct = self.telemetry.read_per_pod_cpu_pct()
        observations = self.telemetry.read_rps_window(now_utc, MIN_HISTORY_ROWS)
        latest_rps = float(observations[-1]["rps"]) if observations else 0.0

        # Pop the most recently matured prediction (if t >= target_t) for ACI feedback.
        matured_feedback: dict[str, Any] | None = None
        while self._pending_feedback and self._pending_feedback[0][0] <= now_utc:
            _, p_lo, p_hi = self._pending_feedback.popleft()
            matured_feedback = {
                "actual_rps": latest_rps,
                "predicted_lower": p_lo,
                "predicted_upper": p_hi,
            }

        forecast: ForecastInput | None = None
        forecaster_error: str | None = None
        try:
            forecast = self.forecaster.predict(
                observations, feedback=matured_feedback
            )
            self._pending_feedback.append(
                (
                    forecast.computed_at + timedelta(seconds=forecast.horizon_s),
                    forecast.lower_rps,
                    forecast.upper_rps,
                )
            )
            if self._on_forecast is not None:
                self._on_forecast(forecast)
        except Exception as exc:
            forecaster_error = f"forecaster unavailable ({type(exc).__name__}: {exc})"
            logger.warning("Entering degraded reactive-only mode: %s", forecaster_error)

        prev_actuation_s = self.controller.last_actuation_s
        decision = self.controller.evaluate(
            now_utc=now_utc,
            now_monotonic_s=now_mono,
            current_replicas=state.desired_replicas,
            per_pod_cpu_pct=cpu_pct,
            forecast=forecast,
            forecaster_error=forecaster_error,
        )

        actuation: ActuationResult | None = None
        if decision.actuated and not self.dry_run:
            actuation = self.actuator.scale(decision.to_replicas)
            if not actuation.success:
                # Do not hold a 60s dwell penalty when the K8s API patch failed.
                self.controller.revert_actuation(prev_actuation_s)

        self.decision_logger.record(decision, actuation=actuation)
        if self._on_decision is not None:
            self._on_decision(decision, actuation)
        return decision, actuation


def run_loop(
    loop: ArbiterLoop,
    *,
    interval_s: int = DECISION_INTERVAL_S,
    max_ticks: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[tuple[DecisionRecord, ActuationResult | None]]:
    """Run closed-loop control until `max_ticks` or KeyboardInterrupt."""
    results: list[tuple[DecisionRecord, ActuationResult | None]] = []
    tick = 0
    while max_ticks is None or tick < max_ticks:
        t_start = time.monotonic()
        res = loop.step()
        results.append(res)
        tick += 1
        if max_ticks is not None and tick >= max_ticks:
            break
        elapsed = time.monotonic() - t_start
        remaining = interval_s - elapsed
        if remaining > 0:
            sleep_fn(remaining)
    return results


def _query_prom_scalar(prom: Any, query: str) -> float:
    res = prom.custom_query(query=query)
    if not res:
        return 0.0
    raw = res[0].get("value")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return 0.0
    val = float(raw[1])
    return val if math.isfinite(val) else 0.0


def _ws_telemetry_worker(
    *,
    stop_event: threading.Event,
    telemetry: PrometheusTelemetrySource,
    actuator: KubernetesScaleActuator,
    hub: Any,
    build_frame_fn: Callable[..., dict[str, Any]],
    step_s: int = SCRAPE_INTERVAL_S,
) -> None:
    """Poll Prometheus + K8s every `step_s` seconds and publish `TelemetryFrame`s."""
    from services.collector.promql import TELEMETRY_QUERIES

    while not stop_event.is_set():
        t_start = time.monotonic()
        try:
            now_utc = datetime.now(timezone.utc)
            state = actuator.read_replica_state()
            frame = build_frame_fn(
                ts_utc=now_utc,
                rps=_query_prom_scalar(telemetry.prom, TELEMETRY_QUERIES["rps"]),
                cpu_millicores=_query_prom_scalar(
                    telemetry.prom, TELEMETRY_QUERIES["cpu_millicores_total"]
                ),
                p50=_query_prom_scalar(telemetry.prom, TELEMETRY_QUERIES["p50"]),
                p90=_query_prom_scalar(telemetry.prom, TELEMETRY_QUERIES["p90"]),
                p99=_query_prom_scalar(telemetry.prom, TELEMETRY_QUERIES["p99"]),
                ready_replicas=state.ready_replicas,
                desired_replicas=state.desired_replicas,
                sla_violation_rate=0.0,
            )
            hub.publish_telemetry(frame)
        except Exception as exc:
            logger.debug("Live WS telemetry poll skipped: %s", exc)
        remaining = step_s - (time.monotonic() - t_start)
        if remaining > 0:
            stop_event.wait(remaining)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description="Run CloudPulse closed-loop Decision Arbiter.")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--prom-url", default="http://127.0.0.1:9090")
    ap.add_argument("--forecaster-url", default="http://127.0.0.1:8001")
    ap.add_argument("--interval", type=int, default=DECISION_INTERVAL_S)
    ap.add_argument("--max-ticks", type=int, default=None)
    ap.add_argument("--cpu-limit-cores", type=float, default=0.25)
    ap.add_argument("--pg-dsn", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profile", default="flash")
    ap.add_argument("--warmup-seconds", type=int, default=30)
    ap.add_argument(
        "--ws-port",
        type=int,
        default=None,
        help="Optional port to expose `/ws/stream`, `/api/snapshot`, and `/healthz`",
    )
    ap.add_argument("--ws-host", default="127.0.0.1")
    ap.add_argument(
        "--log-path",
        type=Path,
        default=None,
        help="Optional decisions.jsonl path (defaults to experiments/results/<run-id>/decisions.jsonl)",
    )
    args = ap.parse_args(argv)

    limits = load_arbiter_limits()
    log_path = args.log_path or (
        PROJECT_ROOT / "experiments" / "results" / args.run_id / "decisions.jsonl"
    )
    telemetry = PrometheusTelemetrySource(
        args.prom_url, cpu_limit_cores=args.cpu_limit_cores
    )
    forecaster = HttpForecasterClient(args.forecaster_url, timeout_s=2.0)
    actuator = KubernetesScaleActuator()
    dlogger = DecisionLogger(log_path, run_id=args.run_id, pg_dsn=args.pg_dsn)

    ws_server: Any = None
    ws_stop = threading.Event()
    ws_poller: threading.Thread | None = None
    on_forecast = None
    on_decision = None

    if args.ws_port is not None:
        from cloudpulse_core.config import SLO_MS
        from services.arbiter.ws import (
            LiveWsServer,
            RunMetaPayload,
            StreamHub,
            build_forecast_frame,
            build_telemetry_frame,
        )

        meta = RunMetaPayload(
            runId=args.run_id,
            scaler="cloudpulse",
            profile=args.profile,
            warmupSeconds=args.warmup_seconds,
            horizonS=limits.horizon_s,
            capacityRps=limits.capacity_rps,
            minPods=limits.min_pods,
            maxPods=limits.max_pods,
            sloMs=SLO_MS,
            live=True,
        )
        hub = StreamHub(meta)
        ws_server = LiveWsServer(hub, host=args.ws_host, port=args.ws_port)
        ws_server.start()
        on_forecast = lambda fc: hub.publish_forecast(build_forecast_frame(fc))
        on_decision = lambda dec, _act: hub.publish_decision(dec)
        ws_poller = threading.Thread(
            target=_ws_telemetry_worker,
            kwargs={
                "stop_event": ws_stop,
                "telemetry": telemetry,
                "actuator": actuator,
                "hub": hub,
                "build_frame_fn": build_telemetry_frame,
            },
            name="cloudpulse-ws-telemetry",
            daemon=True,
        )
        ws_poller.start()

    arb_loop = ArbiterLoop(
        limits=limits,
        telemetry=telemetry,
        forecaster=forecaster,
        actuator=actuator,
        decision_logger=dlogger,
        dry_run=args.dry_run,
        on_forecast=on_forecast,
        on_decision=on_decision,
    )
    try:
        run_loop(arb_loop, interval_s=args.interval, max_ticks=args.max_ticks)
    except KeyboardInterrupt:
        logger.info("Arbiter stopped cleanly by user.")
    finally:
        ws_stop.set()
        if ws_poller is not None:
            ws_poller.join(timeout=3.0)
        if ws_server is not None:
            ws_server.stop()
        forecaster.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
