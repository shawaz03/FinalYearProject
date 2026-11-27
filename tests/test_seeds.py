"""Tests for global determinism and seed enforcement (T0.2.3)."""

from __future__ import annotations

import os
import random

import lightgbm as lgb
import numpy as np
import pytest

from cloudpulse_core.seeds import (
    DEFAULT_SEED,
    lightgbm_determinism_params,
    make_rng,
    set_global_seed,
)


def test_set_global_seed_rejects_invalid_values() -> None:
    for bad in (-1, True, "42", 1.5):
        with pytest.raises(ValueError):
            set_global_seed(bad)  # type: ignore[arg-type]


def test_set_global_seed_produces_identical_sequences() -> None:
    set_global_seed(DEFAULT_SEED)
    assert os.environ["PYTHONHASHSEED"] == str(DEFAULT_SEED)
    seq1 = ([random.random() for _ in range(5)], np.random.rand(5).tolist())

    set_global_seed(DEFAULT_SEED)
    seq2 = ([random.random() for _ in range(5)], np.random.rand(5).tolist())
    assert seq1 == seq2

    rng_a = make_rng(123).normal(size=8)
    rng_b = make_rng(123).normal(size=8)
    assert np.array_equal(rng_a, rng_b)


def test_lightgbm_models_are_byte_identical_across_runs() -> None:
    def train_bytes(seed: int) -> bytes:
        rng = make_rng(seed)
        x = rng.normal(size=(120, 4))
        y = 2.0 * x[:, 0] - 0.5 * x[:, 1] + rng.normal(scale=0.1, size=120)
        params = lightgbm_determinism_params(seed)
        model = lgb.LGBMRegressor(
            objective="quantile",
            alpha=0.95,
            n_estimators=20,
            verbosity=-1,
            **params,
        )
        model.fit(x, y)
        return model.booster_.model_to_string().encode("utf-8")

    assert train_bytes(42) == train_bytes(42)
