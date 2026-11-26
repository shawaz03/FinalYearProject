# CloudPulse — Project Journal

**Team A8-8 · Mohan Babu University · Session 1: 26 November 2026**

A record of what was reviewed, what was decided and why, what was built, and
what was measured. Written so a teammate who was not present can pick up the
work, and so every decision can be defended at viva.

> **Precedence.** `CLAUDE.md` at the repository root is the authoritative plan.
> This journal explains *how we got there*. Where the older documents in
> `docs/planning/` and `docs/viva/` disagree with either, they are superseded —
> see "Documents that are now out of date" below.

---

## Table of contents

1. [Session at a glance](#1-session-at-a-glance)
2. [Verifying the base paper](#2-verifying-the-base-paper)
3. [The review: 20 findings](#3-the-review-20-findings)
4. [The bug that would have sunk the project](#4-the-bug-that-would-have-sunk-the-project)
5. [Architecture decisions and why](#5-architecture-decisions-and-why)
6. [What is built, with evidence](#6-what-is-built-with-evidence)
7. [Defects found during verification](#7-defects-found-during-verification)
8. [Measured constants](#8-measured-constants)
9. [Current state](#9-current-state)
10. [Open decisions](#10-open-decisions)
11. [What each member does next](#11-what-each-member-does-next)
12. [Appendix: commits and commands](#12-appendix-commits-and-commands)

---

## 1. Session at a glance

The project began the session as **14 documents and zero lines of code**. It
ends with a running Kubernetes cluster, a containerised application under
live monitoring, and a verified measurement harness.

| | Start | End |
|---|---|---|
| Code | none | app, manifests, measurement harness |
| Version control | not a git repo | 10 commits |
| Cluster | none | k3d `cloudpulse`, 785 Mi |
| Monitoring | none | Prometheus, 4/4 targets up |
| Tasks complete | 0 | 14 done, 1 partial, 54 open |

Three things were found that would each have caused serious problems later.
All three are described below: a dimensionally wrong replica formula, a
latency metric reporting fabricated numbers, and a cold-start assumption that
is false on our hardware.

---

## 2. Verifying the base paper

Before reviewing anything else, the citation was checked — the whole capstone
rests on it.

**It is genuine and the citation is exact.**

| Field | Value |
|---|---|
| Title | *Uncertainty-Aware Online Time Series Multi-Step Forecasting Framework in Cloud Systems* |
| Authors | Chen, Luo, Huang, Jiang, Shi, Zhang, Gao |
| Journal | IEEE Transactions on Knowledge and Data Engineering |
| Volume / Issue | 38 / 5 · Pages 3277–3290 · May 2026 |
| DOI | `10.1109/TKDE.2026.3674583` |

Verified by resolving the DOI (redirects to IEEE Xplore document 11435627) and
by querying the Crossref API, which returned matching title, journal, volume,
issue, page range and all seven author names.

> **Useful to know:** ordinary web search does **not** find this paper yet —
> the indexes have not caught up. If a mentor searches for it and finds
> nothing, show them the DOI resolving. This is normal for a recent issue, not
> a sign of a problem.

The research-gap framing in the original proposal — *the paper is offline-only,
we close the loop on a real cluster* — is legitimate and survives review intact.

---

## 3. The review: 20 findings

The README and planning documents were reviewed in full. Twenty issues were
recorded as D1–D20 in `CLAUDE.md` §4. Summary:

### Critical

| ID | Finding |
|---|---|
| D1 | Benchmark table presents **targets as measured results**, and contradicts itself (static 45 ms vs CloudPulse <100 ms while claiming parity; cost 48% vs HPA 45% while calling ours "optimal") |
| D2 | Replica formula is dimensionally wrong — see §4 |
| D3 | Forecast target is CPU utilisation %, which is *endogenous* |
| D4 | No capacity model: nothing converts a forecast into a pod count |
| D5 | Zero code existed; not even a git repository |

### High

| ID | Finding |
|---|---|
| D6 | No control layer — no cooldown, hysteresis, dwell or deadband, so the arbiter would oscillate |
| D7 | Conformal coverage guarantee invalid: split conformal assumes exchangeability, time series are not exchangeable |
| D8 | Asymmetric loss *and* conformal upper bound double-count safety margin |
| D9 | Locust is closed-loop, so it hides the very latency the project exists to measure |
| D10 | Horizon H asserted at 60 s, never derived from measured pod readiness |
| D11 | Baseline is *untuned default* HPA — a straw man |
| D12 | No repetitions or seeds defined, yet ANOVA/t-tests promised; n=1 supports no statistics |
| D13 | `MAX_PODS=20` may not schedule on a laptop; Pending pods make scaling a silent no-op |

### Medium and low

D14 fictional `$/hour` costing · D15 no failure handling · D16 Alibaba trace
resolution mismatch · D17 leakage discipline unstated · D18 no reproducibility
infrastructure · D19 workload imbalance across members · D20 repository hygiene.

### What survived review unchanged

The problem framing, the hybrid predictive + reactive arbiter shape, the
research-gap argument, and the blueprint's instinct to build infrastructure
before training on real data. These are good and were kept.

---

## 4. The bug that would have sunk the project

The original pseudocode, in both the README and `PROJECT_PIN_TO_PIN_GUIDE.md`:

```python
TARGET_CPU_PER_POD = 60.0
MIN_PODS = 2
predictive_replicas = math.ceil(predicted_upper_cpu / TARGET_CPU_PER_POD)
```

Running it across the full input range:

```
U =  60%  ->  1 pod
U =  86%  ->  2 pods      <- the guide's own worked example
U = 100%  ->  2 pods      <- the absolute ceiling
MIN_PODS  =   2
```

`ceil(100/60) = 2`. The floor is already 2. **The predictive branch can never
raise the replica count above the minimum, at any CPU value.** In the guide's
own spike example it returns 2 while 3 pods are running, so `max()` discards it
entirely and the reactive guard does all the work.

CloudPulse would have been a reactive autoscaler with an ML model attached that
could not influence anything. The demo would still *look* fine — pods scale,
the dashboard animates — which is precisely why it was dangerous.

### Why it happens

Dividing a percentage by a percentage yields a ratio, not a count. But the
deeper problem is that **average utilisation is endogenous**: it is a function
of the replica count the controller itself sets. A model trained on it learns
its own past control policy, and the signal shifts every time the controller
acts.

### The fix (CLAUDE.md §3.2)

Forecast **replica-independent demand**, then convert with a **measured**
capacity constant:

```
replicas = ceil( D_upper(t+H) / C )
```

- `D` = aggregate requests/sec (primary) or total CPU millicores (secondary)
- `C` = sustainable RPS per pod at target utilisation, **measured** in Phase 2

This is why a new **blocking Phase 2** was inserted: without `C`, a forecast
cannot become a pod count at all.

---

## 5. Architecture decisions and why

Every change from the original plan, with its reason.

| # | Was | Now | Why |
|---|---|---|---|
| 1 | Forecast CPU % | Forecast RPS, divide by measured `C` | §4 above |
| 2 | XGBoost point model | Quantile LightGBM/GBR (pinball loss) | CQR needs quantiles, not a point estimate |
| 3 | MAPIE split conformal | CQR **+ Adaptive Conformal Inference** | Coverage guarantee fails under non-exchangeability; ACI is also closer to what the base paper does |
| 4 | Locust | **k6**, constant arrival rate | Locust is closed-loop: under overload it quietly sends less traffic and hides the damage (coordinated omission) |
| 5 | Minikube | **k3d / K3s** | 474 Mi control plane vs ~2 GB; 61 s start; metrics-server built in; CNCF-certified so the "real Kubernetes" claim holds |
| 6 | kube-prometheus-stack | **Plain Prometheus manifest** | Grafana + Alertmanager cost ~500 Mi and the Phase 6 dashboard replaces Grafana. No Helm, no operator, no CRDs |
| 7 | TimescaleDB | Plain PostgreSQL | Ops risk, no research value at this data volume |
| 8 | Everything in-cluster | **Only app + Prometheus in-cluster** | Cuts cluster from ~5.4 GB to ~2 GB, and removes the rebuild-redeploy cycle while developing the arbiter |
| 9 | H = 60 s asserted | H derived from measured readiness | §8 |
| 10 | `$/hour` cost | **Pod-seconds**, dollars only as labelled display | Local cluster has no dollar cost; pod-seconds is defensible |
| 11 | Default HPA baseline | Default **+ tuned** HPA + naive + oracle | Beating an untuned baseline proves little |
| 12 | n=1 | n=5 per cell, non-parametric tests | Latency is not normal; t-tests on n=1 are meaningless |
| 13 | — | **`WARMUP_SECONDS` swept as a variable** | See §7, finding 3 — this is now the headline experiment |

### The decision behind decision 13

The thesis assumes a 30–90 second cold start. On a warm single-node cluster
with the image already imported, pods are Ready in about 3 seconds. At that
speed, reactive HPA performs fine and predictive scaling wins almost nothing —
the experiment would honestly refute our own premise.

Rather than hide this, startup cost became a **declared, swept experimental
variable**. Phase 7 reports *"predictive scaling pays off above X seconds of
startup cost, and here is X"*, including where CloudPulse loses. That is a
stronger and far more defensible finding than an unqualified win claim.

---

## 6. What is built, with evidence

Nothing below is claimed without a check that was actually run.

### Repository (Phase 0, T0.1 — done)

Flattened the duplicated `FinalYearProject-main/FinalYearProject-main/`
nesting, moved all 14 documents into `docs/` with `git mv` (history preserved),
renamed to consistent kebab-case, and created the code skeleton. Every
top-level directory carries a README naming its purpose, owner and phase.

**Nothing was deleted.** Checksums confirmed no byte-identical duplicates —
`review 0.pptx` and `Review_0_A8-8.pptx` genuinely differ, so both were kept.
`docs/README.md` maps every new filename back to its original.

### Target application (Phase 1, T1.1 — done)

`apps/target-app/` — FastAPI, containerised as `cloudpulse/target-app:0.1.1`,
260 MB, non-root uid 10001, single uvicorn worker.

| Endpoint | Purpose |
|---|---|
| `GET /api/work` | the endpoint under load; deterministic CPU burn |
| `GET /healthz` | liveness — never depends on warmup |
| `GET /readyz` | readiness — 503 until `WARMUP_SECONDS` elapses |
| `GET /metrics` | Prometheus scrape target |

**Verified in a running container:**

- Warmup gate: at t=3 s → `healthz 200`, `readyz 503`; at t=10 s → both 200.
- Probe exclusion: 6 calls to `/api/work` produced `http_requests_total 6.0`,
  while 4 calls to `/healthz` and `/readyz` produced **no series at all**.

> That second check matters more than it looks. Kubelet probes `/readyz` every
> two seconds forever. Had those counted, the RPS signal would carry a constant
> inflation that *changes with replica count* — and the forecaster would have
> trained on polluted data with nothing ever failing visibly.

Three design choices worth being able to defend:

- **`--workers 1`** — one pod equals one unit of capacity, so `C` is unambiguous.
- **`/api/work` is a sync `def`** — FastAPI runs it in a threadpool, so a
  saturated CPU cannot stall the health probes.
- **`/healthz` ignores warmup** — a liveness probe that fires during warmup
  would crash-loop the pod forever.

### Cluster (Phase 1, T1.2 — done)

k3d cluster `cloudpulse`, K3s v1.35.5, single node, Traefik disabled.

- Created in **61 seconds**; control plane **474 Mi**.
- Only 3 system pods: coredns, local-path-provisioner, metrics-server.
- `kubectl top` working — the HPA baseline depends on it.
- Deployment 3/3 Ready; Service routing verified (9 requests spread across all
  three pods); scale up and down verified.

`imagePullPolicy: IfNotPresent` is load-bearing: the image is *imported* into
the node, not published to a registry. `Always` would fail every pod with
`ImagePullBackOff`.

### Monitoring (Phase 1, T1.3 — done)

Plain Prometheus — no Helm, no operator, no ServiceMonitor CRDs.

- **27 Mi** against a 400 Mi budget. 5 s scrape, 2 h retention.
- Two jobs: pod discovery by annotation (RPS), and kubelet cAdvisor via
  API-server proxy (total CPU millicores). Both signals replica-independent.
- 4/4 targets UP.

Filtering cAdvisor to two series in one namespace is what keeps it at 27 Mi;
unfiltered it exceeds 1 Gi.

`deploy/queries.md` records the canonical PromQL. **The collector, arbiter and
dashboard must all use those exact expressions** — divergent copies are how
train/serve skew gets in.

### Actuator RBAC and HPA baselines (T5.3.1, T7.2.1, T7.2.2)

- `deploy/k8s/30-actuator-rbac.yaml` — namespaced **Role**, not ClusterRole.
  Read Deployments and pods; write only `deployments/scale`. No pod creation,
  no Secrets, no other namespace.
- `deploy/baselines/hpa-default.yaml` — stock HPA, CPU 70%.
- `deploy/baselines/hpa-tuned.yaml` — CPU 50%, zero scale-up stabilisation,
  +100%/+4 pods per 15 s, scale-down window matched to ours.

The baselines live **outside** `deploy/k8s/` deliberately, so a routine
`kubectl apply -f deploy/k8s/` cannot start a second controller that fights the
arbiter. All three validated with `--dry-run=server`; nothing created.

### Measurement harness (T2.3.1 — running)

`experiments/measure_readiness.py` measures scale-patch → Ready wall clock
client-side at 250 ms resolution.

Not `creationTimestamp` → Ready `lastTransitionTime`: Kubernetes serialises
those at 1-second resolution, which on a ~3 second measurement is ~30%
quantisation error.

---

## 7. Defects found during verification

Three real bugs were caught by checking rather than assuming. Each is the kind
that survives into a thesis unnoticed.

### 1. Latency quantiles were fabricated

The first PromQL run returned `p50 = 50.0 ms, p99 = 99.0 ms`. Too round.
Dumping the histogram showed why:

```
le="0.1"   -> 201
le="0.5"   -> 201
le="1.0"   -> 201
le="+Inf"  -> 201
```

Four buckets. Every request landed in the first one, so `histogram_quantile`
had no information inside 0–100 ms and **interpolated linearly**. It would
report 50 ms and 99 ms whether the true latency was 5 ms or 95 ms.

| | Reported | True |
|---|---|---|
| p50 | 50.0 ms | **8.1 ms** |
| p90 | — | **14.1 ms** |
| p99 | 99.0 ms | **34.6 ms** |

The p99 was **2.9× too high**. And 99 ms against a 100 ms SLO reads as a system
on the edge — exactly the plausible-looking number that gets screenshotted into
a slide and never questioned.

**Fixed** by `LATENCY_BUCKETS` in `apps/target-app/app/main.py`: dense between
5 ms and 300 ms, with an **exact boundary at 0.1** so the SLO is *counted*, not
estimated. Never remove that override.

### 2. kubectl could not reach the cluster

Every `kubectl` call hung 30 s then failed. k3d had written
`server: https://host.docker.internal:60452`, which on this machine resolves to
`10.165.67.128` — a VPN or virtual adapter, not the Docker host. The cluster
was healthy throughout.

**Fixed** by pointing the context at `127.0.0.1`. Written up in
`deploy/troubleshooting.md` because teammates will hit it. Note the port is
assigned at cluster creation and changes if the cluster is recreated.

### 3. Pods start in ~3 seconds, not 30–90

Scaling 3 → 6 reached all-Ready in **2.6 seconds**; a later single-cycle check
gave 3.47 s. The thesis assumes a 30–90 second cold start.

This threatens the premise directly, and is the reason `WARMUP_SECONDS` exists
and is swept (§5, decision 13). Recorded in `deploy/limits.md`.

### Also: a methodology error caught before it mattered

The readiness smoke test returned `3.47, 3.47, 3.47` for a burst of three pods.
Identical — because pods started together become Ready together. Those are
**one observation counted three times**, not three samples.

Left alone, the sweep would have reported n=21 per setting when it had 7
independent observations, overstating confidence threefold. The script now
records **cycle maxima** — time until *all* requested pods are Ready — as the
independent statistic. That is also the operationally correct one: the arbiter
is not done waiting until the last pod it asked for can serve.

---

## 8. Measured constants

All measured on the development laptop, 26 Nov 2026. **Re-measure on any
machine that runs benchmarks.**

| Quantity | Value | Source |
|---|---|---|
| Node allocatable | 8 CPU · 5.79 GiB · 110 pods | `deploy/limits.md` |
| **MAX_PODS** | **15** — 15/15 Running, zero Pending | T1.4.1 |
| Control plane | 474 Mi | T1.2.1 |
| Prometheus | 27 Mi | T1.3.1 |
| Whole cluster (3 pods) | 785 Mi | T1.3 |
| Per pod, idle | ~37 Mi, 3–4 m CPU | T1.4.1 |
| Image size | 260 MB | T1.1.3 |
| Cluster creation | 61 s | T1.2.1 |
| Pod Ready (WARMUP=0) | 2.6 s / 3.47 s | T1.2.2, smoke test |
| p50 / p90 / p99 latency | 8.1 / 14.1 / 34.6 ms | T1.3.3 |
| CPU per request | ~13 ms at 15 000 iterations | T1.1 |

**Still to measure:** capacity constant `C` (T2.2), horizon `H` (T2.3.2), and
the full readiness distribution across warmup settings (T2.3.1, in flight).

---

## 9. Current state

### Live

- k3d cluster `cloudpulse` — target-app 3/3 Ready, Prometheus scraping at 5 s.
- `~/.wslconfig` caps WSL2 at 6 GB with `autoMemoryReclaim=gradual`. Without
  it, WSL2 takes half the machine's RAM and never returns it — the usual cause
  of "my laptop got slow after Docker".

### Progress

**14 tasks done · 1 partial · 54 open**

| Phase | Status |
|---|---|
| 0 — Repo & reproducibility | `[~]` T0.1 done; Makefile, seeds, manifest schema open |
| 1 — Cluster & observability | `[~]` all tasks done; exit gate awaits the k6 sustained run |
| 2 — Capacity & load harness | `[~]` T2.3.1 in flight |
| 3–6 | `[ ]` not started |
| 7 — Evaluation | `[x]` HPA baselines written |
| 8 — Thesis & viva | `[ ]` not started |

### Documents that are now out of date

`docs/planning/` and `docs/viva/` were written before this review. They still
contain the broken replica formula, CPU% as the forecast target, and benchmark
numbers that were never measured.

They are kept as a record of the project's development. **Do not rehearse viva
answers from them.** They get corrected in Phase 8 (T8.1) — not before, because
the real numbers are not knowable until Phase 7 produces them.

The root `README.md` benchmark table now carries a banner marking it
`TARGET VALUES — NOT YET MEASURED`.

---

## 10. Open decisions

### A. HPA measures utilisation against *requests*, not limits — blocking T7.2

The Deployment sets `requests.cpu: 50m`, `limits.cpu: 500m`. HPA computes
utilisation as `usage / requests`, so a pod working at 250m reports **500%
utilisation**. A "70% target" trips at roughly 35m — essentially idle. The
default HPA would hit `maxReplicas` under trivial load.

That would hand CloudPulse a crushing win over HPA that is **pure artifact** —
the kind of result that collapses under one examiner question.

(Our own reactive guard is unaffected: it measures against the limit via
`container_spec_cpu_quota`, a true 0–100% scale.)

**Fix:** bring `requests` and `limits` together, which also gives Guaranteed
QoS and less timing noise. **Catch:** changing `limits.cpu` alters container
startup time, invalidating T2.3.1 readiness numbers and the capacity constant
`C`. Must be settled **before** Phase 2 finalises.

### B. Phase 1 exit gate is deliberately still open

It requires a 10-minute sustained load showing a spike in every query. Only a
600-request burst has been verified. Hand-rolling a worse load generator to
tick the box would prove less than doing it properly — it closes with k6 in
T2.1.

### C. Repository root is one level deeper than ideal

The project root is `Downloads/FinalYearProject-main/`. Cosmetic only; it has
no effect once pushed to GitHub.

---

## 11. What each member does next

| Member | Next action | Blocked by |
|---|---|---|
| **M2** (Cloud) | Finish T2.3.1, derive `H` (T2.3.2), then build the actuator (T5.3.2) | nothing |
| **M3** (Backend) | **T2.1 — k6 open-loop harness.** Highest priority in the project | nothing |
| **M1** (ML) | Read the MAPIE + ACI material; cannot train until `C`, `H` and data exist | Phases 2–3 |
| **M4** (Frontend/Eval) | **Take the experiment runner now, not in week 12.** It is the long pole | nothing |

**T2.1 is the critical path.** It unblocks the Phase 1 exit gate, the capacity
constant `C`, and therefore all of Phases 3, 4 and 5.

**On M4:** the original plan left Member 4 idle until week 9, then loaded them
with the frontend, all statistics and all documentation. The benchmark harness
is the real long pole and had no owner until it was too late. M4 should own
`experiments/` and `analysis/` from now (finding D19).

---

## 12. Appendix: commits and commands

### Commits

```
afb32db  T5.3.1 + T7.2.1/T7.2.2: actuator RBAC and HPA baselines
e702f84  T2.3.1: pod readiness measurement harness
4db257f  T1.3: Prometheus deployed; fix fake latency quantiles
32bab05  Phase 1: k3d cluster live, target-app deployed and verified
7e00488  T1.1.1/T1.1.2 verified: image builds, warmup gate, metrics exclusion
148f41c  Switch cluster runtime from Minikube to k3d; trim monitoring stack
41bf3f6  Phase 1 / T1.1: target app and Dockerfile
24745b4  Add .gitattributes to normalise line endings across the team
5722001  Phase 0 / T0.1: restructure repository into project skeleton
2d1390b  Snapshot: original capstone documents before restructure
```

`2d1390b` is a deliberate pre-restructure checkpoint: every move and rename
since is revertible.

### Environment

| Tool | Version |
|---|---|
| Docker | 29.6.1 |
| k3d | 5.9.0 (k3s v1.35.5) |
| kubectl | bundled |
| Python | 3.12.10 |
| Node | 22.18.0 |
| kubernetes (python) | 37.0.3 |

Not installed, and **not needed**: Minikube, Helm. No Docker Hub or Kubernetes
account is required — images are imported directly into the cluster.

### Daily commands

```powershell
# start / stop the cluster (stop frees ~1.8 GB)
k3d cluster start cloudpulse
k3d cluster stop cloudpulse

# rebuild and roll the app after a code change
docker build -t cloudpulse/target-app:0.1.1 apps/target-app
k3d image import cloudpulse/target-app:0.1.1 -c cloudpulse
kubectl rollout restart deployment/target-app -n cloudpulse

# look around  (always pass --request-timeout; kubectl hangs forever otherwise)
kubectl get pods -A --request-timeout=20s
kubectl top pods -n cloudpulse --request-timeout=20s

# Prometheus UI
kubectl port-forward -n monitoring svc/prometheus 9090:9090
```

### If kubectl hangs

See `deploy/troubleshooting.md`. Almost certainly the
`host.docker.internal` resolution problem; repoint the context at `127.0.0.1`
using the port shown by `docker ps` for `k3d-cloudpulse-serverlb`.
