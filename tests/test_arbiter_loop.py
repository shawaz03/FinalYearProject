"""Integration tests for Phase 5 Step 5.4: degradation, auto-recovery & decision logs (T5.4, T5.5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
from kubernetes.client.rest import ApiException

from cloudpulse_core.features import MIN_HISTORY_ROWS
from ml.prepare_dataset import prepare_from_raw
from ml.train import train_cqr_bundle
from services.arbiter.actuator import KubernetesScaleActuator
from services.arbiter.capacity import ArbiterLimits
from services.arbiter.decision_log import DecisionLogger, reconstruct_replica_timeline
from services.arbiter.loop import ArbiterLoop, HttpForecasterClient
from services.forecaster.app import create_app


class _SimulatedCluster:
    """In-memory mock K8s + Prometheus state tracking live replica transitions."""

    def __init__(self) -> None:
        self.replicas = 3
        self.ready = 3
        self.cpu_pct = 50.0
        self.rps = 25.0
        self.fail_next_patch = False
        self.replica_history: list[int] = []

    def read_namespaced_deployment(self, name: str, namespace: str):
        return SimpleNamespace(
            spec=SimpleNamespace(replicas=self.replicas),
            status=SimpleNamespace(ready_replicas=self.ready),
        )

    def patch_namespaced_deployment_scale(self, name: str, namespace: str, body: dict):
        if self.fail_next_patch:
            self.fail_next_patch = False
            raise ApiException(status=503, reason="Transient API blip")
        self.replicas = int(body["spec"]["replicas"])
        self.ready = self.replicas
        return SimpleNamespace(spec=SimpleNamespace(replicas=self.replicas))

    def read_rps_window(self, now_utc: datetime, n_rows: int = MIN_HISTORY_ROWS):
        return [
            {
                "ts": (now_utc - timedelta(seconds=5 * (n_rows - 1 - i))).isoformat(),
                "rps": self.rps,
            }
            for i in range(n_rows)
        ]

    def read_per_pod_cpu_pct(self) -> float:
        return self.cpu_pct


class _ToggleableForecasterTransport(httpx.BaseTransport):
    """Routes `/predict` to real FastAPI app unless `down=True` (simulates pod kill)."""

    def __init__(self, app) -> None:
        self._inner = httpx.MockTransport(
            lambda request: self._dispatch(request, app)
        )
        self.down = False
        self.force_stale = False

    def _dispatch(self, request: httpx.Request, app) -> httpx.Response:
        if self.down:
            raise httpx.ReadTimeout("Forecaster pod unreachable (>2s timeout)", request=request)
        # Forward synchronously via FastAPI TestClient
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.post("/predict", content=request.content, headers={"content-type": "application/json"})
        data = resp.json()
        if self.force_stale and resp.status_code == 200:
            stale_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
            data["computed_at"] = stale_ts.isoformat()
        return httpx.Response(resp.status_code, json=data)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._inner.handle_request(request)


def _build_forecaster_app(tmp_path: Path):
    rng = np.random.default_rng(11)
    t_base = datetime(2026, 11, 27, 8, 0, 0, tzinfo=timezone.utc)
    frames = []
    for r in range(6):
        start = t_base + timedelta(minutes=20 * r)
        ts = [start + timedelta(seconds=5 * i) for i in range(160)]
        t_idx = np.arange(160, dtype=np.float64)
        rps = 75.0 + 55.0 * np.sin(2.0 * np.pi * t_idx / 60.0) + rng.normal(scale=1.0, size=160)
        frames.append(
            pd.DataFrame(
                {
                    "ts": ts,
                    "rps": np.maximum(5.0, rps),
                    "cpu_millicores_total": 100.0,
                    "mem_bytes": 100_000_000.0,
                    "p50": 8.0,
                    "p90": 14.0,
                    "p99": 30.0,
                    "ready_replicas": 3,
                    "profile": "flash",
                    "run_id": f"arb-run-{r}",
                    "is_imputed": False,
                }
            )
        )
    raw_dir, proc_dir = tmp_path / "raw", tmp_path / "proc"
    raw_dir.mkdir()
    pd.concat(frames, ignore_index=True).to_parquet(raw_dir / "syn.parquet", index=False)
    train_df, cal_df, _ = prepare_from_raw(raw_dir, proc_dir, horizon_s=30, step_s=5)
    bundle = train_cqr_bundle(train_df, cal_df, horizon_s=30, seed=42, n_estimators=40)
    return create_app(bundle=bundle)


def test_forecaster_outage_degrades_to_reactive_and_recovers_automatically(
    tmp_path: Path,
) -> None:
    app = _build_forecaster_app(tmp_path)
    transport = _ToggleableForecasterTransport(app)
    http_client = httpx.Client(transport=transport, timeout=2.0)

    cluster = _SimulatedCluster()
    limits = ArbiterLimits(capacity_rps=14.0, horizon_s=30, min_pods=2, max_pods=15)
    actuator = KubernetesScaleActuator(
        apps_api=cluster, max_attempts=3, base_delay_s=0.0, sleep_fn=lambda _: None
    )
    log_path = tmp_path / "decisions.jsonl"
    dlogger = DecisionLogger(log_path, run_id="test-degradation")

    clock = {"utc": datetime(2026, 11, 27, 14, 0, 0, tzinfo=timezone.utc), "mono": 0.0}

    def tick_clock(delta_s: float = 30.0) -> None:
        clock["utc"] += timedelta(seconds=delta_s)
        clock["mono"] += delta_s

    # Use live wall-clock now_utc when talking to TestClient so computed_at age ~ 0s
    loop = ArbiterLoop(
        limits=limits,
        telemetry=cluster,
        forecaster=HttpForecasterClient("http://forecaster.local", timeout_s=2.0, http_client=http_client),
        actuator=actuator,
        decision_logger=dlogger,
        clock_fn=lambda: datetime.now(timezone.utc),
        monotonic_fn=lambda: clock["mono"],
    )

    observed_replicas: list[int] = []

    # Tick 1 (healthy predictive scale-up under high RPS)
    cluster.rps = 90.0
    cluster.cpu_pct = 65.0
    d1, _ = loop.step()
    observed_replicas.append(cluster.replicas)
    assert d1.degraded is False
    assert d1.mode == "predictive"
    assert cluster.replicas >= 6

    # Tick 2 & 3: Kill forecaster pod! CPU spikes > 85% -> arbiter degrades and reactively adds +2
    tick_clock()
    transport.down = True
    cluster.cpu_pct = 92.0
    d2, _ = loop.step()
    observed_replicas.append(cluster.replicas)
    assert d2.degraded is True
    assert d2.mode == "degraded"
    assert d2.to_replicas == d2.from_replicas + 2
    assert cluster.replicas == d2.to_replicas

    # Tick 4: Forecaster returns with a STALE prediction (> 60s old) -> must still stay degraded
    tick_clock()
    transport.down = False
    transport.force_stale = True
    cluster.cpu_pct = 60.0
    d3, _ = loop.step()
    observed_replicas.append(cluster.replicas)
    assert d3.degraded is True
    assert "stale" in d3.reason

    # Tick 5: Fresh forecaster restored -> automatic recovery without restart!
    tick_clock()
    transport.force_stale = False
    cluster.rps = 140.0
    d4, _ = loop.step()
    observed_replicas.append(cluster.replicas)
    assert d4.degraded is False
    assert d4.mode in {"predictive", "hold", "stabilising", "clamped"}

    http_client.close()

    # T5.5.1: Decision log must reconstruct the exact replica timeline
    timeline = reconstruct_replica_timeline(log_path)
    assert len(timeline) == 4
    assert [row["effective_replicas"] for row in timeline] == observed_replicas
    assert timeline[1]["degraded"] is True
    assert timeline[3]["degraded"] is False


def test_failed_k8s_patch_reverts_dwell_and_records_effective_replicas(
    tmp_path: Path,
) -> None:
    app = _build_forecaster_app(tmp_path)
    transport = _ToggleableForecasterTransport(app)
    http_client = httpx.Client(transport=transport, timeout=2.0)

    cluster = _SimulatedCluster()
    cluster.rps = 90.0
    cluster.cpu_pct = 65.0

    limits = ArbiterLimits(capacity_rps=14.0, horizon_s=30, min_pods=2, max_pods=15)
    # Only 1 attempt so ApiException fails fast cleanly in this test
    actuator = KubernetesScaleActuator(
        apps_api=cluster, max_attempts=1, base_delay_s=0.0, sleep_fn=lambda _: None
    )
    log_path = tmp_path / "decisions_fail.jsonl"
    dlogger = DecisionLogger(log_path, run_id="test-fail-revert")
    clock = {"mono": 0.0}

    loop = ArbiterLoop(
        limits=limits,
        telemetry=cluster,
        forecaster=HttpForecasterClient("http://forecaster.local", timeout_s=2.0, http_client=http_client),
        actuator=actuator,
        decision_logger=dlogger,
        clock_fn=lambda: datetime.now(timezone.utc),
        monotonic_fn=lambda: clock["mono"],
    )

    # First patch fails -> effective replicas stays 3 and dwell is reverted
    cluster.fail_next_patch = True
    d1, act1 = loop.step()
    assert d1.actuated is True
    assert act1 is not None and act1.success is False
    assert cluster.replicas == 3

    # 30s later (< 60s dwell!), retry succeeds immediately because dwell was reverted
    clock["mono"] = 30.0
    d2, act2 = loop.step()
    assert d2.actuated is True
    assert act2 is not None and act2.success is True
    assert cluster.replicas == d2.to_replicas

    http_client.close()

    timeline = reconstruct_replica_timeline(log_path)
    assert [row["effective_replicas"] for row in timeline] == [3, d2.to_replicas]
    assert timeline[0]["applied"] is False
    assert timeline[1]["applied"] is True

