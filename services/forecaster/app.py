"""Phase 4, T4.5 — FastAPI conformalised demand forecasting service (`POST /predict`).

Loads a trained CQR LightGBM bundle (`ml/models/forecaster_bundle.joblib`),
computes causal features exclusively via `cloudpulse_core.features`, tracks
online Adaptive Conformal Inference (`cloudpulse_core.aci`), and returns
`{point, lower, upper, horizon_s, model_version, computed_at}`.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cloudpulse_core.aci import AdaptiveConformalPredictor
from cloudpulse_core.config import PROJECT_ROOT
from cloudpulse_core.features import MIN_HISTORY_ROWS, extract_latest_features
from ml.train import load_bundle

DEFAULT_MODEL_PATH = PROJECT_ROOT / "ml" / "models" / "forecaster_bundle.joblib"


class TelemetryPoint(BaseModel):
    ts: datetime
    rps: float = Field(ge=0.0)


class FeedbackPayload(BaseModel):
    actual_rps: float = Field(ge=0.0)
    predicted_lower: float = Field(ge=0.0)
    predicted_upper: float = Field(ge=0.0)
    q_lo: float | None = None
    q_hi: float | None = None


class PredictRequest(BaseModel):
    observations: list[TelemetryPoint] = Field(min_length=MIN_HISTORY_ROWS)
    feedback: FeedbackPayload | None = None


class PredictResponse(BaseModel):
    point: float
    lower: float
    upper: float
    horizon_s: int
    model_version: str
    computed_at: datetime


class ForecasterEngine:
    """In-memory inference & online ACI wrapper around a loaded CQR bundle."""

    def __init__(self, bundle: dict[str, Any]) -> None:
        self.bundle = bundle
        self.model_version = str(bundle["model_version"])
        self.horizon_s = int(bundle["horizon_s"])
        self.step_s = int(bundle["step_s"])
        self.aci = AdaptiveConformalPredictor(
            initial_scores=bundle["calib_scores"],
            alpha_target=float(bundle["alpha"]),
            gamma=float(bundle["aci_gamma"]),
        )

    def predict(self, observations: list[TelemetryPoint]) -> PredictResponse:
        window_df = pd.DataFrame(
            [{"ts": obs.ts, "rps": obs.rps} for obs in observations]
        )
        X_row = extract_latest_features(window_df, step_s=self.step_s).to_numpy(
            dtype=np.float64
        )
        q_lo = float(self.bundle["q_lo_model"].predict(X_row)[0])
        q_med = float(self.bundle["q_med_model"].predict(X_row)[0])
        q_hi = float(self.bundle["q_hi_model"].predict(X_row)[0])

        point, lower, upper = self.aci.predict_interval(q_lo, q_med, q_hi)
        return PredictResponse(
            point=round(point, 4),
            lower=round(lower, 4),
            upper=round(upper, 4),
            horizon_s=self.horizon_s,
            model_version=self.model_version,
            computed_at=datetime.now(timezone.utc),
        )

    def apply_feedback(self, fb: FeedbackPayload) -> float:
        return self.aci.update(
            fb.actual_rps,
            fb.predicted_lower,
            fb.predicted_upper,
            q_lo=fb.q_lo,
            q_hi=fb.q_hi,
        )


def create_app(
    model_path: Path | None = None,
    *,
    bundle: dict[str, Any] | None = None,
) -> FastAPI:
    """Create the FastAPI forecaster app from a bundle path or preloaded dict."""
    resolved_path = Path(
        model_path or os.getenv("MODEL_PATH", str(DEFAULT_MODEL_PATH))
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if bundle is not None:
            application.state.engine = ForecasterEngine(bundle)
        elif resolved_path.exists():
            application.state.engine = ForecasterEngine(load_bundle(resolved_path))
        else:
            application.state.engine = None
        yield

    application = FastAPI(
        title="CloudPulse Forecaster",
        version="1.0.0",
        lifespan=lifespan,
    )

    @application.get("/healthz")
    def healthz() -> dict[str, Any]:
        engine: ForecasterEngine | None = getattr(application.state, "engine", None)
        return {
            "status": "ok" if engine is not None else "model_missing",
            "model_loaded": engine is not None,
            "model_version": engine.model_version if engine is not None else None,
            "alpha_t": round(engine.aci.alpha_t, 5) if engine is not None else None,
        }

    @application.post("/predict", response_model=PredictResponse)
    def predict(req: PredictRequest) -> PredictResponse:
        engine: ForecasterEngine | None = getattr(application.state, "engine", None)
        if engine is None:
            raise HTTPException(
                status_code=503,
                detail=f"Forecaster model not loaded (expected {resolved_path})",
            )
        if req.feedback is not None:
            engine.apply_feedback(req.feedback)
        try:
            return engine.predict(req.observations)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/feedback")
    def feedback(fb: FeedbackPayload) -> dict[str, float]:
        engine: ForecasterEngine | None = getattr(application.state, "engine", None)
        if engine is None:
            raise HTTPException(status_code=503, detail="Forecaster model not loaded")
        alpha_t = engine.apply_feedback(fb)
        return {"alpha_t": round(alpha_t, 6)}

    return application


app = create_app()
