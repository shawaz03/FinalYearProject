"""Unit tests for Phase 5 Step 5.2 control layer (T5.2.1, T5.2.2, T5.2.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from services.arbiter.capacity import ArbiterLimits
from services.arbiter.controller import ArbiterController, ForecastInput


def _limits(c: float = 14.0) -> ArbiterLimits:
    return ArbiterLimits(
        capacity_rps=c,
        horizon_s=70,
        min_pods=2,
        max_pods=15,
    )


def _forecast(now: datetime, upper: float, *, age_s: float = 0.0) -> ForecastInput:
    return ForecastInput(
        point_rps=max(0.0, upper * 0.85),
        lower_rps=max(0.0, upper * 0.70),
        upper_rps=upper,
        horizon_s=70,
        model_version="cqr-aci-lgbm-1.0.0",
        computed_at=now - timedelta(seconds=age_s),
    )


def test_unpredicted_spike_triggers_reactive_guard_within_one_interval() -> None:
    ctrl = ArbiterController(_limits(14.0))
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)

    # First predictive scale-up sets last_actuation_s = 0.0
    d0 = ctrl.evaluate(
        now_utc=t0,
        now_monotonic_s=0.0,
        current_replicas=3,
        per_pod_cpu_pct=55.0,
        forecast=_forecast(t0, 50.0),  # ceil(50/14) = 4
    )
    assert d0.to_replicas == 4
    assert d0.mode == "predictive"

    # 30s later (inside 60s dwell!), an unpredicted CPU spike > 85% must STILL scale immediately
    d1 = ctrl.evaluate(
        now_utc=t0 + timedelta(seconds=30),
        now_monotonic_s=30.0,
        current_replicas=4,
        per_pod_cpu_pct=91.0,
        forecast=_forecast(t0 + timedelta(seconds=30), 50.0),
    )
    assert d1.to_replicas == 6
    assert d1.mode == "reactive"
    assert d1.actuated is True


def test_noisy_steady_load_produces_at_most_one_actuation_per_5_minutes() -> None:
    """T5.2.2 acceptance check: noisy steady predictions must not oscillate."""
    ctrl = ArbiterController(_limits(14.0))
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)

    # 30 minutes (60 ticks at 30 s) of noisy upper bounds oscillating across 3/4 and 12/13 boundaries
    rng = np.random.default_rng(42)
    # Regime A: steady ~42 RPS (bounces ceil(upper/14) between 3 and 4)
    current = 3
    actuations_steady_low = 0
    for step in range(30):  # 15 minutes
        now_s = step * 30.0
        now_utc = t0 + timedelta(seconds=now_s)
        noisy_upper = 42.0 + float(rng.uniform(-4.0, 4.0))
        d = ctrl.evaluate(
            now_utc=now_utc,
            now_monotonic_s=now_s,
            current_replicas=current,
            per_pod_cpu_pct=60.0,
            forecast=_forecast(now_utc, noisy_upper),
        )
        if d.actuated:
            actuations_steady_low += 1
            current = d.to_replicas

    # Over 15 minutes (= three 5-min windows), should see <= 1 actuation per 5 min (actually <= 1 total!)
    assert actuations_steady_low <= 1

    # Regime B: high steady ~168 RPS (12 pods) with +-6 RPS noise (12 vs 13 is 8.3% < 10% deadband)
    ctrl_hi = ArbiterController(_limits(14.0))
    current = 12
    actuations_steady_hi = 0
    for step in range(30):  # 15 minutes
        now_s = step * 30.0
        now_utc = t0 + timedelta(seconds=now_s)
        noisy_upper = 168.0 + float(rng.uniform(-6.0, 6.0))
        d = ctrl_hi.evaluate(
            now_utc=now_utc,
            now_monotonic_s=now_s,
            current_replicas=current,
            per_pod_cpu_pct=65.0,
            forecast=_forecast(now_utc, noisy_upper),
        )
        if d.actuated:
            actuations_steady_hi += 1
            current = d.to_replicas

    assert actuations_steady_hi == 0


def test_scale_down_waits_full_300s_stabilisation_window() -> None:
    ctrl = ArbiterController(_limits(14.0))
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)

    current = 8
    decisions = []
    # 12 ticks at 30s = 0..330s with demand wanting only 3 pods (ceil(40/14)=3)
    for step in range(12):
        now_s = step * 30.0
        now_utc = t0 + timedelta(seconds=now_s)
        d = ctrl.evaluate(
            now_utc=now_utc,
            now_monotonic_s=now_s,
            current_replicas=current,
            per_pod_cpu_pct=25.0,
            forecast=_forecast(now_utc, 40.0),
        )
        decisions.append((now_s, d))
        current = d.to_replicas

    # For t < 300s (0..270s), must hold at 8 in stabilising mode
    for now_s, d in decisions:
        if now_s < 300.0:
            assert d.to_replicas == 8
            assert d.mode == "stabilising"

    # Once the 300s window matures (t >= 300s), it scales down cleanly to 3
    assert current == 3
    final_scale_down = [d for now_s, d in decisions if now_s >= 300.0 and d.actuated]
    assert len(final_scale_down) == 1
    assert final_scale_down[0].to_replicas == 3


def test_scale_down_stabilises_300s_after_real_spike_leaves_window() -> None:
    ctrl = ArbiterController(_limits(14.0))
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    current = 3

    # t=0..60s: spike to ~105 RPS -> scales up to 8 pods
    for now_s in (0.0, 30.0, 60.0):
        now_utc = t0 + timedelta(seconds=now_s)
        d = ctrl.evaluate(
            now_utc=now_utc,
            now_monotonic_s=now_s,
            current_replicas=current,
            per_pod_cpu_pct=65.0,
            forecast=_forecast(now_utc, 105.0),
        )
        current = d.to_replicas
    assert current == 8

    # t=90..360s: demand drops to 40 RPS (3 pods), but t=60s (8) stays inside
    # [now_s - 300s, now_s] until now_s > 360s (i.e. t=390s)
    for step in range(3, 13):  # 90s .. 360s
        now_s = step * 30.0
        now_utc = t0 + timedelta(seconds=now_s)
        d = ctrl.evaluate(
            now_utc=now_utc,
            now_monotonic_s=now_s,
            current_replicas=current,
            per_pod_cpu_pct=25.0,
            forecast=_forecast(now_utc, 40.0),
        )
        assert d.to_replicas == 8
        assert d.mode == "stabilising"

    # At t=390s, cutoff = 90s prunes t=60s (8), so window max becomes 3 -> scales down!
    t_end = t0 + timedelta(seconds=390.0)
    d_down = ctrl.evaluate(
        now_utc=t_end,
        now_monotonic_s=390.0,
        current_replicas=current,
        per_pod_cpu_pct=25.0,
        forecast=_forecast(t_end, 40.0),
    )
    assert d_down.to_replicas == 3
    assert d_down.actuated is True
    assert d_down.mode == "predictive"


def test_decision_ws_dict_matches_dashboard_telemetry_contract() -> None:
    ctrl = ArbiterController(_limits(14.0))
    t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    d = ctrl.evaluate(
        now_utc=t0,
        now_monotonic_s=0.0,
        current_replicas=3,
        per_pod_cpu_pct=50.0,
        forecast=_forecast(t0, 300.0),  # ceil(300/14) = 22 -> clamped to 15
    )
    assert d.mode == "clamped"
    assert d.to_replicas == 15
    ws = d.to_ws_dict()
    assert set(ws.keys()) == {
        "t",
        "mode",
        "fromReplicas",
        "toReplicas",
        "predictiveReplicas",
        "reactiveReplicas",
        "upperBound",
        "capacityRps",
        "reason",
    }
    assert ws["toReplicas"] == 15
    assert ws["predictiveReplicas"] == 22
