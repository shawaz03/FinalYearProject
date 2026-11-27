"""Adaptive Conformal Inference (ACI) for non-exchangeable time series (T4.3.2).

Implements Gibbs & Candes (2021) online miscoverage tracking around CQR
conformity scores (`s_t = max(q_lo - y_t, y_t - q_hi)`):

    alpha_{t+1} = clip(alpha_t + gamma * (alpha_target - err_t), alpha_min, alpha_max)

Used identically in offline drift evaluation (`ml/evaluate.py`) and live
serving (`services/forecaster/app.py`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np


def cqr_conformity_score(y_true: float, q_lo: float, q_hi: float) -> float:
    """Signed CQR residual matching MAPIE's ConformalizedQuantileRegressor."""
    return float(max(q_lo - y_true, y_true - q_hi))


def finite_sample_conformal_quantile(
    scores: np.ndarray, alpha_t: float
) -> float:
    """Compute finite-sample conformal quantile from conformity scores.

    When drift drives `alpha_t` below `1 / (n + 1)` (more misses than the static
    calibration ceiling can absorb), scales smoothly beyond `max(scores)` rather
    than returning `+inf` so the Kubernetes arbiter never receives infinite RPS.
    """
    arr = np.asarray(scores, dtype=np.float64)
    if arr.size == 0:
        raise ValueError("Conformity score buffer cannot be empty")
    n = arr.size
    level = 1.0 - float(alpha_t)
    floor_alpha = 1.0 / (n + 1)

    if level <= 0.0:
        return float(np.min(arr))
    if alpha_t >= floor_alpha:
        q = min(1.0, (n + 1) * level / n)
        return float(np.quantile(arr, q, method="higher"))

    # Smooth finite widening when alpha_t < 1 / (n + 1)
    max_s = float(np.max(arr))
    span = max(1.0, float(np.max(arr) - np.min(arr)))
    deficit_ratio = (floor_alpha - alpha_t) / floor_alpha
    return float(max_s + deficit_ratio * span)


@dataclass
class AdaptiveConformalPredictor:
    """Online ACI state tracking rolling conformity scores and `alpha_t`."""

    initial_scores: Iterable[float]
    alpha_target: float = 0.10
    gamma: float = 0.08
    window_size: int = 40
    alpha_min: float = 0.001
    alpha_max: float = 0.50
    alpha_t: float = field(init=False)
    _scores: deque[float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not (0.0 < self.alpha_target < 1.0):
            raise ValueError(f"alpha_target must be in (0, 1), got {self.alpha_target}")
        if self.gamma <= 0.0:
            raise ValueError(f"gamma must be > 0, got {self.gamma}")
        init = [float(s) for s in self.initial_scores]
        if not init:
            raise ValueError("initial_scores must contain at least one score")
        self.alpha_t = float(self.alpha_target)
        self._scores = deque(init[-self.window_size :], maxlen=self.window_size)

    def current_margin(self) -> float:
        """Current symmetric CQR adjustment added to [q_lo, q_hi]."""
        return finite_sample_conformal_quantile(
            np.fromiter(self._scores, dtype=np.float64),
            self.alpha_t,
        )

    def predict_interval(
        self, q_lo: float, q_med: float, q_hi: float
    ) -> tuple[float, float, float]:
        """Apply current ACI margin and enforce non-negative ordered bounds."""
        margin = self.current_margin()
        point = max(0.0, float(q_med))
        lower = max(0.0, min(point, float(q_lo) - margin))
        upper = max(point, float(q_hi) + margin)
        return point, lower, upper

    def update(
        self,
        y_true: float,
        interval_lower: float,
        interval_upper: float,
        *,
        q_lo: float | None = None,
        q_hi: float | None = None,
    ) -> float:
        """Update `alpha_t` (and optional score window) from a realized sample."""
        err_t = 1.0 if (y_true < interval_lower or y_true > interval_upper) else 0.0
        next_alpha = self.alpha_t + self.gamma * (self.alpha_target - err_t)
        self.alpha_t = float(min(self.alpha_max, max(self.alpha_min, next_alpha)))
        if q_lo is not None and q_hi is not None:
            self._scores.append(cqr_conformity_score(y_true, q_lo, q_hi))
        return self.alpha_t
