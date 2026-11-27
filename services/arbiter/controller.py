"""Phase 5, T5.2 — Stateful control layer (stabilisation, dwell, deadband, guard).

Implements CLAUDE.md §3.5:
  - Asymmetric response: scale up immediately; scale down only after a
    300 s stabilisation window (max of desired replicas over the window).
  - Minimum dwell time of 60 s between actuations (emergency reactive guard
    bypasses dwell/deadband when per-pod CPU > 85%).
  - Deadband: ignore non-emergency changes < 1 replica or < 10% of current.
  - Hard clamp to `[MIN_PODS, MAX_PODS]`.
  - Clean degraded fallback (`mode="degraded"`) when forecast is missing/stale.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from cloudpulse_core.config import (
    DEADBAND_RATIO,
    DECISION_INTERVAL_S,
    MIN_DWELL_S,
    SCALE_DOWN_WINDOW_S,
)
from services.arbiter.capacity import (
    ArbiterLimits,
    clamp_replicas,
    predictive_replicas_from_demand,
    reactive_guard_replicas,
)

DecisionMode = Literal[
    "predictive",
    "reactive",
    "hold",
    "stabilising",
    "clamped",
    "degraded",
]


@dataclass(frozen=True)
class ForecastInput:
    """Forecast snapshot consumed by the arbiter on one control tick."""

    point_rps: float
    lower_rps: float
    upper_rps: float
    horizon_s: int
    model_version: str
    computed_at: datetime


@dataclass(frozen=True)
class DecisionRecord:
    """Complete decision record matching both audit storage and WebSocket schema."""

    ts_utc: datetime
    mode: DecisionMode
    from_replicas: int
    to_replicas: int
    predictive_replicas: int
    reactive_replicas: int
    upper_bound_rps: float
    point_rps: float
    lower_rps: float
    capacity_rps: float
    per_pod_cpu_pct: float
    degraded: bool
    actuated: bool
    reason: str

    def to_ws_dict(self) -> dict[str, Any]:
        """Serialize to the exact `Decision` contract in `dashboard/src/lib/telemetry.ts`."""
        return {
            "t": int(self.ts_utc.timestamp() * 1000),
            "mode": self.mode,
            "fromReplicas": self.from_replicas,
            "toReplicas": self.to_replicas,
            "predictiveReplicas": self.predictive_replicas,
            "reactiveReplicas": self.reactive_replicas,
            "upperBound": round(self.upper_bound_rps, 3),
            "capacityRps": round(self.capacity_rps, 3),
            "reason": self.reason,
        }

    def to_log_dict(self) -> dict[str, Any]:
        """Serialize full input/output audit fields for JSONL / Postgres persistence."""
        payload = asdict(self)
        payload["ts_utc"] = self.ts_utc.isoformat()
        return payload


class ArbiterController:
    """Stateful hybrid predictive + reactive autoscaling controller."""

    def __init__(
        self,
        limits: ArbiterLimits,
        *,
        scale_down_window_s: int = SCALE_DOWN_WINDOW_S,
        min_dwell_s: int = MIN_DWELL_S,
        deadband_ratio: float = DEADBAND_RATIO,
        decision_interval_s: int = DECISION_INTERVAL_S,
        max_staleness_intervals: int = 2,
    ) -> None:
        if scale_down_window_s < 0 or min_dwell_s < 0 or decision_interval_s <= 0:
            raise ValueError("Timing parameters must be non-negative (interval > 0)")
        if not (0.0 <= deadband_ratio < 1.0):
            raise ValueError(f"deadband_ratio must be in [0, 1), got {deadband_ratio}")

        self.limits = limits
        self.scale_down_window_s = scale_down_window_s
        self.min_dwell_s = min_dwell_s
        self.deadband_ratio = deadband_ratio
        self.max_forecast_age_s = float(max_staleness_intervals * decision_interval_s)

        self._desired_history: deque[tuple[float, int]] = deque()
        self._first_tick_s: float | None = None
        self._last_actuation_s: float | None = None

    @property
    def last_actuation_s(self) -> float | None:
        return self._last_actuation_s

    def revert_actuation(self, previous_last_actuation_s: float | None) -> None:
        """Restore dwell timestamp if the Kubernetes patch failed to apply."""
        self._last_actuation_s = previous_last_actuation_s

    def _prune_window(self, now_s: float) -> None:
        cutoff = now_s - float(self.scale_down_window_s)
        while self._desired_history and self._desired_history[0][0] < cutoff:
            self._desired_history.popleft()

    def _within_deadband(self, current: int, target: int) -> bool:
        delta = abs(target - current)
        if delta < 1:
            return True
        if current <= 0:
            return False
        return (delta / float(current)) < self.deadband_ratio

    def _dwell_satisfied(self, now_s: float) -> bool:
        if self._last_actuation_s is None:
            return True
        return (now_s - self._last_actuation_s) >= float(self.min_dwell_s)

    def evaluate(
        self,
        *,
        now_utc: datetime,
        now_monotonic_s: float,
        current_replicas: int,
        per_pod_cpu_pct: float,
        forecast: ForecastInput | None = None,
        forecaster_error: str | None = None,
    ) -> DecisionRecord:
        """Compute one control decision without performing cluster I/O."""
        if self._first_tick_s is None:
            self._first_tick_s = now_monotonic_s

        # 1. Assess forecast health / staleness (T5.4.1)
        degraded = False
        degraded_reason = ""
        if forecaster_error:
            degraded = True
            degraded_reason = forecaster_error
        elif forecast is None:
            degraded = True
            degraded_reason = "forecaster prediction missing"
        else:
            age_s = (now_utc - forecast.computed_at).total_seconds()
            if age_s > self.max_forecast_age_s:
                degraded = True
                degraded_reason = (
                    f"forecast stale ({age_s:.1f}s > {self.max_forecast_age_s:.0f}s)"
                )
            elif not math.isfinite(forecast.upper_rps) or forecast.upper_rps < 0.0:
                degraded = True
                degraded_reason = f"invalid forecast upper bound ({forecast.upper_rps!r})"

        # 2. Compute raw predictive & reactive replica targets (T5.1, T5.2.1)
        reactive_target, reactive_triggered = reactive_guard_replicas(
            current_replicas,
            per_pod_cpu_pct,
            threshold_pct=self.limits.reactive_cpu_threshold_pct,
            increment=self.limits.reactive_increment,
        )

        if degraded or forecast is None:
            predictive_target = 0
            point_rps = 0.0
            lower_rps = 0.0
            upper_rps = 0.0
            unclamped = reactive_target
        else:
            predictive_target = predictive_replicas_from_demand(
                forecast.upper_rps, self.limits.capacity_rps
            )
            point_rps = float(forecast.point_rps)
            lower_rps = float(forecast.lower_rps)
            upper_rps = float(forecast.upper_rps)
            unclamped = (
                max(predictive_target, reactive_target)
                if reactive_triggered
                else predictive_target
            )

        desired, was_clamped = clamp_replicas(
            unclamped,
            min_pods=self.limits.min_pods,
            max_pods=self.limits.max_pods,
        )

        # Record desired count in trailing 300 s window before evaluating scale-down.
        self._desired_history.append((now_monotonic_s, desired))
        self._prune_window(now_monotonic_s)

        emergency_up = reactive_triggered and desired > current_replicas
        dwell_ok = self._dwell_satisfied(now_monotonic_s)

        # 3. Control layer: asymmetric scale-up / stabilised scale-down / dwell / deadband
        chosen = current_replicas
        mode: DecisionMode
        reason: str

        if current_replicas < self.limits.min_pods and desired >= self.limits.min_pods:
            # Always restore floor violations immediately
            chosen = desired
            mode = "degraded" if degraded else "clamped"
            reason = (
                f"{degraded_reason}; enforcing MIN_PODS={self.limits.min_pods}"
                if degraded
                else f"restoring MIN_PODS={self.limits.min_pods}"
            )
        elif desired > current_replicas:
            if emergency_up:
                chosen = desired
                if degraded:
                    mode = "degraded"
                    reason = (
                        f"{degraded_reason}; reactive guard {per_pod_cpu_pct:.1f}% > "
                        f"{self.limits.reactive_cpu_threshold_pct:.0f}% (+{self.limits.reactive_increment})"
                    )
                else:
                    mode = "clamped" if was_clamped else "reactive"
                    reason = (
                        f"reactive guard {per_pod_cpu_pct:.1f}% > "
                        f"{self.limits.reactive_cpu_threshold_pct:.0f}%"
                        + (" (clamped to MAX_PODS)" if was_clamped else "")
                    )
            elif not dwell_ok:
                chosen = current_replicas
                mode = "degraded" if degraded else "hold"
                reason = (
                    f"{degraded_reason}; min dwell {self.min_dwell_s}s active"
                    if degraded
                    else f"scale-up held by min dwell {self.min_dwell_s}s"
                )
            elif self._within_deadband(current_replicas, desired):
                chosen = current_replicas
                mode = "degraded" if degraded else "hold"
                reason = (
                    f"{degraded_reason}; within deadband"
                    if degraded
                    else f"within {int(self.deadband_ratio * 100)}% deadband"
                )
            else:
                chosen = desired
                if degraded:
                    mode = "degraded"
                    reason = f"{degraded_reason}; reactive scale-up"
                elif was_clamped:
                    mode = "clamped"
                    reason = f"ceil(upper/C) clamped to MAX_PODS={self.limits.max_pods}"
                else:
                    mode = "predictive"
                    reason = "ceil(upper / C)"
        elif desired < current_replicas:
            observed_span = now_monotonic_s - self._first_tick_s
            window_max = max(val for _, val in self._desired_history)
            stabilised = min(current_replicas, max(desired, window_max))

            if observed_span < float(self.scale_down_window_s):
                chosen = current_replicas
                mode = "degraded" if degraded else "stabilising"
                reason = (
                    f"scale-down window warming ({observed_span:.0f}s < {self.scale_down_window_s}s)"
                )
            elif stabilised >= current_replicas:
                chosen = current_replicas
                mode = "degraded" if degraded else "stabilising"
                reason = f"scale-down held by {self.scale_down_window_s}s max-window"
            elif not dwell_ok:
                chosen = current_replicas
                mode = "degraded" if degraded else "stabilising"
                reason = f"scale-down held by min dwell {self.min_dwell_s}s"
            elif self._within_deadband(current_replicas, stabilised):
                chosen = current_replicas
                mode = "degraded" if degraded else "hold"
                reason = f"scale-down within {int(self.deadband_ratio * 100)}% deadband"
            else:
                chosen = stabilised
                if degraded:
                    mode = "degraded"
                    reason = f"{degraded_reason}; stabilised reactive scale-down"
                elif was_clamped and chosen == self.limits.min_pods:
                    mode = "clamped"
                    reason = f"stabilised scale-down clamped to MIN_PODS={self.limits.min_pods}"
                else:
                    mode = "predictive"
                    reason = "stabilised ceil(upper / C) scale-down"
        else:
            chosen = current_replicas
            if degraded:
                mode = "degraded"
                reason = f"{degraded_reason}; reactive-only hold"
            elif was_clamped:
                mode = "clamped"
                reason = (
                    f"at MAX_PODS={self.limits.max_pods}"
                    if current_replicas == self.limits.max_pods
                    else f"at MIN_PODS={self.limits.min_pods}"
                )
            else:
                mode = "hold"
                reason = "desired equals current"

        actuated = chosen != current_replicas
        if actuated:
            self._last_actuation_s = now_monotonic_s

        return DecisionRecord(
            ts_utc=now_utc.astimezone(timezone.utc),
            mode=mode,
            from_replicas=current_replicas,
            to_replicas=chosen,
            predictive_replicas=predictive_target,
            reactive_replicas=reactive_target,
            upper_bound_rps=upper_rps,
            point_rps=point_rps,
            lower_rps=lower_rps,
            capacity_rps=self.limits.capacity_rps,
            per_pod_cpu_pct=float(per_pod_cpu_pct),
            degraded=degraded,
            actuated=actuated,
            reason=reason,
        )
