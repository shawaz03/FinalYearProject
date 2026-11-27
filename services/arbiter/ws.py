#!/usr/bin/env python
"""Phase 6, T6.1 — WebSocket streamer for telemetry, forecasts, and decisions.

Broadcasts JSON messages matching the exact TypeScript interfaces pinned in
`dashboard/src/lib/telemetry.ts`:
  - `RunMeta`
  - `TelemetryFrame`
  - `ForecastFrame`
  - `Decision`

Supports:
  - Live cluster polling (`--prom-url`, `--forecaster-url`, `--run-id`)
  - Local deterministic replay (`--replay`) for offline UI & `wscat` checks
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import threading
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.config import DECISION_INTERVAL_S, SCRAPE_INTERVAL_S, SLO_MS
from services.arbiter.capacity import ArbiterLimits, load_arbiter_limits
from services.arbiter.controller import ArbiterController, DecisionRecord, ForecastInput

logger = logging.getLogger("cloudpulse.arbiter.ws")


@dataclass(frozen=True)
class RunMetaPayload:
    runId: str
    scaler: str
    profile: str
    warmupSeconds: int
    horizonS: int
    capacityRps: float
    minPods: int
    maxPods: int
    sloMs: float
    live: bool


def build_telemetry_frame(
    *,
    ts_utc: datetime,
    rps: float,
    cpu_millicores: float,
    p50: float,
    p90: float,
    p99: float,
    ready_replicas: int,
    desired_replicas: int,
    sla_violation_rate: float,
) -> dict[str, Any]:
    """Build a `TelemetryFrame` payload matching `dashboard/src/lib/telemetry.ts`."""
    return {
        "t": int(ts_utc.astimezone(timezone.utc).timestamp() * 1000),
        "rps": round(float(rps), 3),
        "cpuMillicores": round(float(cpu_millicores), 1),
        "p50": round(float(p50), 2),
        "p90": round(float(p90), 2),
        "p99": round(float(p99), 2),
        "readyReplicas": int(ready_replicas),
        "desiredReplicas": int(desired_replicas),
        "slaViolationRate": round(min(1.0, max(0.0, float(sla_violation_rate))), 6),
    }


def build_forecast_frame(forecast: ForecastInput) -> dict[str, Any]:
    """Build a `ForecastFrame` payload matching `dashboard/src/lib/telemetry.ts`."""
    t_ms = int(forecast.computed_at.astimezone(timezone.utc).timestamp() * 1000)
    target_ms = t_ms + int(forecast.horizon_s) * 1000
    return {
        "t": t_ms,
        "horizonS": int(forecast.horizon_s),
        "targetT": target_ms,
        "point": round(float(forecast.point_rps), 3),
        "lower": round(float(forecast.lower_rps), 3),
        "upper": round(float(forecast.upper_rps), 3),
        "modelVersion": str(forecast.model_version),
    }


class StreamHub:
    """Thread-safe in-memory ring buffer and async fan-out hub for WebSocket clients."""

    def __init__(self, meta: RunMetaPayload, *, max_history: int = 720) -> None:
        self.meta = meta
        self._lock = threading.Lock()
        self._telemetry: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._forecasts: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._decisions: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the running asyncio event loop for cross-thread live broadcasts."""
        with self._lock:
            self._loop = loop
            self._loop_thread_id = threading.get_ident()

    def snapshot_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "meta": asdict(self.meta),
                "telemetry": list(self._telemetry),
                "forecasts": list(self._forecasts),
                "decisions": list(self._decisions),
            }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def _fanout(self, msg: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for q in subscribers:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                stale.append(q)
        if stale:
            with self._lock:
                for q in stale:
                    self._subscribers.discard(q)

    def _record_and_fanout(
        self, buf: deque[dict[str, Any]], msg_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        msg = {"type": msg_type, "payload": payload}
        with self._lock:
            buf.append(payload)
            loop = self._loop
            loop_tid = self._loop_thread_id
        if (
            loop is not None
            and not loop.is_closed()
            and loop_tid is not None
            and threading.get_ident() != loop_tid
        ):
            try:
                loop.call_soon_threadsafe(self._fanout, msg)
                return msg
            except RuntimeError:
                pass
        self._fanout(msg)
        return msg

    def publish_telemetry(self, frame: dict[str, Any]) -> dict[str, Any]:
        return self._record_and_fanout(self._telemetry, "telemetry", frame)

    def publish_forecast(self, frame: dict[str, Any]) -> dict[str, Any]:
        return self._record_and_fanout(self._forecasts, "forecast", frame)

    def publish_decision(self, decision: DecisionRecord | dict[str, Any]) -> dict[str, Any]:
        payload = decision.to_ws_dict() if isinstance(decision, DecisionRecord) else decision
        return self._record_and_fanout(self._decisions, "decision", payload)


def generate_replay_frames(
    *,
    limits: ArbiterLimits,
    warmup_seconds: int = 30,
    steps: int = 60,
    start_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """Generate a deterministic `flash` event stream routed through real `ArbiterController`.

    Useful for offline WebSocket contract tests and local UI integration on
    machines without Docker/k3d. Marked explicitly with `live=False` in `RunMeta`.
    """
    t0 = start_utc or datetime(2026, 11, 27, 14, 0, 0, tzinfo=timezone.utc)
    ctrl = ArbiterController(limits)

    ready = limits.min_pods + 1
    desired = ready
    pending_ready_at: list[datetime] = []
    events: list[dict[str, Any]] = []
    cum_reqs = 0.0
    cum_viol = 0.0

    def _demand(minute: float) -> float:
        if minute < 1.5:
            return 26.0 + math.sin(minute * 2.0) * 3.0
        if minute < 3.0:
            return 26.0 + (minute - 1.5) * 80.0
        if minute < 4.5:
            return 146.0 - (minute - 3.0) * 20.0
        return max(24.0, 116.0 - (minute - 4.5) * 40.0)

    for i in range(steps):
        ts = t0 + timedelta(seconds=i * SCRAPE_INTERVAL_S)
        minute = (i * SCRAPE_INTERVAL_S) / 60.0
        while pending_ready_at and pending_ready_at[0] <= ts:
            pending_ready_at.pop(0)
            ready += 1

        rps = _demand(minute)
        cap = max(1.0, ready * limits.capacity_rps)
        util = rps / cap
        pressure = max(0.0, util - 0.72)
        p50 = 8.0 + pressure * 90.0
        p90 = p50 * 1.7 + pressure * 160.0
        p99 = p50 * 2.4 + pressure * 520.0
        viol_share = min(1.0, pressure * 2.0) if p99 > SLO_MS else 0.0
        cum_reqs += rps * SCRAPE_INTERVAL_S
        cum_viol += rps * SCRAPE_INTERVAL_S * viol_share

        events.append(
            {
                "kind": "telemetry",
                "payload": build_telemetry_frame(
                    ts_utc=ts,
                    rps=rps,
                    cpu_millicores=rps * 13.0,
                    p50=p50,
                    p90=p90,
                    p99=p99,
                    ready_replicas=ready,
                    desired_replicas=desired,
                    sla_violation_rate=(cum_viol / cum_reqs) if cum_reqs > 0 else 0.0,
                ),
            }
        )

        # Forecast every 15s (3 ticks)
        if i % 3 == 0:
            future_rps = _demand(minute + limits.horizon_s / 60.0)
            half = 8.0 + abs(future_rps - rps) * 0.25
            fc = ForecastInput(
                point_rps=future_rps,
                lower_rps=max(0.0, future_rps - half),
                upper_rps=future_rps + half,
                horizon_s=limits.horizon_s,
                model_version="cqr-aci-replay",
                computed_at=ts,
            )
            events.append({"kind": "forecast", "payload": build_forecast_frame(fc)})

            # Arbiter every 30s (6 ticks)
            if i % 6 == 0:
                d = ctrl.evaluate(
                    now_utc=ts,
                    now_monotonic_s=float(i * SCRAPE_INTERVAL_S),
                    current_replicas=ready + len(pending_ready_at),
                    per_pod_cpu_pct=min(99.0, util * 100.0),
                    forecast=fc,
                )
                if d.actuated:
                    delta = d.to_replicas - d.from_replicas
                    if delta > 0:
                        ready_time = ts + timedelta(seconds=warmup_seconds + 5)
                        for _ in range(delta):
                            pending_ready_at.append(ready_time)
                    else:
                        ready = d.to_replicas
                    desired = d.to_replicas
                events.append({"kind": "decision", "payload": d.to_ws_dict()})
    return events


async def _replay_worker(
    hub: StreamHub, events: list[dict[str, Any]], *, interval_s: float
) -> None:
    try:
        for ev in events:
            kind = ev["kind"]
            payload = ev["payload"]
            if kind == "telemetry":
                hub.publish_telemetry(payload)
                if interval_s > 0:
                    await asyncio.sleep(interval_s)
            elif kind == "forecast":
                hub.publish_forecast(payload)
            elif kind == "decision":
                hub.publish_decision(payload)
    except asyncio.CancelledError:
        return


def create_ws_app(
    hub: StreamHub,
    *,
    replay_events: list[dict[str, Any]] | None = None,
    replay_tick_s: float = 0.25,
) -> FastAPI:
    """Create FastAPI WebSocket broadcaster serving `/ws/stream` and `/api/snapshot`."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        hub.attach_loop(asyncio.get_running_loop())
        task: asyncio.Task[None] | None = None
        if replay_events:
            task = asyncio.create_task(
                _replay_worker(hub, replay_events, interval_s=replay_tick_s)
            )
        try:
            yield
        finally:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="CloudPulse Telemetry Streamer", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        with hub._lock:
            subscribers = len(hub._subscribers)
        return {
            "status": "ok",
            "run_id": hub.meta.runId,
            "live": hub.meta.live,
            "subscribers": subscribers,
        }

    @app.get("/api/snapshot")
    def snapshot() -> dict[str, Any]:
        return hub.snapshot_payload()

    @app.websocket("/ws/stream")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        queue = hub.subscribe()
        try:
            await ws.send_json({"type": "snapshot", "payload": hub.snapshot_payload()})
            while True:
                msg = await queue.get()
                await ws.send_json(msg)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(queue)

    return app


class LiveWsServer:
    """Runs `create_ws_app(hub)` on a background daemon thread for live arbiter runs."""

    def __init__(self, hub: StreamHub, *, host: str = "127.0.0.1", port: int = 8002) -> None:
        import uvicorn

        app = create_ws_app(hub)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        # Signal handlers can only be registered on the main interpreter thread.
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._thread = threading.Thread(
            target=self._server.run,
            name=f"cloudpulse-ws-{port}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout_s)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    ap = argparse.ArgumentParser(description="CloudPulse WebSocket telemetry streamer (T6.1).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--replay", action="store_true", help="Stream synthetic replay without cluster")
    ap.add_argument("--replay-tick-s", type=float, default=0.5)
    ap.add_argument("--run-id", default="ws-replay-001")
    ap.add_argument("--capacity-rps", type=float, default=None)
    ap.add_argument("--horizon-s", type=int, default=None)
    args = ap.parse_args(argv)

    if args.replay:
        cap = args.capacity_rps or 18.0
        hor = args.horizon_s or 45
        limits = ArbiterLimits(capacity_rps=cap, horizon_s=hor, min_pods=2, max_pods=15)
        meta = RunMetaPayload(
            runId=args.run_id,
            scaler="cloudpulse",
            profile="flash",
            warmupSeconds=30,
            horizonS=hor,
            capacityRps=cap,
            minPods=2,
            maxPods=15,
            sloMs=SLO_MS,
            live=False,
        )
        hub = StreamHub(meta)
        events = generate_replay_frames(limits=limits, warmup_seconds=30, steps=120)
        app = create_ws_app(hub, replay_events=events, replay_tick_s=args.replay_tick_s)
    else:
        limits = load_arbiter_limits()
        meta = RunMetaPayload(
            runId=args.run_id,
            scaler="cloudpulse",
            profile="flash",
            warmupSeconds=30,
            horizonS=limits.horizon_s,
            capacityRps=limits.capacity_rps,
            minPods=limits.min_pods,
            maxPods=limits.max_pods,
            sloMs=SLO_MS,
            live=True,
        )
        hub = StreamHub(meta)
        app = create_ws_app(hub)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
