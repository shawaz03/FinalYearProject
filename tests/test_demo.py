"""Tests for Phase 6 Step 6.4 (`T6.3`): Dockerfiles, pinned service deps, and `demo.py`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.demo import check_cluster_prerequisites, main as demo_main

ROOT = Path(__file__).resolve().parent.parent


def _parse_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        assert "==" in line, f"Unpinned dependency in {path}: {line!r}"
        pkg, ver = line.split("==", 1)
        pins[pkg.strip().lower()] = ver.strip()
    return pins


def test_collector_and_arbiter_dockerfiles_and_pins() -> None:
    root_pins = _parse_pins(ROOT / "requirements.txt")

    for service in ("collector", "forecaster", "arbiter"):
        req_path = ROOT / "services" / service / "requirements.txt"
        df_path = ROOT / "services" / service / "Dockerfile"
        assert req_path.exists(), f"Missing {req_path}"
        assert df_path.exists(), f"Missing {df_path}"

        svc_pins = _parse_pins(req_path)
        for pkg, ver in svc_pins.items():
            assert pkg in root_pins, f"{pkg} in {req_path} missing from root requirements.txt"
            assert (
                root_pins[pkg] == ver
            ), f"Version skew for {pkg}: {req_path} has {ver}, root has {root_pins[pkg]}"

        df_text = df_path.read_text(encoding="utf-8")
        assert "FROM python:3.12-slim-bookworm" in df_text
        assert "USER appuser" in df_text


def test_cluster_preflight_reports_missing_tools_and_artifacts(tmp_path: Path) -> None:
    res = check_cluster_prerequisites(
        tmp_path,
        build_images=True,
        which_fn=lambda _name: None,
    )
    assert res.ok is False
    assert res.missing_tools == ("kubectl", "k6", "docker", "k3d")
    assert "deploy/capacity.yaml" in res.missing_artifacts
    assert "ml/models/forecaster_bundle.joblib" in res.missing_artifacts


def test_cluster_preflight_refuses_when_hpa_attached(tmp_path: Path) -> None:
    for rel in (
        "deploy/capacity.yaml",
        "deploy/limits.md",
        "services/arbiter/config.yaml",
        "ml/models/forecaster_bundle.joblib",
        "load/k6_runner.js",
        "load/profiles/flash.json",
    ):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder", encoding="utf-8")

    def fake_runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["kubectl", "get", "hpa"]:
            payload = {
                "items": [
                    {
                        "metadata": {"name": "target-app-tuned-hpa"},
                        "spec": {"scaleTargetRef": {"name": "target-app"}},
                    }
                ]
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="NAME\ntarget-app\n", stderr="")

    res = check_cluster_prerequisites(
        tmp_path,
        which_fn=lambda name: f"/usr/bin/{name}",
        cmd_runner=fake_runner,
    )
    assert res.ok is False
    assert any("target-app-tuned-hpa" in err for err in res.errors)


def test_demo_replay_mode_runs_twice_cleanly() -> None:
    rc = demo_main(["--mode", "replay", "--replay-tick-s", "0.02", "--rehearsals", "2"])
    assert rc == 0
