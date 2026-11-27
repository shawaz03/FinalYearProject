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

## Resolved in manifest (pending live cluster re-verification): CPU requests == limits

HPA computes CPU utilisation as `usage / requests`, **not** `usage / limits`.

Previously, the Deployment set `requests.cpu: 50m` and `limits.cpu: 500m`, so a
pod doing real work at 250m reported **500% utilisation** to HPA and breached a
"70% target" at roughly 35m - giving CloudPulse an unfair artifact win.

`deploy/k8s/10-target-app-deployment.yaml` now sets `requests == limits` on
both CPU (`250m`) and memory (`128Mi`), placing every `target-app` pod in
Kubernetes **Guaranteed** QoS and aligning HPA's utilisation scale with our
own reactive guard (`container_spec_cpu_quota`, `deploy/queries.md` query 3).

**Pending on the cluster laptop before Phase 2 finalises:** apply the updated
manifest, re-verify `MAX_PODS = 15` (`0 Pending`), re-run `T2.3.1` readiness,
and measure `C` (`T2.2`) against the `250m` CPU cap.
