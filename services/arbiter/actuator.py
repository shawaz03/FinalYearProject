"""Phase 5, T5.3.2 — Kubernetes scale actuator with retry and exponential backoff.

Operates strictly within the least-privilege permissions defined in
`deploy/k8s/30-actuator-rbac.yaml` (`deployments` get/list/watch,
`deployments/scale` get/update/patch, `pods` get/list/watch).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger("cloudpulse.arbiter.actuator")

NAMESPACE = "cloudpulse"
DEPLOYMENT = "target-app"

# Status codes safe to retry on transient control-plane congestion/conflicts.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


class AppsApiProtocol(Protocol):
    def read_namespaced_deployment(self, name: str, namespace: str) -> Any: ...

    def patch_namespaced_deployment_scale(
        self, name: str, namespace: str, body: dict[str, Any]
    ) -> Any: ...


@dataclass(frozen=True)
class ReplicaState:
    desired_replicas: int
    ready_replicas: int


@dataclass(frozen=True)
class ActuationResult:
    success: bool
    target_replicas: int
    attempts: int
    backoff_delays_s: tuple[float, ...]
    error: str | None = None
    status_code: int | None = None


def load_apps_v1_api() -> client.AppsV1Api:
    """Load in-cluster ServiceAccount config when inside K8s, else kubeconfig."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.AppsV1Api()


def _is_retryable_exception(exc: Exception) -> tuple[bool, int | None]:
    if isinstance(exc, ApiException):
        status = getattr(exc, "status", None)
        if status is None:
            return True, None
        return int(status) in RETRYABLE_STATUS_CODES, int(status)
    return isinstance(exc, (OSError, TimeoutError, ConnectionError)), None


class KubernetesScaleActuator:
    """Patches `deployments/scale` with exponential backoff; never crashes loop."""

    def __init__(
        self,
        apps_api: AppsApiProtocol | None = None,
        *,
        namespace: str = NAMESPACE,
        deployment: str = DEPLOYMENT,
        max_attempts: int = 4,
        base_delay_s: float = 0.5,
        max_delay_s: float = 8.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        if base_delay_s < 0.0 or max_delay_s < base_delay_s:
            raise ValueError("Invalid retry delay bounds")

        self.apps_api: AppsApiProtocol = apps_api or load_apps_v1_api()
        self.namespace = namespace
        self.deployment = deployment
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self._sleep = sleep_fn

    def read_replica_state(self) -> ReplicaState:
        """Read current desired and authoritative Ready replica counts (queries.md §6)."""
        dep = self.apps_api.read_namespaced_deployment(self.deployment, self.namespace)
        spec_replicas = int(getattr(dep.spec, "replicas", 0) or 0)
        ready_replicas = int(getattr(dep.status, "ready_replicas", 0) or 0)
        return ReplicaState(
            desired_replicas=spec_replicas,
            ready_replicas=ready_replicas,
        )

    def scale(self, target_replicas: int) -> ActuationResult:
        """Patch `deployments/scale` to `target_replicas` with retry & backoff."""
        if target_replicas < 0:
            raise ValueError(f"target_replicas must be >= 0, got {target_replicas}")

        body = {"spec": {"replicas": int(target_replicas)}}
        delays: list[float] = []
        last_error: str | None = None
        last_status: int | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                self.apps_api.patch_namespaced_deployment_scale(
                    self.deployment, self.namespace, body
                )
                if attempt > 1:
                    logger.info(
                        "Scaled %s/%s to %d replicas on retry attempt %d/%d",
                        self.namespace,
                        self.deployment,
                        target_replicas,
                        attempt,
                        self.max_attempts,
                    )
                return ActuationResult(
                    success=True,
                    target_replicas=int(target_replicas),
                    attempts=attempt,
                    backoff_delays_s=tuple(delays),
                )
            except Exception as exc:
                retryable, status_code = _is_retryable_exception(exc)
                last_status = status_code
                last_error = f"{type(exc).__name__}: {exc}"

                if not retryable or attempt >= self.max_attempts:
                    logger.error(
                        "Scale patch failed for %s/%s -> %d replicas "
                        "(attempt %d/%d, retryable=%s, status=%s): %s",
                        self.namespace,
                        self.deployment,
                        target_replicas,
                        attempt,
                        self.max_attempts,
                        retryable,
                        status_code,
                        last_error,
                    )
                    return ActuationResult(
                        success=False,
                        target_replicas=int(target_replicas),
                        attempts=attempt,
                        backoff_delays_s=tuple(delays),
                        error=last_error,
                        status_code=last_status,
                    )

                delay = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
                delays.append(delay)
                logger.warning(
                    "Transient K8s API error scaling %s/%s -> %d (attempt %d/%d, "
                    "status=%s); backing off %.2fs: %s",
                    self.namespace,
                    self.deployment,
                    target_replicas,
                    attempt,
                    self.max_attempts,
                    status_code,
                    delay,
                    last_error,
                )
                if delay > 0:
                    self._sleep(delay)

        return ActuationResult(
            success=False,
            target_replicas=int(target_replicas),
            attempts=self.max_attempts,
            backoff_delays_s=tuple(delays),
            error=last_error,
            status_code=last_status,
        )
