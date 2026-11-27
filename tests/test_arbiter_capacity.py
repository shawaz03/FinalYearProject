"""Unit tests for Phase 5 Step 5.1: capacity conversion and bounds clamping (T5.1, T5.2.3)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from services.arbiter.capacity import (
    clamp_replicas,
    load_arbiter_limits,
    parse_max_pods_from_limits_md,
    predictive_replicas_from_demand,
    reactive_guard_replicas,
)


def test_capacity_conversion_scales_above_old_broken_ceiling() -> None:
    """Covers CLAUDE.md §3.2 & PROJECT_JOURNAL.md §4 worked failure cases.

    The superseded formula `ceil(predicted_upper_cpu / 70)` yielded `ceil(92/70) = 2`
    (and `ceil(100/60) = 2`), so predictive scaling could never exceed MIN_PODS=2
    even when 3 or 10 pods were needed. `ceil(D_upper / C)` scales linearly.
    """
    superseded_at_92_pct = math.ceil(92.0 / 70.0)
    superseded_at_100_pct = math.ceil(100.0 / 60.0)
    assert superseded_at_92_pct == 2
    assert superseded_at_100_pct == 2

    c_rps = 14.2
    assert predictive_replicas_from_demand(0.0, c_rps) == 0
    assert predictive_replicas_from_demand(25.0, c_rps) == 2
    assert predictive_replicas_from_demand(42.6, c_rps) == 3  # exact 3 * 14.2 float guard
    assert predictive_replicas_from_demand(90.0, c_rps) == 7
    assert predictive_replicas_from_demand(140.0, c_rps) == 10


def test_predictive_conversion_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="upper_rps"):
        predictive_replicas_from_demand(-1.0, 14.0)
    with pytest.raises(ValueError, match="upper_rps"):
        predictive_replicas_from_demand(float("inf"), 14.0)
    with pytest.raises(ValueError, match="capacity_rps"):
        predictive_replicas_from_demand(50.0, 0.0)


def test_reactive_guard_triggers_strictly_above_85_pct() -> None:
    assert reactive_guard_replicas(3, 85.0) == (3, False)
    assert reactive_guard_replicas(3, 85.1) == (5, True)
    assert reactive_guard_replicas(6, 98.0) == (8, True)


def test_clamp_replicas_enforces_schedulable_bounds() -> None:
    assert clamp_replicas(0, min_pods=2, max_pods=15) == (2, True)
    assert clamp_replicas(1, min_pods=2, max_pods=15) == (2, True)
    assert clamp_replicas(5, min_pods=2, max_pods=15) == (5, False)
    assert clamp_replicas(15, min_pods=2, max_pods=15) == (15, False)
    assert clamp_replicas(25, min_pods=2, max_pods=15) == (15, True)


def test_load_arbiter_limits_reads_configs_and_fails_loudly_if_missing(tmp_path: Path) -> None:
    limits_md = Path(__file__).resolve().parent.parent / "deploy" / "limits.md"
    assert parse_max_pods_from_limits_md(limits_md) == 15

    # Missing Phase 2 configs must fail loudly rather than invent C or H
    with pytest.raises(FileNotFoundError):
        load_arbiter_limits(
            capacity_path=tmp_path / "missing_capacity.yaml",
            arbiter_config_path=tmp_path / "missing_arbiter.yaml",
            limits_path=limits_md,
        )

    cap_path = tmp_path / "capacity.yaml"
    arb_path = tmp_path / "config.yaml"
    cap_path.write_text(yaml.safe_dump({"pod_capacity_rps": 14.5}), encoding="utf-8")
    arb_path.write_text(yaml.safe_dump({"forecast_horizon_s": 70}), encoding="utf-8")

    limits = load_arbiter_limits(
        capacity_path=cap_path,
        arbiter_config_path=arb_path,
        limits_path=limits_md,
    )
    assert limits.capacity_rps == 14.5
    assert limits.horizon_s == 70
    assert limits.min_pods == 2
    assert limits.max_pods == 15
