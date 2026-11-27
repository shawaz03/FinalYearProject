# Measured cluster limits

Measured 2026-11-26 on the development laptop. **Re-measure on any machine that
runs benchmarks** — these numbers are hardware-specific and every replica
decision depends on them.

## Node

`k3d-cloudpulse-server-0`, K3s v1.35.5+k3s1, single node.

| Allocatable | Value |
|---|---|
| CPU | 8 cores |
| Memory | 5.79 GiB |
| Max pods | 110 |

## MAX_PODS — resolves finding D13

**MAX_PODS = 15 (measured 2026-11-26 under `50m`/`64Mi` requests; re-verification
pending on cluster after updating to `250m`/`128Mi` Guaranteed QoS).**

Previously scaled `target-app` to 15 replicas: all 15 reached `Running`, **zero Pending**.

This matters because a `Pending` pod is not a scaled pod. If the arbiter asks
for replicas the node cannot place, scaling silently becomes a no-op, latency
stays bad, and the benchmark measures nothing.

- Previous requests at 15 replicas (2026-11-26): 750m CPU (9%), 960Mi memory (16%)
- **Updated Guaranteed QoS (`250m` / `128Mi`) at 15 replicas:**
  - CPU requests/limits: 15 x 250m = 3750m of 8000m allocatable (47%)
  - Memory requests/limits: 15 x 128Mi = 1920Mi of 5.79Gi allocatable (32%)
- Observed actual idle usage (2026-11-26): ~37 Mi and 3-4 m CPU per pod

Do not treat `MAX_PODS = 15` as final under the new `250m`/`128Mi` spec until
re-verified with `0 Pending` on the 15.6 GB cluster laptop.

## Cluster footprint

| State | Node memory |
|---|---|
| Control plane only | 474 Mi |
| + 3 target-app pods | ~700 Mi |
| + 15 target-app pods | 1205 Mi |

Host-side `vmmemWSL` peaked at ~1.8 GB, inside the 6 GB `.wslconfig` cap.

## Pod startup — preliminary, NOT the Phase 2 measurement

Scaling 3 -> 6 reached all-Ready in **2.6 seconds** with `WARMUP_SECONDS=0`.

**This is the single most important number measured so far, and it threatens
the project's premise.** The thesis assumes a 30-90 s cold start. On a warm
single-node k3d cluster with the image already imported, pods are Ready in
under three seconds. At that speed reactive HPA performs fine and predictive
scaling has almost nothing to win — the experiment would honestly refute us.

This is exactly the risk recorded in CLAUDE.md §3.6, now confirmed with a
number rather than predicted. The response is unchanged and already built:
`WARMUP_SECONDS` makes startup cost a declared, swept experimental variable
(T7.4), so the result becomes "predictive scaling pays off above X seconds of
startup cost, and here is X" instead of an unsupportable win claim.

Proper p50/p95 characterisation across `WARMUP_SECONDS` in {0,5,15,30,60} is
Phase 2, T2.3.
