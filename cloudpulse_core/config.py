"""Shared CloudPulse configuration constants and validated YAML loaders."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Canonical timing and SLO constants (CLAUDE.md §3.5 & §3.7)
SLO_MS: Final[float] = 100.0
SCRAPE_INTERVAL_S: Final[int] = 5
DECISION_INTERVAL_S: Final[int] = 30

# Control layer bounds & guardrails (CLAUDE.md §3.5)
MIN_PODS: Final[int] = 2
DEFAULT_MAX_PODS: Final[int] = 15
REACTIVE_CPU_THRESHOLD_PCT: Final[float] = 85.0
SCALE_DOWN_WINDOW_S: Final[int] = 300
MIN_DWELL_S: Final[int] = 60
DEADBAND_RATIO: Final[float] = 0.10


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping from `path`, raising clear errors if missing/invalid."""
    if not path.exists():
        raise FileNotFoundError(
            f"Required config file not found: {path}. "
            "Run the corresponding Phase 2 cluster measurement first."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}, got {type(data).__name__}")
    return data


def load_capacity_config(path: Path | None = None) -> dict[str, Any]:
    """Load `deploy/capacity.yaml` produced by Phase 2 (T2.2)."""
    target = path or (PROJECT_ROOT / "deploy" / "capacity.yaml")
    return load_yaml(target)


def load_arbiter_config(path: Path | None = None) -> dict[str, Any]:
    """Load `services/arbiter/config.yaml` containing derived horizon H (T2.3.2)."""
    target = path or (PROJECT_ROOT / "services" / "arbiter" / "config.yaml")
    return load_yaml(target)
