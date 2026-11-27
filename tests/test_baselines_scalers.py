"""Unit and integration tests for Phase 7 Step 7.1 (`T7.2.3–T7.2.5`, `T7.3`)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from cloudpulse_core.features import MIN_HISTORY_ROWS
from experiments.k6_summary import load_profile
from experiments.scalers import (
    AblationForecasterWrapper,
    NaivePredictiveClient,
    OracleForecasterClient,
    build_forecaster_for_experiment,
    compute_static_replicas,
    scheduled_demand_at,
)
from services.arbiter.actuator import KubernetesScaleActuator
from services.arbiter.capacity import ArbiterLimits
from services.arbiter.controller import ForecastInput
from services.arbiter.decision_log import DecisionLogger
from services.arbiter.loop import ArbiterLoop


class _StubForecaster:
    def __init__(self) -> None:
        self.seen_feedbacks: list[dict | None] = []

    def close(self) -> None:
        return None

    def predict(self, observations: list[dict], *, feedback: dict | None = None) -> ForecastInput:
        self.seen_feedbacks.append(feedback)
        return ForecastInput(
            point_rps=80.0,
            lower_rps=65.0,
            upper_rps=108.0,
            horizon_s=45,
            model_version="cqr-aci-test",
            computed_at=datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc),
        )


def test_static_replicas_sized_from_capacity_and_clamped() -> None:
    limits = ArbiterLimits(capacity_rps=18.0, horizon_s=45, min_pods=2, max_pods=15)
    # flash peak = 140 RPS -> ceil(140 / 18) = 8 pods
    assert compute_static_replicas("flash", limits=limits) == 8
    # Scaled 3x -> peak 420 RPS -> ceil(420 / 18) = 24 -> clamped to max_pods 15
    assert compute_static_replicas("flash", limits=limits, rps_scale=3.0) == 15


def test_naive_predictive_and_oracle_clients() -> None:
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    obs = [
        {"ts": (t0 + timedelta(seconds=5 * i)).isoformat(), "rps": 40.0}
        for i in range(MIN_HISTORY_ROWS)
    ]

    naive = NaivePredictiveClient(horizon_s=45, margin_ratio=0.25, clock_fn=lambda: t0)
    fc_naive = naive.predict(obs)
    assert fc_naive.point_rps == 40.0
    assert fc_naive.lower_rps == 30.0
    assert fc_naive.upper_rps == 50.0
    assert fc_naive.horizon_s == 45

    flash = load_profile("flash")
    # flash segments: 180s@20, 60s@40, 180s@140...
    assert scheduled_demand_at(flash, 100.0) == 20.0
    assert scheduled_demand_at(flash, 210.0) == 40.0
    assert scheduled_demand_at(flash, 260.0) == 140.0

    # Last observation is at run_start + 210s; with horizon=45s, oracle sees 255s -> 140 RPS!
    run_start = obs[-1]["ts"]
    start_dt = datetime.fromisoformat(run_start) - timedelta(seconds=210)
    oracle = OracleForecasterClient(
        flash, horizon_s=45, run_start_utc=start_dt, clock_fn=lambda: t0
    )
    fc_oracle = oracle.predict(obs)
    assert fc_oracle.point_rps == 140.0
    assert fc_oracle.lower_rps == 140.0
    assert fc_oracle.upper_rps == 140.0


def test_ablation_wrappers_and_arbiter_loop_integration(tmp_path: Path) -> None:
    stub = _StubForecaster()
    point_only = AblationForecasterWrapper(stub, ablation="point-only")
    fc_pt = point_only.predict(
        [{"ts": "2026-11-27T12:00:00+00:00", "rps": 80.0}],
        feedback={"actual_rps": 82.0, "predicted_lower": 65.0, "predicted_upper": 108.0},
    )
    assert fc_pt.point_rps == 80.0
    assert fc_pt.upper_rps == 80.0
    assert fc_pt.lower_rps == 80.0
    assert "[point-only]" in fc_pt.model_version
    assert stub.seen_feedbacks[-1] is not None

    static_cqr = AblationForecasterWrapper(stub, ablation="static-conformal")
    fc_sc = static_cqr.predict(
        [{"ts": "2026-11-27T12:00:00+00:00", "rps": 80.0}],
        feedback={"actual_rps": 82.0, "predicted_lower": 65.0, "predicted_upper": 108.0},
    )
    assert fc_sc.upper_rps == 108.0
    assert stub.seen_feedbacks[-1] is None

    # End-to-end ArbiterLoop step using OracleForecasterClient
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    limits = ArbiterLimits(capacity_rps=18.0, horizon_s=45, min_pods=2, max_pods=15)
    oracle = build_forecaster_for_experiment(
        scaler="oracle",
        profile="flash",
        limits=limits,
        run_start_utc=t0 - timedelta(seconds=210),
    )
    assert oracle is not None

    k8s_state = {"replicas": 2}

    class _FakeApps:
        def read_namespaced_deployment(self, _n: str, _ns: str):
            return SimpleNamespace(
                spec=SimpleNamespace(replicas=k8s_state["replicas"]),
                status=SimpleNamespace(ready_replicas=k8s_state["replicas"]),
            )

        def patch_namespaced_deployment_scale(self, _n: str, _ns: str, body: dict):
            k8s_state["replicas"] = int(body["spec"]["replicas"])
            return SimpleNamespace(spec=SimpleNamespace(replicas=k8s_state["replicas"]))

    class _FakeTelemetry:
        def read_rps_window(self, now_utc: datetime, n_rows: int = MIN_HISTORY_ROWS):
            return [
                {
                    "ts": (now_utc - timedelta(seconds=5 * (n_rows - 1 - i))).isoformat(),
                    "rps": 40.0,
                }
                for i in range(n_rows)
            ]

        def read_per_pod_cpu_pct(self) -> float:
            return 45.0

    loop = ArbiterLoop(
        limits=limits,
        telemetry=_FakeTelemetry(),
        forecaster=oracle,
        actuator=KubernetesScaleActuator(apps_api=_FakeApps()),
        decision_logger=DecisionLogger(tmp_path / "decisions.jsonl", run_id="oracle-test"),
        clock_fn=lambda: t0,
        monotonic_fn=lambda: 100.0,
    )
    decision, actuation = loop.step()
    # Oracle sees 140 RPS at 210+45=255s -> ceil(140 / 18) = 8 replicas pre-provisioned!
    assert decision.to_replicas == 8
    assert actuation is not None and actuation.success is True
    assert k8s_state["replicas"] == 8
