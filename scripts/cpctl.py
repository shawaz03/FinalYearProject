#!/usr/bin/env python
"""Cross-platform CloudPulse task runner backing the root Makefile (T0.2.2).

Uses Python stdlib only so `setup` works before `.venv` exists. Works natively
on Windows (where GNU `make` is often absent) as well as Linux/WSL2/macOS.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"


def venv_python() -> Path:
    """Return the path to the `.venv` Python executable on the current OS."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(cmd: list[str], cwd: Path = ROOT) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=False).returncode


def _require_tool(name: str, hint: str) -> str | None:
    found = shutil.which(name)
    if not found:
        print(f"ERROR: `{name}` was not found on PATH. {hint}", file=sys.stderr)
    return found


def _require_script(rel: str, phase: str) -> Path | None:
    path = ROOT / rel
    if not path.exists():
        print(
            f"NOT YET IMPLEMENTED: `{rel}` will be created in {phase}.",
            file=sys.stderr,
        )
        return None
    return path


def cmd_help(_: argparse.Namespace) -> int:
    print(
        """CloudPulse task runner (scripts/cpctl.py)

Targets (also exposed in Makefile):
  help        Show this target list
  setup       Create Python 3.12 .venv, install requirements.txt, verify ML imports
  test        Run pytest unit tests under tests/
  cluster-up  Create/start k3d cluster and import target-app image (cluster laptop)
  deploy      Apply deploy/k8s/ manifests to the live cluster (cluster laptop)
  collect     Run telemetry collector (Phase 3)
  train       Train quantile forecaster & CQR calibrator (Phase 4)
  serve       Start forecaster / arbiter services locally (Phases 4-5)
  bench       Run sequential benchmark suite (Phase 7)
  analyse     Compute metrics and tables from experiments/results/ (Phase 7)
  clean       Remove Python caches and temporary test artifacts
"""
    )
    return 0


def cmd_setup(_: argparse.Namespace) -> int:
    py = venv_python()
    if not py.exists():
        if os.name == "nt" and shutil.which("py"):
            rc = _run(["py", "-3.12", "-m", "venv", str(VENV_DIR)])
        else:
            rc = _run([sys.executable, "-m", "venv", str(VENV_DIR)])
        if rc != 0:
            return rc

    req = ROOT / "requirements.txt"
    if not req.exists():
        print("ERROR: requirements.txt is missing.", file=sys.stderr)
        return 1

    rc = _run([str(py), "-m", "pip", "install", "-r", str(req)])
    if rc != 0:
        return rc
    return _run(
        [
            str(py),
            "-c",
            "import mapie, lightgbm, sklearn, fastapi, kubernetes; "
            "print('Verified imports: mapie', mapie.__version__, 'lightgbm', lightgbm.__version__)",
        ]
    )


def cmd_test(_: argparse.Namespace) -> int:
    py = venv_python()
    if not py.exists():
        print("ERROR: .venv not found. Run `setup` first.", file=sys.stderr)
        return 1
    return _run([str(py), "-m", "pytest", "-v", "tests"])


def cmd_cluster_up(_: argparse.Namespace) -> int:
    if not _require_tool("docker", "Run this target on the 15.6 GB cluster laptop."):
        return 1
    if not _require_tool("k3d", "Install k3d on the cluster laptop."):
        return 1
    rc = _run(["k3d", "cluster", "start", "cloudpulse"])
    if rc != 0:
        rc = _run(
            [
                "k3d",
                "cluster",
                "create",
                "cloudpulse",
                "--servers",
                "1",
                "--k3s-arg",
                "--disable=traefik@server:0",
            ]
        )
    return rc


def cmd_deploy(_: argparse.Namespace) -> int:
    if not _require_tool("kubectl", "Run this target on the 15.6 GB cluster laptop."):
        return 1
    return _run(
        ["kubectl", "apply", "-f", "deploy/k8s/", "--request-timeout=20s"]
    )


def cmd_collect(_: argparse.Namespace) -> int:
    script = _require_script("services/collector/collector.py", "Phase 3 (T3.1)")
    if not script:
        return 2
    return _run([str(venv_python()), str(script)])


def cmd_train(_: argparse.Namespace) -> int:
    script = _require_script("ml/train.py", "Phase 4 (T4.2)")
    if not script:
        return 2
    return _run([str(venv_python()), str(script)])


def cmd_serve(_: argparse.Namespace) -> int:
    script = _require_script("services/forecaster/app.py", "Phase 4 (T4.5)")
    if not script:
        return 2
    return _run(
        [
            str(venv_python()),
            "-m",
            "uvicorn",
            "services.forecaster.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8001",
        ]
    )


def cmd_bench(_: argparse.Namespace) -> int:
    script = _require_script("experiments/run.py", "Phase 7 (T7.1)")
    if not script:
        return 2
    return _run([str(venv_python()), str(script)])


def cmd_analyse(_: argparse.Namespace) -> int:
    script = _require_script("analysis/metrics.py", "Phase 7 (T7.6)")
    if not script:
        return 2
    return _run([str(venv_python()), str(script)])


def cmd_clean(_: argparse.Namespace) -> int:
    removed = 0
    for pattern in ("__pycache__", ".pytest_cache"):
        for path in ROOT.rglob(pattern):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    print(f"Removed {removed} cache directories.")
    return 0


COMMANDS = {
    "help": cmd_help,
    "setup": cmd_setup,
    "test": cmd_test,
    "cluster-up": cmd_cluster_up,
    "deploy": cmd_deploy,
    "collect": cmd_collect,
    "train": cmd_train,
    "serve": cmd_serve,
    "bench": cmd_bench,
    "analyse": cmd_analyse,
    "clean": cmd_clean,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CloudPulse cross-platform workflow runner."
    )
    parser.add_argument("target", nargs="?", default="help", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    return COMMANDS[args.target](args)


if __name__ == "__main__":
    raise SystemExit(main())
