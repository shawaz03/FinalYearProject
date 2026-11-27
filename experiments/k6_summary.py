"""Shared loaders and validators for k6 profiles and k6_summary.json (T2.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloudpulse_core.config import PROJECT_ROOT

PROFILES_DIR = PROJECT_ROOT / "load" / "profiles"
REQUIRED_SUMMARY_KEYS = (
    "profile",
    "slo_ms",
    "total_duration_s",
    "target_rps",
    "achieved_rps",
    "tracking_error_pct",
    "dropped_iterations",
    "sla_violation_rate",
    "latency_ms",
)
REQUIRED_LATENCY_KEYS = ("p50", "p90", "p99")


def validate_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a declarative load profile dict and return summary stats."""
    for key in ("profile", "slo_ms", "segments"):
        if key not in data:
            raise ValueError(f"Profile missing required field: {key}")

    segments = data["segments"]
    if not isinstance(segments, list) or not segments:
        raise ValueError("Profile `segments` must be a non-empty list")

    total_s = 0
    total_reqs = 0.0
    peak_rps = 0.0
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"Segment {idx} must be an object")
        dur = seg.get("duration_s")
        rps = seg.get("target_rps")
        if not isinstance(dur, (int, float)) or dur <= 0:
            raise ValueError(f"Segment {idx} duration_s must be > 0, got {dur!r}")
        if not isinstance(rps, (int, float)) or rps <= 0:
            raise ValueError(f"Segment {idx} target_rps must be > 0, got {rps!r}")
        total_s += int(dur)
        total_reqs += float(dur) * float(rps)
        peak_rps = max(peak_rps, float(rps))

    return {
        "profile": data["profile"],
        "slo_ms": float(data["slo_ms"]),
        "total_duration_s": total_s,
        "peak_rps": peak_rps,
        "mean_rps": total_reqs / total_s,
        "segment_count": len(segments),
    }


def load_profile(name: str, profiles_dir: Path = PROFILES_DIR) -> dict[str, Any]:
    """Load and validate `load/profiles/<name>.json`."""
    path = profiles_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Load profile not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_profile(data)
    return data


def parse_k6_summary(path: Path) -> dict[str, Any]:
    """Load and validate a `k6_summary.json` produced by `load/k6_runner.js`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in REQUIRED_SUMMARY_KEYS:
        if key not in data:
            raise ValueError(f"k6 summary {path} missing required key: {key}")
    lat = data["latency_ms"]
    if not isinstance(lat, dict):
        raise ValueError(f"k6 summary {path} latency_ms must be an object")
    for q in REQUIRED_LATENCY_KEYS:
        if q not in lat:
            raise ValueError(f"k6 summary {path} latency_ms missing quantile: {q}")
    return data
