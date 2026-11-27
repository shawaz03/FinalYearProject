"""Unit tests for Phase 5 Step 5.3 Kubernetes actuator retry & backoff (T5.3.2)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

from services.arbiter.actuator import KubernetesScaleActuator


class _FakeAppsApi:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.failures = list(failures or [])
        self.calls: list[tuple[str, str, dict]] = []
        self.replicas = 3
        self.ready = 3

    def read_namespaced_deployment(self, name: str, namespace: str):
        return SimpleNamespace(
            spec=SimpleNamespace(replicas=self.replicas),
            status=SimpleNamespace(ready_replicas=self.ready),
        )

    def patch_namespaced_deployment_scale(self, name: str, namespace: str, body: dict):
        self.calls.append((name, namespace, body))
        if self.failures:
            raise self.failures.pop(0)
        self.replicas = body["spec"]["replicas"]
        return SimpleNamespace(spec=SimpleNamespace(replicas=self.replicas))


def test_actuator_retries_transient_api_failures_with_exponential_backoff(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleeps: list[float] = []
    api = _FakeAppsApi(
        failures=[
            ApiException(status=503, reason="Service Unavailable"),
            ApiException(status=429, reason="Too Many Requests"),
        ]
    )
    actuator = KubernetesScaleActuator(
        apps_api=api,
        max_attempts=4,
        base_delay_s=0.5,
        max_delay_s=4.0,
        sleep_fn=sleeps.append,
    )

    with caplog.at_level(logging.INFO):
        res = actuator.scale(7)

    assert res.success is True
    assert res.attempts == 3
    assert res.backoff_delays_s == (0.5, 1.0)
    assert sleeps == [0.5, 1.0]
    assert api.replicas == 7
    assert any("Transient K8s API error" in r.message for r in caplog.records)


def test_actuator_handles_exhausted_retries_without_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleeps: list[float] = []
    api = _FakeAppsApi(
        failures=[
            ApiException(status=500, reason="Internal Error"),
            ApiException(status=500, reason="Internal Error"),
            ApiException(status=500, reason="Internal Error"),
        ]
    )
    actuator = KubernetesScaleActuator(
        apps_api=api,
        max_attempts=3,
        base_delay_s=0.25,
        max_delay_s=2.0,
        sleep_fn=sleeps.append,
    )

    with caplog.at_level(logging.ERROR):
        res = actuator.scale(9)

    assert res.success is False
    assert res.attempts == 3
    assert res.backoff_delays_s == (0.25, 0.5)
    assert res.status_code == 500
    assert any("Scale patch failed" in r.message for r in caplog.records)


def test_actuator_does_not_retry_rbac_403_forbidden() -> None:
    sleeps: list[float] = []
    api = _FakeAppsApi(failures=[ApiException(status=403, reason="Forbidden")])
    actuator = KubernetesScaleActuator(
        apps_api=api, max_attempts=4, base_delay_s=1.0, sleep_fn=sleeps.append
    )
    res = actuator.scale(5)

    assert res.success is False
    assert res.attempts == 1
    assert res.status_code == 403
    assert sleeps == []


def test_read_replica_state_returns_spec_and_ready_counts() -> None:
    api = _FakeAppsApi()
    api.replicas = 5
    api.ready = 4
    actuator = KubernetesScaleActuator(apps_api=api)
    state = actuator.read_replica_state()
    assert state.desired_replicas == 5
    assert state.ready_replicas == 4
