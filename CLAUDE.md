# CLAUDE.md — CloudPulse

Guidance for Claude Code working in this repository, plus the authoritative
architectural plan. **This file is the single source of truth for scope,
sequencing and status.** When a step completes, flip its status box here in the
same commit that lands the work.

---

## 1. Project identity

**CloudPulse** — a load-aware, uncertainty-aware *predictive* autoscaler for
Kubernetes. It forecasts offered load H seconds ahead, converts the forecast's
conformal **upper bound** into a replica count via a measured capacity model,
and actuates the Deployment before traffic arrives — with a reactive guard
underneath it.

- Team A8-8, Mohan Babu University, capstone.
- Base paper (**verified against Crossref on 2026-11-26** — title, journal,
  volume 38, issue 5, pages 3277–3290, May 2026 and all seven authors match;
  `https://doi.org/10.1109/TKDE.2026.3674583` resolves to IEEE Xplore
  document 11435627):
  Chen, Luo, Huang, Jiang, Shi, Zhang, Gao — *"Uncertainty-Aware Online Time
  Series Multi-Step Forecasting Framework in Cloud Systems"*, IEEE TKDE.
- Our contribution is **operationalisation**: the paper evaluates offline on
  traces and never closes the loop. We close it on a real cluster and measure
  what that costs and buys.

---

## 2. Non-negotiable working rules

1. **No invented numbers.** A latency, cost, coverage or replica figure may
   appear in the README, thesis, slides or dashboard **only** if it was
   produced by a committed experiment run and can be traced to a file under
   `experiments/results/<run_id>/`. Target values are allowed but must be
   labelled `TARGET (not yet measured)`. The current README's benchmark table
   violates this and must be relabelled — see §4, D1.
2. **Forecast replica-independent quantities only.** Never train on, or scale
   from, *average CPU utilisation percent*. See §3.2 — this is the single
   biggest correctness bug in the existing design.
3. **One controller per Deployment.** CloudPulse, HPA and static are compared
   in **separate sequential runs**, never simultaneously on the same object.
4. **Client-side latency, open-loop load.** Latency is what the load generator
   measured, under a fixed arrival rate. See §3.5.
5. **Pin everything.** `requirements.txt` with `==`, `package-lock.json`
   committed, image tags by explicit version — never `:latest`.
6. Keep research code in `ml/` and serving code in `services/`; serving code
   imports from a shared `cloudpulse_core` package rather than duplicating
   feature logic. Train/serve feature skew is the classic way this project
   fails silently.

---

## 3. Corrected architecture

### 3.1 Component map

```
 load/            k6 (open-loop) ──HTTP──► apps/target-app (Deployment, N pods)
                                                 │ /metrics
                                                 ▼
 deploy/          Prometheus (scrape 5s, 2h retention)
                                                 │ PromQL
                                                 ▼
 services/collector  ──► Postgres `telemetry` (+ Parquet export for training)
                                                 │ recent window
                                                 ▼
 services/forecaster  CQR + Adaptive Conformal Inference ──► [lo, point, hi] of D(t+H)
                                                 │
                                                 ▼
 services/arbiter   capacity model → control layer (hysteresis, dwell, clamp)
                        │                        │
                        │ patch scale            │ WS events
                        ▼                        ▼
                  Kubernetes API           dashboard/ (Next.js)
```

### 3.2 The forecasting target — corrected

The existing plan computes `replicas = ceil(predicted_upper_cpu / target_cpu_per_pod)`.
**This is dimensionally wrong and will scale the cluster *down* during a spike.**
With a predicted upper CPU of 92% and a 70% target it yields
`ceil(92/70) = 2` replicas — regardless of whether 3 or 30 pods are running.

There is also a deeper problem: average utilisation is *endogenous*. It is a
function of the replica count the controller itself sets, so a model trained on
it learns its own past control policy, and the signal shifts underneath the
model every time it acts.

**Fix — forecast demand, not utilisation:**

- Primary signal `D(t)` = **aggregate offered load in requests/sec** across the
  Deployment. Replica-independent.
- Secondary signal = **total CPU millicores summed over all pods**
  (`sum(rate(container_cpu_usage_seconds_total[1m]))`). Also replica-independent.
- Capacity `C` = sustainable RPS a single pod serves at target utilisation
  `U* = 0.70`, **measured** in Phase 2, not assumed.

```
replicas_pred  = ceil( D_upper(t+H) / C )
replicas_final = clamp( max(replicas_pred, replicas_reactive), MIN, MAX )
```

If a utilisation-based form is ever needed as a fallback, it must be the ratio
form Kubernetes itself uses:
`ceil( current_replicas * u_predicted / u_target )`.

### 3.3 Horizon H — derived, not asserted

"60 seconds" is currently an assumption. H must satisfy:

```
H  >=  t_ready_p95  +  t_decision_interval  +  t_safety
```

`t_ready_p95` is the measured p95 from pod creation to Ready (Phase 2, T2.3).
If pods are Ready in 4 s on a warm k3d node, H = 60 s is not justified and
the whole premise collapses — which is why Phase 2 also *introduces a
controlled, declared startup delay* and sweeps it (§3.6).

### 3.4 Uncertainty — what is actually guaranteed

Split conformal prediction guarantees coverage under **exchangeability**. Time
series are not exchangeable, so plain split-CQR on this data **does not**
deliver the advertised 90%. The base paper handles this with adaptive/online
machinery; we must too.

- Base learner: **quantile** gradient boosting (pinball loss) at
  `tau = 0.05 / 0.50 / 0.95`.
- Wrap with **CQR**, then run **Adaptive Conformal Inference** online:
  `alpha_{t+1} = alpha_t + gamma * (alpha - err_t)`, so the interval widens
  automatically under drift.
- Report **marginal coverage AND conditional coverage restricted to spike
  windows**. Marginal 90% that fails exactly during spikes is worthless here,
  and this is the question a sharp examiner will ask.
- **Do not stack** a 3:1 asymmetric training loss *and* a conformal upper bound
  without measuring it — that double-counts safety margin, and is why the
  README's own target table shows CloudPulse costing *more* than HPA. Treat
  asymmetry as expressed through the quantile level `tau`; keep the explicit
  asymmetric loss as a measured ablation (Phase 7, T7.3).

### 3.5 Control layer — missing from the current design

A raw `max()` recomputed every 30 s from a noisy upper bound will oscillate.
Pod churn itself degrades latency, and Kubernetes HPA ships with a 5-minute
scale-down stabilisation window, so omitting one also makes the comparison
unfair to us. Required:

- **Asymmetric response**: scale up immediately; scale down only after a 300 s
  stabilisation window (take the max of desired over that window).
- **Minimum dwell time** 60 s between actuations.
- **Deadband**: ignore changes of < 1 replica or < 10% of current.
- **Degradation modes**: forecaster timeout (> 2 s) or stale prediction
  (> 2 intervals old) → fall back to reactive-only and log the event. Failed
  `patch_scale` → exponential backoff, never silent.

### 3.6 Startup latency must be a declared experimental variable

If the image is already imported into the single k3d node, pods become Ready in
seconds. Reactive HPA then performs *fine*, and CloudPulse shows no benefit —
the experiment would honestly refute our own thesis.

Do not hide this. Make it the headline result instead: inject a **declared,
configurable warmup delay** into the target app (`WARMUP_SECONDS`, enforced by
the readiness probe) and **sweep it** across `{0, 5, 15, 30, 60}` s. The
deliverable is a curve showing CloudPulse's advantage over HPA growing with
startup cost, and identifying the break-even point below which predictive
scaling is not worth it. That is a stronger and more defensible finding than
any single win claim.

### 3.7 Stack decisions

| Area | Choice | Note |
|---|---|---|
| Cluster | **k3d** (K3s in Docker), single node | replaces Minikube: ~600 MB control plane vs ~2 GB, ~20 s start vs ~2 min, and `metrics-server` is built in (HPA needs it). K3s is CNCF-certified Kubernetes, so the viva claim "we deployed on a real Kubernetes cluster" holds. The blueprint's "2+ nodes" is dropped — it buys nothing and costs RAM |
| Target app | FastAPI + `prometheus-fastapi-instrumentator` | pod Guaranteed QoS `250m` CPU / `128Mi` (`requests == limits`) — see Phase 1, T1.4 |
| Metrics | **plain Prometheus manifest**, `kubernetes_sd_configs` | NOT kube-prometheus-stack: that bundles Grafana + Alertmanager (~500 MB) which the Phase 6 dashboard makes redundant. No Helm, no operator, no ServiceMonitor CRDs. Retention 2h — experiments run ~15 min and the collector persists to Postgres anyway |
| Storage | **plain PostgreSQL 16** | TimescaleDB dropped — ops risk, no research value at this data volume |
| Load | **k6** (open-loop, constant arrival rate) | Locust is closed-loop by default and hides overload latency (coordinated omission). Keep Locust only for interactive demo flavour |
| ML | scikit-learn / LightGBM quantile + MAPIE | pin MAPIE exactly; its v1 API renamed the regressors |
| Dashboard | Next.js + Recharts, WebSocket | Phase 6 only; it is a presentation layer, not a contribution |

### 3.7.1 What runs inside the cluster, and what does not

Only two things belong in the cluster, because only they have to be there:

| In the cluster | Why |
|---|---|
| `target-app` | it is the thing being scaled |
| Prometheus | it scrapes pods from inside the pod network |

Everything else — Postgres, collector, forecaster, arbiter, dashboard — runs on
the **host** during development. The arbiter reaches the cluster through the
kubeconfig just as well from outside it.

Two reasons, and the second matters more:

1. Memory. Keeps the cluster near ~2 GB instead of ~5.4 GB on a 16 GB laptop.
2. Iteration speed. No rebuild-redeploy cycle to test a change to the control
   logic — edit, save, run. You will change the arbiter a hundred times.

They get containerised in Phase 6 (T6.3) for the demo, which is the point at
which "it works on my host" stops being good enough.

**Laptop budget (measured 2026-11-26: 15.6 GB total, only 4.1 GB free at idle).**
`~/.wslconfig` caps WSL2 at 6 GB with `autoMemoryReclaim=gradual`; without it
WSL2 takes half the RAM and never hands it back. Close Chrome tabs, ChatGPT and
WhatsApp before benchmark runs — background load adds noise to latency numbers.

### 3.8 Repository layout

Created in Phase 0 (T0.1). Each directory carries its own `README.md` stating
its purpose, owning member and the phase that fills it.

```
README.md  CLAUDE.md  .gitignore          <- Makefile, requirements.txt: T0.2
cloudpulse_core/   shared features/seeds/config, imported by ml AND services
apps/target-app/   FastAPI workload under test (WARMUP_SECONDS lives here)
services/
  collector/       Prometheus -> Postgres
  forecaster/      CQR + ACI, POST /predict
  arbiter/         capacity model, control layer, K8s actuator
ml/
  data/raw/  data/processed/  models/     (all gitignored, regenerated)
  notebooks/
deploy/
  k8s/  helm/      + capacity.yaml, readiness.md, limits.md, queries.md
load/profiles/     k6 open-loop RPS schedules
experiments/results/<run_id>/             manifest + summaries
analysis/figures/  metrics.py, stats.ipynb
dashboard/         Next.js (off critical path)
tests/  scripts/
docs/
  planning/  reports/  presentations/  viva/  assets/
```

`docs/README.md` maps every renamed file back to its original name.

---

## 4. Review of the current repository — findings to resolve

Status of each is tracked in the phase tables below.

| # | Finding | Severity |
|---|---|---|
| D1 | Benchmark table presents **targets as measured results**; also self-contradictory (static 45 ms vs CloudPulse <100 ms, yet claims parity; cost 48% vs HPA 45%, yet claims "optimal") | Critical — viva risk |
| D2 | Replica formula dimensionally wrong (§3.2) | Critical |
| D3 | Forecast target is endogenous utilisation (§3.2) | Critical |
| D4 | No capacity model — nothing converts a forecast into a pod count | Critical |
| D5 | Zero code exists; repo is 14 documents and a `.gitignore`, and is not an initialised git repo locally | Critical |
| D6 | No control layer: no cooldown, hysteresis, dwell or deadband → thrash (§3.5) | High |
| D7 | Conformal coverage claim invalid under non-exchangeability (§3.4) | High |
| D8 | Asymmetric loss and conformal bound double-count margin (§3.4) | High |
| D9 | Closed-loop Locust masks the latency the project exists to measure (§3.5) | High |
| D10 | Horizon H asserted, not derived from measured pod readiness (§3.3) | High |
| D11 | Baseline is *untuned default* HPA — a straw man. Must tune HPA `behavior:` and add a naive-predictor ablation and an oracle upper bound | High |
| D12 | No repetitions or seeds defined, yet ANOVA/t-tests are promised; n=1 supports no statistics | High |
| D13 | `MAX_PODS=20` plus Prometheus, Grafana, Postgres and Next.js will not schedule on a student laptop; pods go `Pending` and the benchmark becomes meaningless | High |
| D14 | Cost in `$/hour` on a local k3d cluster is fictional; use **pod-seconds** as the primary metric, dollars only as a derived display with stated assumptions | Medium |
| D15 | No failure/degradation handling for forecaster or K8s API (§3.5) | Medium |
| D16 | Alibaba trace resolution (1 min) mismatches a 30 s decision loop, and its domain differs from our pods; role must be narrowed to pre-training/supplementary | Medium |
| D17 | Chronological split and leakage discipline unstated; the calibration set must sit between train and test in time | Medium |
| D18 | No reproducibility infra: no lockfiles, Makefile, seeds, run manifests or CI | Medium |
| D19 | Workload imbalance: Member 1 owns all ML alone; Member 4 idles until W9 then owns frontend + all statistics + all documents | Medium |
| D20 | Repo hygiene: duplicated nested `FinalYearProject-main/`, `.gitignore` has no Python/Node entries, no LICENSE, binaries at root | Low |

**What is genuinely strong and should be kept:** the citation is accurate and
verifiable; the research-gap framing (theory → closed loop) is correct and
defensible; the hybrid predictive/reactive arbiter is the right shape; and the
blueprint's "build infrastructure before training on real data" instinct is
exactly right.

---

## 5. Status legend

`[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked (add reason)

A box flips to `[x]` **only** when the named artifact exists at the named path
and its check passes. Update the box in the same commit as the work.

---

## 6. Phase plan

Phases are strictly ordered. A phase begins only when its predecessor's **Exit
gate** is `[x]`. Weeks are relative (W1 = first working week); anchor them to
real dates in §8 once the submission deadline is fixed.

---

### PHASE 0 — Repository & reproducibility foundation (W1, 2 days)

**Goal:** a repo that can hold an experiment honestly.
**Depends on:** nothing. **Resolves:** D5, D18, D20.

**T0.1 Initialise and restructure** — DONE 2026-11-26
- [x] 0.1.1 `git init`; create the §3.8 directory skeleton with `.gitkeep` files.
      *Done when:* `git log` has an initial commit and all directories exist.
      → commit `2d1390b` snapshots the original documents before any move, so
      the whole restructure is revertible. Skeleton matches §3.8.
- [x] 0.1.2 Move all `.docx/.pptx/.pdf/.md` deliverables into `docs/`; delete the
      duplicated nested `FinalYearProject-main/` directory.
      *Done when:* the repo root holds only `README.md`, `CLAUDE.md`,
      `.gitignore`, `Makefile` and the code directories.
      → moved with `git mv` (history preserved) and renamed to kebab-case;
      mapping in `docs/README.md`. Nested folder removed. `Makefile` is T0.2.2.
- [x] 0.1.3 Extend `.gitignore`: `__pycache__/ venv/ .venv/ node_modules/ *.pkl
      *.joblib .env *.parquet experiments/results/**/raw/`.
      *Done when:* `git status` is clean after a training run.
      → written and verified clean for the current tree; the post-training-run
      half of this check is only exercisable from Phase 4 — re-verify there.

**T0.2 Environment pinning**
- [x] 0.2.1 `requirements.txt` with `==` pins for every Python dependency;
      Python version fixed in `.python-version`.
      *Done when:* a fresh venv installs and `python -c "import mapie, lightgbm"` succeeds.
      → `.venv` created on Python `3.12.4`, `requirements.txt` pinned (`MAPIE==1.6.0`,
      `lightgbm==4.7.0`, `fastapi==0.115.6`, `kubernetes==37.0.3`, etc.), and
      `import mapie, lightgbm` verified.
- [~] 0.2.2 `Makefile` targets: `setup cluster-up deploy collect train serve
      bench analyse clean`.
      *Done when:* `make help` lists all targets and `make setup` completes.
      → `Makefile` and Windows-compatible `scripts/cpctl.py` implemented and
      verified via `cpctl.py help`, `cpctl.py setup`, `cpctl.py test`, and
      `tests/test_cpctl.py`. GNU `make` itself is not installed on the 7.9 GB
      Windows laptop; verify `make help` on a machine with `make`.
- [~] 0.2.3 Global seed utility in `cloudpulse_core/seeds.py`.
      *Done when:* two runs of the training script produce byte-identical models.
      → `cloudpulse_core/seeds.py` implemented; `tests/test_seeds.py` verifies
      byte-identical LightGBM quantile models across two runs. Re-verify on
      `ml/train.py` in Phase 4 (`T4.2`).

**T0.3 Experiment manifest contract**
- [x] 0.3.1 Define the `experiments/results/<run_id>/manifest.json` schema: git
      SHA, scaler under test, traffic profile, `WARMUP_SECONDS`, seed,
      start/end time, cluster resources.
      *Done when:* the schema file is committed with one hand-written example
      that validates against it.
      → `experiments/manifest.schema.json`, `experiments/manifest.example.json`,
      and `experiments/manifest.py` implemented and verified by `tests/test_manifest.py`.

> **EXIT GATE 0:** `[~]` Fresh `.venv` setup (`scripts/cpctl.py setup`), shared
> `cloudpulse_core` seeds/config, and manifest schema all pass locally; only
> running `make` directly (Windows laptop lacks GNU `make`) and testing
> `seeds.py` inside `ml/train.py` (Phase 4) remain.

---

### PHASE 1 — Cluster, target app & observability (W1–W3)

**Goal:** a cluster that runs the app and tells the truth about it.
**Depends on:** Phase 0. **Owner:** M2 (infra) + M3 (app). **Resolves:** D13.

**T1.1 Target application**
- [x] 1.1.1 FastAPI app: `/` (tunable CPU-bound work), `/healthz`, `/readyz`, `/metrics`.
      *Done when:* `curl localhost:8000/metrics` returns `http_requests_total`.
      → verified in-container: 6 work requests produced
      `http_requests_total{handler="/api/work"} 6.0`, and 4 probe calls produced
      **zero** entries — probe traffic cannot contaminate the forecast signal.
- [x] 1.1.2 Implement `WARMUP_SECONDS` (env): `/readyz` returns 503 until elapsed (§3.6).
      *Done when:* with `WARMUP_SECONDS=30` the container reports NotReady for ~30 s.
      → verified at 8 s: t=3 s gave healthz 200 / readyz 503; t=10 s gave 200 / 200.
- [x] 1.1.3 Dockerfile, multi-stage, pinned base; import into the k3d cluster.
      *Done when:* `k3d image import` succeeds and a pod runs the tagged image.
      → `cloudpulse/target-app:0.1.0` (260 MB, non-root) imported; 3/3 pods Running.

**T1.2 Cluster**
- [x] 1.2.1 `k3d cluster create cloudpulse` (servers 1, Traefik disabled).
      *Done when:* `kubectl get nodes` shows Ready and `kubectl top pods` returns
      values — metrics-server ships with K3s, nothing to enable.
      → created in 61 s; node Ready; only 3 system pods; Traefik confirmed absent;
      `kubectl top` works. Control plane 474 Mi (vs ~2 GB for Minikube).
      One gotcha hit and documented in `deploy/troubleshooting.md`: k3d wrote
      `host.docker.internal`, which resolved to a VPN adapter; repointed the
      context at `127.0.0.1`.
- [~] 1.2.2 Deployment + Service manifests with explicit `resources.requests` =
      `250m` CPU / `128Mi` and matching limits (Guaranteed QoS).
      *Done when:* `kubectl get deploy target-app` shows 3/3 Ready.
      → Previously 3/3 Ready under `50m`/`64Mi` (`500m`/`128Mi` limits) with
      Service routing across all 3 pods verified. Updated 2026-11-27 to
      `250m`/`128Mi` Guaranteed QoS (`requests == limits`) so HPA `usage /
      requests` is fair — pending re-apply on the live cluster.
      `imagePullPolicy: IfNotPresent` is load-bearing — the image is imported,
      not published to any registry.

**T1.3 Observability**
- [x] 1.3.1 Apply the plain Prometheus manifest: `scrape_interval: 5s`,
      `--storage.tsdb.retention.time=2h`, `kubernetes_sd_configs` limited to our
      namespaces. No Helm, no operator.
      *Done when:* the `monitoring` pod is Running under 400 MB.
      → Running at **27 Mi**. Filtering cAdvisor to two series in one namespace
      is what kept it there; unfiltered it would exceed 1 Gi.
- [x] 1.3.2 Annotate the target-app pods for discovery
      (`prometheus.io/scrape`, `/port`, `/path`).
      *Done when:* the app appears UP under Prometheus → Targets.
      → 4/4 targets up: 3 pods by annotation + kubelet-cAdvisor via API proxy.
- [x] 1.3.3 Record the canonical PromQL set in `deploy/queries.md` (RPS, total
      CPU millicores, per-pod utilisation, p50/p90/p99, ready replicas).
      *Done when:* each query returns non-empty data under load.
      → all return data: RPS 5.59, CPU 183 m, p50/p90/p99 8.1/14.1/34.6 ms,
      SLA violation 0.0%, pods 3.
      **Defect found and fixed here:** the library's default latency histogram
      has only buckets (0.1, 0.5, 1.0). Every request lands in the first one,
      so `histogram_quantile` interpolated a fixed p50 = 50 ms and p99 = 99 ms
      regardless of truth — the real p99 was 34.6 ms, a 2.9x error that looked
      entirely plausible. `LATENCY_BUCKETS` in the app overrides them and pins
      an exact 0.1 boundary so the SLO is counted, not estimated.

**T1.4 Capacity sanity (resolves D13)**
- [~] 1.4.1 Scale manually to the intended `MAX_PODS` and observe scheduling.
      *Done when:* all pods reach Running with **zero** `Pending`; if any pend,
      lower `MAX_PODS` and record the real ceiling in `deploy/limits.md`. This
      number overrides the README's 20.
      → Previously 15/15 Running (`0 Pending`) at `50m`/`64Mi` requests. Updated
      2026-11-27 to `250m`/`128Mi` Guaranteed QoS (`3750m` / `1920Mi` at 15
      pods vs `8` CPU / `5.79 GiB` allocatable); pending re-verification on the
      cluster laptop. Recorded in `deploy/limits.md`.

> **EXIT GATE 1:** `[~]` A 10-minute manual load produces a visible spike in all
> queries from T1.3.3, and `MAX_PODS` is empirically proven schedulable.
> → `MAX_PODS` proven (15/15, zero Pending) and every query verified against a
> short 600-request burst. The sustained 10-minute profiled run is deliberately
> deferred to T2.1, where k6 generates it properly — hand-rolling a worse load
> generator now to tick this box would prove less, not more.

---

### PHASE 2 — Capacity characterisation & load harness (W4) — **NEW, BLOCKING**

**Goal:** the constants every later phase divides by.
**Depends on:** Phase 1. **Owner:** M3 + M2. **Resolves:** D4, D9, D10.

**T2.1 Open-loop load generator**
- [ ] 2.1.1 k6 scripts with the `constant-arrival-rate` executor for three
      profiles: `gradual`, `flash`, `diurnal`; the RPS schedule declared in
      config, not code.
      *Done when:* achieved RPS tracks target RPS within 5% while the app is healthy.
- [ ] 2.1.2 Emit per-request client-side latency to a JSON summary per run.
      *Done when:* `experiments/results/<id>/k6_summary.json` carries p50/p90/p99.

**T2.2 Saturation test → capacity constant C**
- [ ] 2.2.1 Pin the Deployment to exactly 1 replica; step RPS upward until
      per-pod CPU reaches 70% with p99 still inside the SLO.
      *Done when:* `deploy/capacity.yaml` records `pod_capacity_rps`, with the
      raw curve committed under `experiments/results/`.
- [ ] 2.2.2 Repeat at 3 replicas to confirm near-linear scaling.
      *Done when:* measured 3-pod capacity is within 15% of `3 * C`; if not,
      document the bottleneck (Service / kube-proxy / node) before proceeding.

**T2.3 Pod readiness characterisation → horizon H**
- [ ] 2.3.1 Measure creation→Ready for 20 scale-ups at each
      `WARMUP_SECONDS ∈ {0,5,15,30,60}`.
      *Done when:* `deploy/readiness.md` holds p50/p95 per setting.
- [ ] 2.3.2 Derive H from §3.3 using p95, and record the arithmetic.
      *Done when:* `H` is written into `services/arbiter/config.yaml` with a
      one-line justification citing T2.3.1.

> **EXIT GATE 2:** `[ ]` `C`, `MAX_PODS` and `H` are all measured numbers in
> committed config. **No ML work starts before this gate** — without `C`, a
> forecast cannot become a replica count.

---

### PHASE 3 — Telemetry dataset & feature pipeline (W5)

**Goal:** training data that matches what serving will see.
**Depends on:** Phase 2. **Owner:** M1 + M3. **Resolves:** D16, D17.

**T3.1 Collector**
- [ ] 3.1.1 Postgres schema `telemetry(ts, rps, cpu_millicores_total, mem_bytes,
      p50, p90, p99, ready_replicas, profile, run_id)`.
      *Done when:* `make collect` writes rows at a steady 5 s cadence.
- [ ] 3.1.2 Scraper service using `prometheus-api-client`, resilient to gaps.
      *Done when:* a 30-minute run has no missing timestamps and no duplicates.

**T3.2 Dataset generation**
- [ ] 3.2.1 Run all three profiles ×3 at a **fixed** replica count, to gather
      unconfounded demand data.
      *Done when:* ≥ 6 hours of 5 s telemetry is exported to `ml/data/raw/*.parquet`.
- [ ] 3.2.2 Narrow the Alibaba trace to supplementary pre-training only;
      resample to 5 s or drop it, and document the decision.
      *Done when:* `ml/data/README.md` states each dataset's role and resolution.

**T3.3 Features (shared module)**
- [ ] 3.3.1 `cloudpulse_core/features.py`: lags, rolling mean/std, first
      difference, slope, time-of-day sin/cos — all computed on `D(t)` (§3.2).
      *Done when:* the same function is importable by both `ml/` and
      `services/forecaster/`.
- [ ] 3.3.2 Chronological split: train / calibration / test, strictly ordered in
      time, with a gap of ≥ H to prevent leakage.
      *Done when:* a unit test asserts `max(train.ts) + H <= min(cal.ts)`, and
      likewise between calibration and test.

> **EXIT GATE 3:** `[ ]` One command rebuilds the dataset from raw parquet; the
> leakage test is green; no feature code is duplicated between training and serving.

---

### PHASE 4 — Forecasting & uncertainty (W6–W7)

**Goal:** calibrated interval forecasts of `D(t+H)`.
**Depends on:** Phase 3. **Owner:** M1, reviewed by M4. **Resolves:** D3, D7, D8.

**T4.1 Baselines first**
- [ ] 4.1.1 Implement naive (last value), seasonal naive and ARIMA baselines.
      *Done when:* `analysis/baselines.md` reports RMSE/MAE/SMAPE on the test
      split. These are also the ablation opponents in Phase 7 — do not skip them.

**T4.2 Quantile base learner**
- [ ] 4.2.1 LightGBM/GBR quantile models at `tau = 0.05/0.50/0.95`.
      *Done when:* the median model beats the best Phase-4.1 baseline on test
      RMSE, with the margin recorded.

**T4.3 Conformalisation**
- [ ] 4.3.1 CQR on the calibration split via pinned MAPIE.
      *Done when:* marginal coverage on test lands within [0.88, 0.95] at `alpha=0.10`.
- [ ] 4.3.2 Add the Adaptive Conformal Inference online update (§3.4).
      *Done when:* under an injected distribution shift, ACI recovers to target
      coverage within 20 steps while static CQR does not — plot committed.
- [ ] 4.3.3 **Conditional coverage during spike windows.**
      *Done when:* `analysis/coverage.md` reports coverage restricted to the top
      10% of `dD/dt` windows, separately from marginal coverage.

**T4.4 Asymmetry, measured not assumed**
- [ ] 4.4.1 Train an asymmetric-loss variant; compare interval width and
      over-provisioning against plain CQR (§3.4, D8).
      *Done when:* a table shows mean interval width and pod-seconds cost for
      both, and `config.yaml` records which one ships and why.

**T4.5 Serving**
- [ ] 4.5.1 FastAPI `/predict` returning
      `{point, lower, upper, horizon_s, model_version, computed_at}`.
      *Done when:* p99 service latency < 200 ms over 1000 calls.
- [ ] 4.5.2 Containerise and deploy into the cluster.
      *Done when:* the arbiter can reach it by Service DNS.

> **EXIT GATE 4:** `[ ]` The deployed `/predict` meets coverage targets both
> marginally and conditionally, and beats the naive baseline measurably.

---

### PHASE 5 — Decision & control plane (W8–W9)

**Goal:** safe, non-oscillating actuation.
**Depends on:** Phases 2 and 4. **Owner:** M3 (logic) + M2 (K8s).
**Resolves:** D2, D6, D15.

**T5.1 Capacity-based conversion**
- [ ] 5.1.1 Implement `replicas = ceil(D_upper / C)` using the Phase-2 constant (§3.2).
      *Done when:* unit tests cover the worked examples in §3.2, including the
      case the old formula got wrong.

**T5.2 Control layer**
- [ ] 5.2.1 Reactive guard: per-pod CPU > 85% → `current + 2`.
      *Done when:* a synthetic unpredicted spike triggers it within one interval.
- [ ] 5.2.2 Scale-down stabilisation (300 s max-window), min dwell 60 s,
      deadband (§3.5).
      *Done when:* replaying a noisy prediction trace produces ≤ 1 actuation per
      5 minutes during steady load, verified by test.
- [ ] 5.2.3 Clamp to `[MIN_PODS, MAX_PODS]` from `deploy/limits.md`.
      *Done when:* the arbiter never requests an unschedulable count.

**T5.3 Actuator and RBAC**
- [~] 5.3.1 ServiceAccount + Role for `deployments/scale` (get, patch) only.
      *Done when:* the pod scales the Deployment with no cluster-admin binding.
      → `deploy/k8s/30-actuator-rbac.yaml` written and validated server-side.
      Namespaced Role, not ClusterRole. Stays `[~]` until an arbiter actually
      scales with it (T5.3.2).
- [ ] 5.3.2 `patch_namespaced_deployment_scale` with retry and backoff.
      *Done when:* an induced API failure retries and logs, without crashing.

**T5.4 Degradation modes**
- [ ] 5.4.1 Forecaster timeout > 2 s, or a prediction older than 2 intervals →
      reactive-only, logged with reason.
      *Done when:* killing the forecaster pod leaves the arbiter running and
      reactively scaling, and recovery is automatic.

**T5.5 Decision logging**
- [ ] 5.5.1 Persist every decision (inputs, bounds, chosen replicas, mode, reason).
      *Done when:* a run's decision log reconstructs the replica timeline exactly.

> **EXIT GATE 5:** `[ ]` A `flash` profile run scales pods up **before** the RPS
> peak, with no oscillation during steady state and no manual intervention.

---

### PHASE 6 — Closed-loop integration & dashboard (W10–W11)

**Goal:** the system runs end to end and can be watched.
**Depends on:** Phase 5. **Owner:** M4 (UI) + M3 (WS).

**T6.1 Streaming**
- [ ] 6.1.1 WebSocket broadcasting telemetry, predictions and decisions.
      *Done when:* `wscat` shows messages at the decision cadence.

**T6.2 Dashboard**
- [ ] 6.2.1 Next.js + Recharts prediction-cone chart (actual, point, shaded band).
      *Done when:* the band visibly leads the actual line during a `flash` run.
- [ ] 6.2.2 Replica gauge, client-side p99 latency panel, decision log table.
      *Done when:* each panel updates live and matches the database for the same run.
- [ ] 6.2.3 Pod-seconds counter (not dollars by default), with a toggle to a
      dollar estimate that **prints its price assumption on screen** (D14).
      *Done when:* the cost shown equals the integral of replicas over time.

**T6.3 Full-loop rehearsal**
- [ ] 6.3.1 One scripted end-to-end demo run, `make demo`.
      *Done when:* a single command brings up the loop and drives a `flash`
      profile with no manual steps.

> **EXIT GATE 6:** `[ ]` `make demo` runs clean twice in a row on a fresh cluster.

---

### PHASE 7 — Experimental evaluation (W12–W14)

**Goal:** defensible evidence, including evidence against us.
**Depends on:** Phase 6. **Owner:** M4 (lead) + all.
**Resolves:** D1, D11, D12, D14.

**T7.1 Experiment runner**
- [ ] 7.1.1 `experiments/run.py` taking (scaler, profile, warmup, seed, rep),
      enforcing a clean cluster and a fixed settle period between runs, and
      writing the Phase-0 manifest.
      *Done when:* a run is fully reproducible from its manifest alone.
- [ ] 7.1.2 **Sequential execution only** — assert no other scaler is active.
      *Done when:* the runner refuses to start if an HPA exists on the target.

**T7.2 Tuned baselines (resolves D11)**
- [x] 7.2.1 Default HPA (CPU 70%). → `deploy/baselines/hpa-default.yaml`, validated.
- [x] 7.2.2 **Tuned** HPA with `behavior:` policies and a tuned threshold.
      → `deploy/baselines/hpa-tuned.yaml`: CPU 50%, instant scale-up, +100%/+4
      pods per 15 s, scale-down window matched to ours so neither side gets an
      unfair edge on pod-seconds.
- [ ] 7.2.3 Static over-provisioned, sized from Phase-2 capacity to peak demand.
- [ ] 7.2.4 Naive predictive scaler (last value + margin) — isolates the value of ML.
- [ ] 7.2.5 Oracle scaler fed the true future demand — the upper bound on any predictor.
      *Done when:* all five configurations run under all three profiles.

**T7.3 Ablations**
- [ ] 7.3.1 Point forecast vs conformal upper bound — does uncertainty pay?
- [ ] 7.3.2 Asymmetric loss on vs off (§3.4).
- [ ] 7.3.3 ACI on vs static conformal, under drift.
      *Done when:* each ablation reports SLA violation % and pod-seconds.

**T7.4 Startup-latency sweep (the headline, §3.6)**
- [ ] 7.4.1 Sweep `WARMUP_SECONDS ∈ {0,5,15,30,60}` for CloudPulse vs tuned HPA.
      *Done when:* a curve of advantage vs startup cost exists **and the
      break-even point is stated**, including the honest finding that below it
      predictive scaling does not pay.

**T7.5 Statistics (resolves D12)**
- [ ] 7.5.1 n = 5 repetitions per cell, different seeds. Budget the time:
      5 scalers × 3 profiles × 5 reps × ~15 min ≈ 19 h of machine time — schedule it.
      *Done when:* every reported cell has n = 5.
- [ ] 7.5.2 Report median + IQR, not mean alone, for latency.
- [ ] 7.5.3 Significance testing appropriate to the data: Kruskal–Wallis with
      Dunn post-hoc (latency is not normal), plus effect sizes and confidence intervals.
      *Done when:* `analysis/stats.ipynb` outputs the results table with
      p-values, effect sizes and n stated for each cell.

**T7.6 Primary metrics, fixed now**
- [ ] 7.6.1 SLA violation rate (% requests > SLO, client-side); p99 latency;
      **pod-seconds**; actuation count; mean time-to-ready-capacity; coverage.
      *Done when:* `analysis/metrics.py` computes all six from a run directory.

> **EXIT GATE 7:** `[ ]` Every number in `analysis/results.csv` traces to a
> `run_id`, and the README benchmark table is **regenerated from that file**,
> replacing today's target values (D1).

---

### PHASE 8 — Thesis, paper & viva (W15–W16)

**Goal:** submission-ready, and rehearsed against hard questions.
**Depends on:** Phase 7. **Owner:** M4 lead, all contribute.

**T8.1 Documents**
- [ ] 8.1.1 Rewrite `README.md` from measured results; delete every unlabelled
      target number (D1).
      *Done when:* each table cell cites a `run_id`.
- [ ] 8.1.2 Thesis in MBU format; IEEE-format short paper.
      *Done when:* supervisor sign-off is recorded.
- [ ] 8.1.3 Reproducibility appendix: exact commands from clone to figures.
      *Done when:* a teammate reproduces one figure on a clean machine following
      it alone.

**T8.2 Viva preparation**
- [ ] 8.2.1 Written answers for the predictable attacks: why conformal coverage
      holds under non-exchangeability (§3.4); why not just tune HPA (T7.2.2);
      what happens when the model is wrong (§3.5); why open-loop load (§3.5);
      and where CloudPulse *loses* (T7.4 break-even).
      *Done when:* each answer cites a figure or table from Phase 7.
- [ ] 8.2.2 Demo rehearsed 3× end to end, with a recorded fallback video in case
      the live cluster misbehaves.
      *Done when:* the video is committed and the demo has run three times
      without intervention.

> **EXIT GATE 8:** `[ ]` Submitted; demo rehearsed; fallback video exists.

---

## 7. Dependency summary

```
P0 ─► P1 ─► P2 ─► P3 ─► P4 ─► P5 ─► P6 ─► P7 ─► P8
                  └──────────────────┘
                   P5 also consumes P2 (capacity C, horizon H)
```

P2 is the new blocking gate: it produces `C`, `H` and the real `MAX_PODS`.
P4 (ML) and P5 (control) both consume it. Nothing downstream is trustworthy
without it.

**Critical path:** P1 → P2 → P3 → P4 → P5 → P7. The dashboard (P6) is off the
critical path and may be cut to a minimal page if time runs short — it is
presentation, not contribution.

**Rebalanced ownership (resolves D19):** M4 owns the experiment runner and
metrics code from **Phase 2 onward**, not from W12 — the benchmark harness is
the long pole and currently has no owner until it is too late. M1 pairs with M3
on `cloudpulse_core/features.py` so ML is not a single point of failure.

---

## 8. Current status

Updated 2026-11-27.

| Phase | Status |
|---|---|
| 0 — Repo & reproducibility | `[~]` T0.1, T0.2.1, T0.3.1 done; T0.2.2 (`cpctl.py` verified, `make` pending on Linux/WSL) & T0.2.3 (`seeds.py` verified on LightGBM, re-check on `ml/train.py`) |
| 1 — Cluster & observability | `[~]` T1.1 & T1.3 done; T1.2.2 & T1.4.1 updated to `250m`/`128Mi` Guaranteed QoS (re-verify on cluster); exit gate awaits T2.1 |
| 2–8 | `[ ]` not started (T2.3.1 preliminary harness/data recorded; re-run at `250m`/`128Mi` on cluster) |

Live: k3d cluster `cloudpulse`, target-app 3/3 Ready, Prometheus scraping at 5 s.
Whole cluster footprint 785 Mi. Measured constants so far are in
`deploy/limits.md`; canonical queries in `deploy/queries.md`.

**Next action: Phase 2, T2.1 — k6 open-loop load harness.** It unblocks both
the Phase 1 exit gate and the capacity constant `C`.

Anchor W1–W16 to real calendar dates here once the submission deadline is known.
