"""Phase 5, T5.1 & T5.2.3 — capacity-based replica conversion and bounds clamping.

Converts predicted upper-bound offered demand `D_upper(t+H)` (RPS) into a
desired replica count using the Phase 2 measured capacity constant `C`
(`deploy/capacity.yaml`) and clamps to `[MIN_PODS, MAX_PODS]` (`deploy/limits.md`).

Never uses the superseded CPU-% ratio `ceil(predicted_upper_cpu / target_cpu_per_pod)`,
which was dimensionally broken and capped below `MIN_PODS` (CLAUDE.md §3.2).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from cloudpulse_core.config import (
    MIN_PODS,
    PROJECT_ROOT,
    REACTIVE_CPU_THRESHOLD_PCT,
    load_arbiter_config,
    load_capacity_config,
)

LIMITS_MD_PATH = PROJECT_ROOT / "deploy" / "limits.md"


@dataclass(frozen=True)
class ArbiterLimits:
    capacity_rps: float
    horizon_s: int
    min_pods: int
    max_pods: int
    reactive_cpu_threshold_pct: float = REACTIVE_CPU_THRESHOLD_PCT
    reactive_increment: int = 2


def parse_max_pods_from_limits_md(limits_path: Path = LIMITS_MD_PATH) -> int:
    """Read the empirically verified `MAX_PODS` ceiling from `deploy/limits.md`."""
    if not limits_path.exists():
        raise FileNotFoundError(f"Cluster limits document not found: {limits_path}")
    text = limits_path.read_text(encoding="utf-8")
    match = re.search(r"\bMAX_PODS\s*=\s*(\d+)", text)
    if not match:
        raise ValueError(f"Could not find `MAX_PODS = <int>` in {limits_path}")
    max_pods = int(match.group(1))
    if max_pods < MIN_PODS:
        raise ValueError(
            f"Parsed MAX_PODS={max_pods} is smaller than MIN_PODS={MIN_PODS}"
        )
    return max_pods


def load_arbiter_limits(
    *,
    capacity_path: Path | None = None,
    arbiter_config_path: Path | None = None,
    limits_path: Path = LIMITS_MD_PATH,
) -> ArbiterLimits:
    """Load measured Phase 2 capacity C, horizon H, and MAX_PODS from committed config.

    Fails loudly with `FileNotFoundError` if Phase 2 cluster measurements have
    not generated `deploy/capacity.yaml` or `services/arbiter/config.yaml` yet.
    """
    cap_cfg = load_capacity_config(capacity_path)
    arb_cfg = load_arbiter_config(arbiter_config_path)
    max_pods = parse_max_pods_from_limits_md(limits_path)

    capacity_rps = float(cap_cfg["pod_capacity_rps"])
    horizon_s = int(arb_cfg["forecast_horizon_s"])
    if capacity_rps <= 0 or not math.isfinite(capacity_rps):
        raise ValueError(f"Invalid pod_capacity_rps in capacity config: {capacity_rps!r}")
    if horizon_s <= 0:
        raise ValueError(f"Invalid forecast_horizon_s in arbiter config: {horizon_s!r}")

    return ArbiterLimits(
        capacity_rps=capacity_rps,
        horizon_s=horizon_s,
        min_pods=MIN_PODS,
        max_pods=max_pods,
    )


def predictive_replicas_from_demand(upper_rps: float, capacity_rps: float) -> int:
    """Compute `ceil(D_upper / C)` from predicted upper-bound demand (CLAUDE.md §3.2)."""
    if not math.isfinite(upper_rps) or upper_rps < 0.0:
        raise ValueError(f"upper_rps must be finite and >= 0, got {upper_rps!r}")
    if not math.isfinite(capacity_rps) or capacity_rps <= 0.0:
        raise ValueError(f"capacity_rps must be finite and > 0, got {capacity_rps!r}")
    # Round at 1e-9 before ceil so exact multiples (e.g. 42.6 / 14.2 = 3.0000000000000004)
    # do not trigger spurious +1 pod rounding.
    ratio = round(upper_rps / capacity_rps, 9)
    return int(math.ceil(ratio))


def reactive_guard_replicas(
    current_replicas: int,
    per_pod_cpu_pct: float,
    *,
    threshold_pct: float = REACTIVE_CPU_THRESHOLD_PCT,
    increment: int = 2,
) -> tuple[int, bool]:
    """Return `(reactive_target, triggered)` for the emergency CPU guard (T5.2.1)."""
    if current_replicas < 0:
        raise ValueError(f"current_replicas must be >= 0, got {current_replicas!r}")
    if not math.isfinite(per_pod_cpu_pct) or per_pod_cpu_pct < 0.0:
        raise ValueError(
            f"per_pod_cpu_pct must be finite and >= 0, got {per_pod_cpu_pct!r}"
        )
    if per_pod_cpu_pct > threshold_pct:
        return current_replicas + increment, True
    return current_replicas, False


def clamp_replicas(
    replicas: int,
    *,
    min_pods: int = MIN_PODS,
    max_pods: int = 15,
) -> tuple[int, bool]:
    """Clamp replica target to schedulable `[min_pods, max_pods]` (T5.2.3)."""
    if min_pods < 1 or max_pods < min_pods:
        raise ValueError(f"Invalid pod bounds: min_pods={min_pods}, max_pods={max_pods}")
    clamped = min(max_pods, max(min_pods, int(replicas)))
    return clamped, clamped != int(replicas)
