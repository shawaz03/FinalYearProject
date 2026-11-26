# Baseline scaler configurations

**Never `kubectl apply -f deploy/k8s/` these.** They live outside that
directory deliberately. Only the experiment runner applies them, for baseline
runs, and it removes them afterwards.

## One controller per Deployment

An HPA and the CloudPulse arbiter both write `spec.replicas` on `target-app`.
Run both and they fight: each overwrites the other within seconds, the replica
count oscillates, and the resulting numbers describe neither system. CLAUDE.md
§2 rule 3; T7.1.2 makes the runner refuse to start if an HPA already exists.

| File | Role | Task |
|---|---|---|
| `hpa-default.yaml` | stock HPA, CPU 70%, default behavior | T7.2.1 |
| `hpa-tuned.yaml` | HPA at its realistic best | T7.2.2 |

Static over-provisioning (T7.2.3) needs no manifest - it is the Deployment
pinned to a fixed replica count, sized from the capacity constant `C` once
Phase 2 measures it.

## Manual use

```bash
kubectl apply -f deploy/baselines/hpa-default.yaml
kubectl get hpa -n cloudpulse -w
kubectl delete -f deploy/baselines/hpa-default.yaml   # ALWAYS clean up
```

## ⚠ Open issue: HPA utilisation is measured against *requests*

HPA computes CPU utilisation as `usage / requests`, **not** `usage / limits`.

The Deployment currently sets `requests.cpu: 50m` and `limits.cpu: 500m`. A pod
doing real work at 250m therefore reports **500% utilisation**, so a "70%
target" is breached at roughly 35m - almost idle. The default HPA would slam
into `maxReplicas` under trivial load, and the comparison would be meaningless
in CloudPulse's favour. That is not a win we want.

Note this differs from our own reactive guard, which measures against the
*limit* (`container_spec_cpu_quota`, see `deploy/queries.md` query 3) and is
therefore a true 0-100% scale.

**Resolution required before T7.2 runs:** bring `requests` and `limits` together
so utilisation means what HPA thinks it means. Setting them equal also puts the
pods in Guaranteed QoS, which reduces scheduling noise in benchmark timings.

Changing `limits.cpu` alters container startup time, so it invalidates the
T2.3.1 readiness numbers and the capacity constant `C`. Decide this **before**
Phase 2 finalises, not after.
