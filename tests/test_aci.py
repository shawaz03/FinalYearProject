"""Unit tests for Adaptive Conformal Inference (Phase 4, T4.3.2)."""

from __future__ import annotations

import numpy as np
import pytest

from cloudpulse_core.aci import (
    AdaptiveConformalPredictor,
    cqr_conformity_score,
    finite_sample_conformal_quantile,
)


def test_aci_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="alpha_target"):
        AdaptiveConformalPredictor([1.0], alpha_target=1.2)
    with pytest.raises(ValueError, match="gamma"):
        AdaptiveConformalPredictor([1.0], gamma=0.0)
    with pytest.raises(ValueError, match="at least one"):
        AdaptiveConformalPredictor([])


def test_aci_bounds_non_negative_and_ordered() -> None:
    aci = AdaptiveConformalPredictor([1.0, 2.0, 3.0], alpha_target=0.10, gamma=0.05)
    point, lo, hi = aci.predict_interval(q_lo=0.5, q_med=2.0, q_hi=4.0)
    assert 0.0 <= lo <= point <= hi


def test_aci_widens_under_shift_and_recovers_within_20_steps() -> None:
    rng = np.random.default_rng(7)
    init_scores = np.abs(rng.normal(loc=0.0, scale=1.5, size=80))
    aci = AdaptiveConformalPredictor(
        initial_scores=init_scores, alpha_target=0.10, gamma=0.10, window_size=120
    )

    # Sudden +25 RPS regime shift where base quantile models underestimate by 25
    hits = []
    for _ in range(40):
        q_lo, q_med, q_hi = 18.0, 20.0, 22.0
        y_true = 45.0 + float(rng.normal(scale=1.0))
        _, lo, hi = aci.predict_interval(q_lo, q_med, q_hi)
        hits.append(1.0 if (lo <= y_true <= hi) else 0.0)
        aci.update(y_true, lo, hi, q_lo=q_lo, q_hi=q_hi)

    assert aci.alpha_t < 0.10
    # Must start recovering within <= 20 steps and achieve high post-adaptation coverage
    assert any(hits[:20])
    assert np.mean(hits[15:]) >= 0.85


def test_finite_sample_quantile_never_returns_inf() -> None:
    scores = np.array([1.0, 2.0, 3.0, 4.0])
    q = finite_sample_conformal_quantile(scores, alpha_t=0.001)
    assert np.isfinite(q)
    assert q >= 4.0
    assert cqr_conformity_score(10.0, 5.0, 8.0) == 2.0
