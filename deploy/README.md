# deploy

Cluster definition. Everything needed to recreate the environment.

- `k8s/` — Deployment, Service, ServiceMonitor, RBAC (ServiceAccount + Role
  limited to `deployments/scale`), HPA manifests for the baselines
- `helm/` — kube-prometheus-stack values (`scrapeInterval: 5s`)

Measured constants land here and are treated as committed config:
`capacity.yaml` (pod_capacity_rps), `readiness.md` (pod ready p50/p95),
`limits.md` (real schedulable MAX_PODS), `queries.md` (canonical PromQL).

Phases 1–2. Owner: M2.
