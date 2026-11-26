# services

The CloudPulse control loop. One container each.

| Service | Role | Phase | Owner |
|---|---|---|---|
| `collector/` | Prometheus → Postgres telemetry | 3 (T3.1) | M1 + M3 |
| `forecaster/` | CQR + ACI intervals, `POST /predict` | 4 (T4.5) | M1 |
| `arbiter/` | capacity model, control layer, K8s actuator | 5 | M3 + M2 |

Data flow: collector → forecaster → arbiter → Kubernetes API.
Each imports features from `cloudpulse_core`, never reimplements them.
