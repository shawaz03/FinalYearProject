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

**MAX_PODS = 15, empirically verified schedulable.**

Scaled `target-app` to 15 replicas: all 15 reached `Running`, **zero Pending**.

This matters because a `Pending` pod is not a scaled pod. If the arbiter asks
for replicas the node cannot place, scaling silently becomes a no-op, latency
stays bad, and the benchmark measures nothing. Headroom at 15 replicas:

- CPU requests: 15 x 50m = 750m of 8000m allocatable (9%)
- Memory requests: 15 x 64Mi = 960Mi of 5.79Gi allocatable (16%)
- Observed actual usage: ~37 Mi and 3-4 m CPU per idle pod

There is room to raise MAX_PODS above 15 if an experiment needs it. Do not
raise it without re-running this check.

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
