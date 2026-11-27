"""Unit tests for open-loop k6 profiles and summary parsing (Phase 2, T2.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from experiments.k6_summary import load_profile, parse_k6_summary, validate_profile

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["gradual", "flash", "diurnal"])
def test_declarative_profiles_are_valid_and_15_minutes(name: str) -> None:
    data = load_profile(name)
    stats = validate_profile(data)
    assert stats["profile"] == name
    assert stats["slo_ms"] == 100.0
    assert stats["total_duration_s"] == 900
    assert stats["peak_rps"] > stats["mean_rps"] > 0


def test_flash_profile_starts_calm_before_spike() -> None:
    flash = load_profile("flash")
    first = flash["segments"][0]
    peak = max(seg["target_rps"] for seg in flash["segments"])
    assert first["target_rps"] <= 25
    assert first["duration_s"] >= 120
    assert peak >= first["target_rps"] * 5


def test_k6_runner_uses_constant_arrival_rate_and_explicit_p50_p99() -> None:
    js = (ROOT / "load" / "k6_runner.js").read_text(encoding="utf-8")
    assert 'executor: "constant-arrival-rate"' in js
    assert '"p(50)"' in js
    assert '"p(99)"' in js
    assert re.search(r"summaryTrendStats\s*:", js)


def test_parse_k6_summary_validates_required_fields(tmp_path: Path) -> None:
    sample = {
        "profile": "flash",
        "slo_ms": 100,
        "total_duration_s": 900,
        "target_rps": 55.0,
        "achieved_rps": 54.8,
        "tracking_error_pct": -0.36,
        "dropped_iterations": 0,
        "sla_violation_rate": 0.005,
        "latency_ms": {"p50": 8.2, "p90": 14.5, "p99": 35.1},
    }
    p = tmp_path / "k6_summary.json"
    p.write_text(json.dumps(sample), encoding="utf-8")
    parsed = parse_k6_summary(p)
    assert parsed["latency_ms"]["p99"] == 35.1

    broken = dict(sample)
    broken["latency_ms"] = {"p50": 8.2, "p90": 14.5}
    p.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="p99"):
        parse_k6_summary(p)
