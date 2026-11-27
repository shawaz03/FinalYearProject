"""Global determinism and seed utilities for CloudPulse (Phase 0, T0.2.3).

Used by both offline training (`ml/`) and evaluation (`experiments/`) so runs
are reproducible and consecutive model trainings with the same seed are exact.
"""

from __future__ import annotations

import os
import random
from typing import Any, Final

import numpy as np

DEFAULT_SEED: Final[int] = 42


def set_global_seed(seed: int = DEFAULT_SEED) -> int:
    """Pin Python, OS hash, and NumPy RNG states and return the validated seed."""
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError(f"seed must be a non-negative integer, got {seed!r}")
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    return seed


def make_rng(seed: int = DEFAULT_SEED) -> np.random.Generator:
    """Return an isolated NumPy Generator seeded consistently."""
    set_global_seed(seed)
    return np.random.default_rng(seed)


def lightgbm_determinism_params(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Return LightGBM keyword args required for byte-identical determinism."""
    validated = set_global_seed(seed)
    return {
        "random_state": validated,
        "deterministic": True,
        "force_row_wise": True,
        "n_jobs": 1,
    }
