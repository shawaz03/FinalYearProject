"""Unit tests for Phase 6 Step 6.2 WebSocket broadcaster (T6.1)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from cloudpulse_core.config import SLO_MS
from services.arbiter.capacity import ArbiterLimits
from services.arbiter.controller import ForecastInput
from services.arbiter.ws import (
    RunMetaPayload,
    StreamHub,
    build_forecast_frame,
    build_telemetry_frame,
    create_ws_app,
    generate_replay_frames,
)


def _sample_meta(*, live: bool = False) -> RunMetaPayload:
    return RunMetaPayload(
        runId="test-ws-001",
        scaler="cloudpulse",
        profile="flash",
        warmupSeconds=30,
        horizonS=45,
        capacityRps=18.0,
        minPods=2,
        maxPods=15,
        sloMs=SLO_MS,
        live=live,
    )


def test_ws_frame_builders_match_dashboard_typescript_contract() -> None:
    ts = datetime(2026, 11, 27, 14, 0, 0, tzinfo=timezone.utc)
    tf = build_telemetry_frame(
        ts_utc=ts,
        rps=42.5,
        cpu_millicores=550.0,
        p50=8.1,
        p90=14.2,
        p99=34.6,
        ready_replicas=3,
        desired_replicas=4,
        sla_violation_rate=0.012,
    )
    assert set(tf.keys()) == {
        "t",
        "rps",
        "cpuMillicores",
        "p50",
        "p90",
        "p99",
        "readyReplicas",
        "desiredReplicas",
        "slaViolationRate",
    }

    ff = build_forecast_frame(
        ForecastInput(
            point_rps=55.0,
            lower_rps=45.0,
            upper_rps=68.0,
            horizon_s=45,
            model_version="cqr-aci-lgbm-1.0.0",
            computed_at=ts,
        )
    )
    assert set(ff.keys()) == {
        "t",
        "horizonS",
        "targetT",
        "point",
        "lower",
        "upper",
        "modelVersion",
    }
    assert ff["targetT"] - ff["t"] == 45_000


def test_ws_snapshot_and_live_replay_streaming() -> None:
    limits = ArbiterLimits(capacity_rps=18.0, horizon_s=45, min_pods=2, max_pods=15)
    events = generate_replay_frames(limits=limits, warmup_seconds=30, steps=18)
    hub = StreamHub(_sample_meta(live=False))
    app = create_ws_app(hub, replay_events=events, replay_tick_s=0.01)

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["run_id"] == "test-ws-001"

        with client.websocket_connect("/ws/stream") as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            assert first["payload"]["meta"]["runId"] == "test-ws-001"

            seen_types: set[str] = set()
            for _ in range(12):
                msg = ws.receive_json()
                seen_types.add(msg["type"])
                if {"telemetry", "forecast", "decision"}.issubset(seen_types):
                    break
            assert {"telemetry", "forecast", "decision"}.issubset(seen_types)

        snap = client.get("/api/snapshot")
        assert snap.status_code == 200
        assert len(snap.json()["telemetry"]) > 0


def test_stream_hub_live_ws_server_cross_thread_publish() -> None:
    import asyncio
    import json
    from urllib.request import urlopen
    import websockets

    from scripts.demo import find_free_port, wait_for_http
    from services.arbiter.controller import ArbiterController
    from services.arbiter.ws import LiveWsServer

    port = find_free_port()
    limits = ArbiterLimits(capacity_rps=18.0, horizon_s=45, min_pods=2, max_pods=15)
    hub = StreamHub(_sample_meta(live=True))
    server = LiveWsServer(hub, host="127.0.0.1", port=port)
    server.start()
    try:
        assert wait_for_http(f"http://127.0.0.1:{port}/healthz", timeout_s=15.0)
        ts = datetime(2026, 11, 27, 14, 1, 0, tzinfo=timezone.utc)
        ctrl = ArbiterController(limits)
        fc = ForecastInput(
            point_rps=60.0,
            lower_rps=48.0,
            upper_rps=74.0,
            horizon_s=45,
            model_version="live-test",
            computed_at=ts,
        )
        dec = ctrl.evaluate(
            now_utc=ts,
            now_monotonic_s=100.0,
            current_replicas=3,
            per_pod_cpu_pct=55.0,
            forecast=fc,
        )

        async def _consume() -> list[dict]:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/stream") as ws:
                snap = json.loads(await ws.recv())
                hub.publish_telemetry(
                    build_telemetry_frame(
                        ts_utc=ts,
                        rps=52.0,
                        cpu_millicores=650.0,
                        p50=9.0,
                        p90=16.0,
                        p99=32.0,
                        ready_replicas=3,
                        desired_replicas=5,
                        sla_violation_rate=0.0,
                    )
                )
                hub.publish_forecast(build_forecast_frame(fc))
                hub.publish_decision(dec)
                received = [snap]
                for _ in range(3):
                    received.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0)))
                return received

        msgs = asyncio.run(_consume())
        assert [m["type"] for m in msgs] == ["snapshot", "telemetry", "forecast", "decision"]
        assert msgs[0]["payload"]["meta"]["live"] is True
        with urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=3.0) as resp:
            snap_http = json.loads(resp.read().decode("utf-8"))
        assert len(snap_http["decisions"]) == 1
    finally:
        server.stop()

