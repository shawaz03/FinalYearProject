"""Phase 7, T7.2 & T7.3 — Baseline and ablation scaler predictors.

All predictive baselines and ablations conform to `ForecasterProtocol` so they
execute through the exact same `ArbiterController` and `KubernetesScaleActuator`
pipeline as CloudPulse:
  - `static` (T7.2.3): fixed replica count sized from Phase 2 `C` and profile peak.
  - `naive-predictive` (T7.2.4): `last_rps * (1 + margin)` without ML.
  - `oracle` (T7.2.5): exact scheduled future demand `D(t + H)` from profile JSON.
  - Ablations (T7.3): `point-only` (drops conformal margin), `static-conformal`
    (withholds online ACI feedback), `asymmetric-on` (uses `asymmetric_3_1` bundle).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final

from cloudpulse_core.config import SCRAPE_INTERVAL_S
from experiments.k6_summary import PROFILES_DIR, load_profile, validate_profile
from services.arbiter.capacity import (
    ArbiterLimits,
    clamp_replicas,
    predictive_replicas_from_demand,
)
from services.arbiter.controller import ForecastInput
from services.arbiter.loop import ForecasterProtocol, HttpForecasterClient

VALID_SCALERS: Final[tuple[str, ...]] = (
    "cloudpulse",
    "hpa-default",
    "hpa-tuned",
    "static",
    "naive-predictive",
    "oracle",
)

VALID_ABLATIONS: Final[tuple[str, ...]] = (
    "point-only",
    "static-conformal",
    "asymmetric-on",
)


def compute_static_replicas(
    profile: str,
    *,
    limits: ArbiterLimits,
    rps_scale: float = 1.0,
    profiles_dir: Path = PROFILES_DIR,
) -> int:
    """Compute fixed over-provisioned replica count from peak profile RPS and `C` (T7.2.3)."""
    if rps_scale <= 0.0:
        raise ValueError(f"rps_scale must be > 0, got {rps_scale!r}")
    data = load_profile(profile, profiles_dir=profiles_dir)
    summary = validate_profile(data)
    peak_rps = float(summary["peak_rps"]) * float(rps_scale)
    raw = predictive_replicas_from_demand(peak_rps, limits.capacity_rps)
    clamped, _ = clamp_replicas(raw, min_pods=limits.min_pods, max_pods=limits.max_pods)
    return clamped


class NaivePredictiveClient:
    """Non-ML baseline forecasting `last_rps * (1 + margin_ratio)` (T7.2.4)."""

    def __init__(
        self,
        *,
        horizon_s: int,
        margin_ratio: float = 0.20,
        clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if horizon_s <= 0:
            raise ValueError(f"horizon_s must be > 0, got {horizon_s!r}")
        if margin_ratio < 0.0:
            raise ValueError(f"margin_ratio must be >= 0, got {margin_ratio!r}")
        self.horizon_s = int(horizon_s)
        self.margin_ratio = float(margin_ratio)
        self._now_utc = clock_fn
        self.model_version = f"naive-last-plus-{int(round(self.margin_ratio * 100))}pct"

    def close(self) -> None:
        return None

    def predict(
        self,
        observations: list[dict[str, Any]],
        *,
        feedback: dict[str, Any] | None = None,
    ) -> ForecastInput:
        del feedback
        if not observations:
            raise ValueError("observations cannot be empty")
        last_rps = max(0.0, float(observations[-1]["rps"]))
        point = last_rps
        lower = max(0.0, point * (1.0 - self.margin_ratio))
        upper = point * (1.0 + self.margin_ratio)
        return ForecastInput(
            point_rps=round(point, 4),
            lower_rps=round(lower, 4),
            upper_rps=round(upper, 4),
            horizon_s=self.horizon_s,
            model_version=self.model_version,
            computed_at=self._now_utc().astimezone(timezone.utc),
        )


def scheduled_demand_at(
    profile_data: dict[str, Any],
    elapsed_s: float,
    *,
    rps_scale: float = 1.0,
    duration_scale: float = 1.0,
) -> float:
    """Evaluate exact piecewise-constant offered RPS at `elapsed_s` matching `k6_runner.js`."""
    if rps_scale <= 0.0 or duration_scale <= 0.0:
        raise ValueError("rps_scale and duration_scale must be > 0")
    segments = profile_data.get("segments") or []
    if not segments:
        raise ValueError("Profile has no segments")

    first_rate = float(max(1, round(float(segments[0]["target_rps"]) * rps_scale)))
    if elapsed_s <= 0.0:
        return first_rate

    cursor = 0.0
    last_rate = first_rate
    for seg in segments:
        dur = float(max(1, round(float(seg["duration_s"]) * duration_scale)))
        rate = float(max(1, round(float(seg["target_rps"]) * rps_scale)))
        last_rate = rate
        if elapsed_s < cursor + dur:
            return rate
        cursor += dur
    return last_rate


class OracleForecasterClient:
    """Idealised upper-bound forecaster returning exact `D(t + H)` (T7.2.5)."""

    def __init__(
        self,
        profile_data: dict[str, Any],
        *,
        horizon_s: int,
        run_start_utc: datetime,
        rps_scale: float = 1.0,
        duration_scale: float = 1.0,
        clock_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if horizon_s <= 0:
            raise ValueError(f"horizon_s must be > 0, got {horizon_s!r}")
        validate_profile(profile_data)
        self.profile_data = profile_data
        self.horizon_s = int(horizon_s)
        self.run_start_utc = run_start_utc.astimezone(timezone.utc)
        self.rps_scale = float(rps_scale)
        self.duration_scale = float(duration_scale)
        self._now_utc = clock_fn or (lambda: datetime.now(timezone.utc))
        self.model_version = f"oracle-{profile_data['profile']}"

    def close(self) -> None:
        return None

    def _current_ts(self, observations: list[dict[str, Any]]) -> datetime:
        if observations and "ts" in observations[-1]:
            raw_ts = str(observations[-1]["ts"]).replace("Z", "+00:00")
            return datetime.fromisoformat(raw_ts).astimezone(timezone.utc)
        return self._now_utc().astimezone(timezone.utc)

    def predict(
        self,
        observations: list[dict[str, Any]],
        *,
        feedback: dict[str, Any] | None = None,
    ) -> ForecastInput:
        del feedback
        now_utc = self._current_ts(observations)
        elapsed_s = max(0.0, (now_utc - self.run_start_utc).total_seconds())
        target_rps = scheduled_demand_at(
            self.profile_data,
            elapsed_s + float(self.horizon_s),
            rps_scale=self.rps_scale,
            duration_scale=self.duration_scale,
        )
        return ForecastInput(
            point_rps=round(target_rps, 4),
            lower_rps=round(target_rps, 4),
            upper_rps=round(target_rps, 4),
            horizon_s=self.horizon_s,
            model_version=self.model_version,
            computed_at=self._now_utc().astimezone(timezone.utc),
        )


class AblationForecasterWrapper:
    """Applies controlled Phase 7 ablations (`T7.3.1` / `T7.3.3`) over a forecaster client."""

    def __init__(self, inner: ForecasterProtocol, *, ablation: str) -> None:
        if ablation not in VALID_ABLATIONS:
            raise ValueError(
                f"Unknown ablation {ablation!r}; expected one of {VALID_ABLATIONS}"
            )
        self.inner = inner
        self.ablation = ablation

    def close(self) -> None:
        self.inner.close()

    def predict(
        self,
        observations: list[dict[str, Any]],
        *,
        feedback: dict[str, Any] | None = None,
    ) -> ForecastInput:
        forwarded_feedback = None if self.ablation == "static-conformal" else feedback
        fc = self.inner.predict(observations, feedback=forwarded_feedback)
        if self.ablation == "point-only":
            return ForecastInput(
                point_rps=fc.point_rps,
                lower_rps=fc.point_rps,
                upper_rps=fc.point_rps,
                horizon_s=fc.horizon_s,
                model_version=f"{fc.model_version}[point-only]",
                computed_at=fc.computed_at,
            )
        if self.ablation == "static-conformal":
            return ForecastInput(
                point_rps=fc.point_rps,
                lower_rps=fc.lower_rps,
                upper_rps=fc.upper_rps,
                horizon_s=fc.horizon_s,
                model_version=f"{fc.model_version}[static-conformal]",
                computed_at=fc.computed_at,
            )
        return fc


def build_forecaster_for_experiment(
    *,
    scaler: str,
    profile: str,
    limits: ArbiterLimits,
    run_start_utc: datetime,
    forecaster_url: str = "http://127.0.0.1:8001",
    ablation: str | None = None,
    rps_scale: float = 1.0,
    duration_scale: float = 1.0,
    naive_margin_ratio: float = 0.20,
    profiles_dir: Path = PROFILES_DIR,
    step_s: int = SCRAPE_INTERVAL_S,
) -> ForecasterProtocol | None:
    """Instantiate the predictor matching `scaler` + `ablation`, or `None` for HPA/static."""
    del step_s
    if scaler not in VALID_SCALERS:
        raise ValueError(f"Unknown scaler {scaler!r}; expected one of {VALID_SCALERS}")
    if ablation is not None and scaler != "cloudpulse":
        raise ValueError("Ablations apply only when scaler='cloudpulse'")

    if scaler in ("hpa-default", "hpa-tuned", "static"):
        return None
    if scaler == "naive-predictive":
        return NaivePredictiveClient(
            horizon_s=limits.horizon_s,
            margin_ratio=naive_margin_ratio,
        )
    if scaler == "oracle":
        pdata = load_profile(profile, profiles_dir=profiles_dir)
        return OracleForecasterClient(
            pdata,
            horizon_s=limits.horizon_s,
            run_start_utc=run_start_utc,
            rps_scale=rps_scale,
            duration_scale=duration_scale,
        )

    client: ForecasterProtocol = HttpForecasterClient(forecaster_url, timeout_s=2.0)
    if ablation is not None:
        return AblationForecasterWrapper(client, ablation=ablation)
    return client
