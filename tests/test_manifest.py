"""Tests for the experiment run manifest schema contract (T0.3.1)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from experiments.manifest import validate_manifest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads(
    (ROOT / "experiments" / "manifest.example.json").read_text(encoding="utf-8")
)


def test_example_manifest_is_valid() -> None:
    validate_manifest(EXAMPLE)


def test_missing_required_field_fails() -> None:
    broken = copy.deepcopy(EXAMPLE)
    del broken["git_sha"]
    with pytest.raises(jsonschema.ValidationError):
        validate_manifest(broken)


def test_unknown_scaler_or_extra_field_fails() -> None:
    bad_scaler = copy.deepcopy(EXAMPLE)
    bad_scaler["scaler"] = "unknown-autoscaler"
    with pytest.raises(jsonschema.ValidationError):
        validate_manifest(bad_scaler)

    extra_field = copy.deepcopy(EXAMPLE)
    extra_field["unexpected"] = 123
    with pytest.raises(jsonschema.ValidationError):
        validate_manifest(extra_field)


def test_invalid_datetime_or_inverted_bounds_fail() -> None:
    bad_time = copy.deepcopy(EXAMPLE)
    bad_time["start_time_utc"] = "not-a-timestamp"
    with pytest.raises(jsonschema.ValidationError):
        validate_manifest(bad_time)

    inverted_time = copy.deepcopy(EXAMPLE)
    inverted_time["end_time_utc"] = "2026-11-27T09:00:00Z"
    with pytest.raises(ValueError, match="end_time_utc"):
        validate_manifest(inverted_time)

    inverted_pods = copy.deepcopy(EXAMPLE)
    inverted_pods["cluster_resources"]["max_pods"] = 1
    with pytest.raises(ValueError, match="max_pods"):
        validate_manifest(inverted_pods)
