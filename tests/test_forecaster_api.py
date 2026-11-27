"""Contract and latency tests for FastAPI /predict service (Phase 4, T4.5)."""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from cloudpulse_core.features import MIN_HISTORY_ROWS
from ml.prepare_dataset import prepare_from_raw
from ml.train import train_cqr_bundle
from services.forecaster.app import create_app


def _build_test_bundle(tmp_path: Path) -> dict:
    rng = np.random.default_rng(7)
    t_base = datetime(2026, 11, 27, 9, 0, 0, tzinfo=timezone.utc)
    frames = []
    for r in range(6):
        start = t_base + timedelta(minutes=20 * r)
        ts = [start + timedelta(seconds=5 * i) for i in range(160)]
        t_idx = np.arange(160, dtype=np.float64)
        rps = 30.0 + 15.0 * np.sin(2.0 * np.pi * t_idx / 60.0) + rng.normal(scale=1.0, size=160)
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
                    "profile": "gradual",
                    "run_id": f"api-run-{r}",
                    "is_imputed": False,
                }
            )
        )
    raw_dir = tmp_path / "raw"
    proc_dir = tmp_path / "processed"
    raw_dir.mkdir()
    pd.concat(frames, ignore_index=True).to_parquet(raw_dir / "api.parquet", index=False)
    train_df, cal_df, _ = prepare_from_raw(raw_dir, proc_dir, horizon_s=30, step_s=5)
    return train_cqr_bundle(train_df, cal_df, horizon_s=30, seed=42, n_estimators=40)


def _sample_observations(n: int = MIN_HISTORY_ROWS) -> list[dict]:
    t0 = datetime(2026, 11, 27, 15, 0, 0, tzinfo=timezone.utc)
    return [
        {
            "ts": (t0 + timedelta(seconds=5 * i)).isoformat(),
            "rps": 30.0 + 0.5 * i,
        }
        for i in range(n)
    ]


def test_forecaster_api_contract_feedback_and_1000_call_latency(tmp_path: Path) -> None:
    bundle = _build_test_bundle(tmp_path)
    app = create_app(bundle=bundle)
    payload = {"observations": _sample_observations(MIN_HISTORY_ROWS)}

    with TestClient(app) as client:
        h = client.get("/healthz")
        assert h.status_code == 200
        assert h.json()["model_loaded"] is True

        # Reject short history
        bad = client.post("/predict", json={"observations": _sample_observations(10)})
        assert bad.status_code == 422

        # Valid prediction contract (T4.5.1)
        res = client.post("/predict", json=payload)
        assert res.status_code == 200
        body = res.json()
        for key in ("point", "lower", "upper", "horizon_s", "model_version", "computed_at"):
            assert key in body
        assert 0.0 <= body["lower"] <= body["point"] <= body["upper"]
        assert body["horizon_s"] == 30

        # Online ACI feedback shifts alpha_t on miss
        fb = client.post(
            "/feedback",
            json={
                "actual_rps": body["upper"] + 50.0,
                "predicted_lower": body["lower"],
                "predicted_upper": body["upper"],
            },
        )
        assert fb.status_code == 200
        assert fb.json()["alpha_t"] < 0.10

        # T4.5.1 latency check: 1000 calls with p99 < 200 ms
        latencies_ms = []
        for _ in range(1000):
            t_start = time.perf_counter()
            r = client.post("/predict", json=payload)
            assert r.status_code == 200
            latencies_ms.append((time.perf_counter() - t_start) * 1000.0)

        p99_ms = statistics.quantiles(latencies_ms, n=100)[98]
        assert p99_ms < 200.0, f"Forecaster /predict p99={p99_ms:.2f}ms >= 200ms"
