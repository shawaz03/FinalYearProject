"""Tests for the cross-platform workflow runner and Makefile targets (T0.2.2)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_TARGETS = (
    "setup",
    "cluster-up",
    "deploy",
    "collect",
    "train",
    "serve",
    "bench",
    "analyse",
    "clean",
)


def test_cpctl_help_lists_all_required_targets() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cpctl.py"), "help"],
        capture_output=True,
        text=True,
        check=True,
    )
    for target in REQUIRED_TARGETS:
        assert target in proc.stdout


def test_makefile_declares_all_required_targets() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in REQUIRED_TARGETS:
        assert f"{target}:" in makefile
