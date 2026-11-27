"""Validation helpers for CloudPulse experiment run manifests (T0.3.1)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent / "manifest.schema.json"


def _build_format_checker() -> jsonschema.FormatChecker:
    checker = jsonschema.FormatChecker()

    @checker.checks("date-time", raises=ValueError)
    def _is_iso_datetime(value: object) -> bool:
        if not isinstance(value, str):
            return False
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True

    return checker


def load_manifest_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest(
    manifest: dict[str, Any], schema_path: Path = SCHEMA_PATH
) -> None:
    """Validate a manifest dictionary against `manifest.schema.json` and invariants."""
    schema = load_manifest_schema(schema_path)
    jsonschema.validate(
        instance=manifest,
        schema=schema,
        format_checker=_build_format_checker(),
    )

    start = datetime.fromisoformat(
        manifest["start_time_utc"].replace("Z", "+00:00")
    )
    end = datetime.fromisoformat(manifest["end_time_utc"].replace("Z", "+00:00"))
    if end < start:
        raise ValueError(
            f"end_time_utc ({end.isoformat()}) precedes start_time_utc ({start.isoformat()})"
        )

    res = manifest["cluster_resources"]
    if res["max_pods"] < res["min_pods"]:
        raise ValueError(
            f"max_pods ({res['max_pods']}) must be >= min_pods ({res['min_pods']})"
        )
