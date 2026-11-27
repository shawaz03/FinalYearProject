# CloudPulse — Work Process Log

**Team A8-8 · Mohan Babu University**

A chronological record of how the work was actually done: what was attempted,
what broke, how it was verified, and what each step produced.

> **Which document to read**
>
> | File | Answers |
> |---|---|
> | `CLAUDE.md` | **What we are building and in what order.** The authoritative plan |
> | `WORKPROCESS.md` (this) | **How the work went**, step by step, including the dead ends |
> | `docs/PROJECT_JOURNAL.md` | **Why each decision was made**, organised by decision |
>
> Where any older document in `docs/planning/` or `docs/viva/` disagrees with
> `CLAUDE.md`, the plan wins. Those were written before the review.

**Standing rule for this log:** a step is only recorded as done when something
was *checked*. Where a check could not be run, that is written down as not
done rather than quietly skipped.

---

## Session 1 — 26 November 2026

Started with 14 documents and no code. Ended with a running Kubernetes
cluster, live monitoring, a measurement harness, measured constants, and a
dashboard.

| | Start | End |
|---|---|---|
| Code | none | app, manifests, harness, dashboard |
| Version control | not a git repo | 25 commits, pushed |
| Cluster | none | k3d, 785 Mi |
| Tracked files | 14 | 95 |
| Tasks | 0 | 14 done · 1 partial · 54 open |

---

### Stage 1 — Review and verification

**Objective.** Check the project's foundations before building anything on them.

**Base paper.** Resolved the DOI and queried Crossref rather than trusting the
README.

```
doi.org/10.1109/TKDE.2026.3674583  →  302 → IEEE Xplore document 11435627
Crossref: TKDE, vol 38, issue 5, pp 3277–3290, May 2026, 7 authors
```

All fields match the README exactly. **The citation is sound.** Worth knowing:
ordinary web search does *not* find this paper yet — the indexes have not
caught up. If a mentor searches and finds nothing, show the DOI resolving.

**Review.** Read the README, technical blueprint, evaluation report and
pin-to-pin guide. Recorded 20 findings (D1–D20) in `CLAUDE.md` §4.

**The finding that mattered most.** The replica formula was executed rather
than read:

```python
TARGET_CPU_PER_POD = 60.0 ; MIN_PODS = 2
predictive_replicas = math.ceil(predicted_upper_cpu / TARGET_CPU_PER_POD)

U =  86%  →  2 pods     (the guide's own worked example)
U = 100%  →  2 pods     (the ceiling, at any CPU value)
```

`ceil(100/60) = 2`, and the floor was already 2. **The predictive branch could
never raise the replica count above the minimum.** CloudPulse would have been
a reactive autoscaler with an ML model attached that could not affect anything
— and the demo would still have looked fine.

Root cause: dividing a percentage by a percentage gives a ratio, not a count.
The deeper problem is that utilisation is *endogenous* — it is a function of
the replica count the controller itself sets.

**Outcome.** Rewrote the architecture around forecasting replica-independent
demand (RPS) divided by a measured capacity constant `C`, and inserted a new
blocking Phase 2 to measure `C`.

---

### Stage 2 — Repository restructure · `2d1390b` `5722001` `24745b4`

**Objective.** Make a repo that can hold an experiment honestly.

1. **Safety first.** `git init` and committed the original state untouched, so
   every later move was revertible.
2. **Checked before deleting.** Checksummed every file: no byte-identical
   duplicates. `review 0.pptx` and `Review_0_A8-8.pptx` genuinely differ, so
   both were kept rather than guessed at. **Nothing was deleted.**
3. Flattened the duplicated `FinalYearProject-main/FinalYearProject-main/`
   nesting; moved all 14 documents into `docs/` with `git mv` so history
   survived; renamed to kebab-case with a mapping table in `docs/README.md`.
4. Built the code skeleton, each directory carrying a README naming its
   purpose, owner and phase.
5. Rewrote `.gitignore`; added `.gitattributes` so four people on different
   machines do not generate phantom line-ending diffs.

**Snag.** The empty nested folder refused to delete — it was a live working
directory. Removed via PowerShell once the shell had moved.

---

### Stage 3 — Target application · `41bf3f6` `7e00488`

**Objective.** A workload to scale, with a controllable startup cost.

Built `apps/target-app`: FastAPI with `/api/work` (deterministic CPU burn),
`/healthz`, `/readyz`, `/metrics`. Multi-stage Dockerfile, non-root uid 10001,
260 MB, single uvicorn worker.

**Verified in a running container, not assumed:**

| Check | Result |
|---|---|
| Warmup gate | t=3 s → `healthz 200`, `readyz 503`; t=10 s → both 200 |
| Probe exclusion | 6 calls to `/api/work` → `http_requests_total 6.0`; 4 probe calls → **no series at all** |

That second check matters more than it looks. Kubelet probes `/readyz` every
two seconds forever. Had those counted, the RPS signal would carry a constant
inflation *that changes with replica count* — and the forecaster would have
trained on polluted data with nothing ever failing visibly.

Three choices to be able to defend: `--workers 1` (one pod = one unit of
capacity, so `C` is unambiguous); `/api/work` is a sync `def` (runs in a
threadpool, so a saturated CPU cannot stall health probes); `/healthz` ignores
warmup (a liveness probe that fires during warmup crash-loops the pod forever).

---

### Stage 4 — Runtime decision: Minikube → k3d · `148f41c`

**Trigger.** Reported laptop lag. Measured before prescribing: **4.1 GB free
of 15.6 GB, with no cluster running at all.** The lag was desktop apps, not
Kubernetes — but an 8 GB Minikube would have made it far worse.

Also found no `.wslconfig`, so WSL2 was on defaults: takes up to half the RAM
and **never returns it**. That is the usual cause of "my laptop got slow after
Docker and stayed slow".

**Decisions.**

| Was | Now | Why |
|---|---|---|
| Minikube | **k3d / K3s** | 474 Mi control plane vs ~2 GB · 61 s start · metrics-server built in · CNCF-certified so the "real Kubernetes" claim holds |
| kube-prometheus-stack | **plain Prometheus** | Grafana + Alertmanager cost ~500 Mi and the Phase 6 dashboard replaces Grafana. No Helm, no operator, no CRDs |
| everything in-cluster | **app + Prometheus only** | ~5.4 GB → ~2 GB, and removes the rebuild-redeploy cycle while developing the arbiter |

Wrote `~/.wslconfig` capping WSL2 at 6 GB with `autoMemoryReclaim=gradual`.

---

### Stage 5 — Cluster · `32bab05`

Created k3d cluster, imported the image, deployed, verified.

**Problem: every `kubectl` command hung 30 s then failed.**

```
dial tcp 10.165.67.128:60452: connection attempt failed
```

k3d had written `server: https://host.docker.internal:60452`, which on this
machine resolves to a VPN/virtual adapter rather than the Docker host. The
cluster was healthy the whole time. Diagnosed by checking that the port *was*
listening on loopback, then repointed the context at `127.0.0.1`. Written up
in `deploy/troubleshooting.md` — teammates will hit it, and the port changes
every time the cluster is recreated.

**Verified:** node Ready · 3 system pods only · Traefik absent · `kubectl top`
working · Deployment 3/3 · Service spread 9 requests across all three pods ·
scale up and down clean.

**T1.4.1 — MAX_PODS, resolving finding D13.** Scaled to 15: **15/15 Running,
zero Pending.** This matters because a Pending pod is not a scaled pod — if
the arbiter asks for replicas the node cannot place, scaling silently becomes
a no-op and the benchmark measures nothing. Node allocatable: 8 CPU, 5.79 GiB,
110 pods.

**First sight of the premise problem.** Scaling 3 → 6 reached all-Ready in
**2.6 seconds**. The thesis assumes a 30–90 second cold start.

---

### Stage 6 — Observability, and a fabricated metric · `4db257f`

Deployed plain Prometheus: 5 s scrape, 2 h retention, pod discovery by
annotation plus kubelet cAdvisor through the API-server proxy. **27 Mi against
a 400 Mi budget** — filtering cAdvisor to two series in one namespace is what
makes that possible; unfiltered it exceeds 1 GiB. 4/4 targets up.

**Problem: the latency numbers were fiction.**

The first PromQL run returned `p50 = 50.0 ms, p99 = 99.0 ms`. Too round.
Dumped the histogram:

```
le="0.1" → 201    le="0.5" → 201    le="1.0" → 201    le="+Inf" → 201
```

Four buckets, every request in the first one. `histogram_quantile` had no
information inside 0–100 ms and **interpolated linearly** — it would report
50 ms and 99 ms whether the truth was 5 ms or 95 ms.

| | Reported | True |
|---|---|---|
| p50 | 50.0 ms | **8.1 ms** |
| p99 | 99.0 ms | **34.6 ms** |

The p99 was **2.9× too high**. And 99 ms against a 100 ms SLO reads as a
system on the edge — exactly the plausible-looking number that gets
screenshotted into a slide and never questioned.

**Fix.** `LATENCY_BUCKETS` in the app: dense from 5 ms to 300 ms with an
**exact boundary at 0.1**, so the Phase 7 SLO is *counted*, not estimated.
Rolled to image `0.1.1` and updated the manifest to match, so
`kubectl apply` cannot silently revert it.

Recorded the canonical query set in `deploy/queries.md`. The collector,
arbiter and dashboard must all use those exact expressions — divergent copies
are how train/serve skew gets in.

---

### Stage 7 — T2.3.1 readiness measurement · `e702f84` `2fd7d5e`

**Objective.** Characterise pod startup so the forecast horizon `H` is derived
rather than asserted.

**Method choice.** Measured scale-patch → Ready client-side at 250 ms
resolution, *not* `creationTimestamp` → Ready `lastTransitionTime`: Kubernetes
serialises those at 1-second resolution, which on a ~3 second measurement is
~30% quantisation error. Patch-to-Ready is also the operationally honest
number — it includes the API server, scheduler and kubelet latency the arbiter
genuinely has to wait out.

**A methodology error, caught by smoke-testing first.** The trial run returned
`3.47, 3.47, 3.47` for a burst of three pods. Identical — because pods started
together become Ready together. Those are **one observation counted three
times**, not three samples. Left alone, the sweep would have reported n=21 per
setting while holding 7 independent observations, overstating confidence
threefold.

Reworked to record **cycle maxima** — time until *all* requested pods are
Ready. That is also the operationally correct statistic: the arbiter is not
done waiting until the last pod it asked for can serve.

**Results — 48 scale-ups:**

| WARMUP | cycles | p50 | max | **overhead** |
|---|---|---|---|---|
| 0 s | 12 | 4.42 s | 5.22 s | 5.22 s |
| 5 s | 12 | 8.64 s | 9.50 s | 4.50 s |
| 15 s | 10 | 18.72 s | 19.56 s | 4.56 s |
| 30 s | 8 | 34.05 s | 34.70 s | 4.70 s |
| 60 s | 6 | 65.19 s | 65.67 s | 5.67 s |

**Platform overhead is a near-constant 4.5–5.7 s** across a 60-second range of
warmup values — scheduling, container start and probe detection. Readiness is
therefore `WARMUP_SECONDS + ~5 s`, a clean linear relationship, which gives
the Phase 7 startup-cost sweep a well-behaved axis.

**Known omission, recorded rather than hidden.** Endpoint propagation
(kube-proxy updating iptables after Ready) is not captured. Sub-second locally
but non-zero — one reason `H` carries a safety margin instead of equalling the
p95.

---

### Stage 8 — Actuator RBAC and HPA baselines · `afb32db`

Written and validated with `--dry-run=server` against the live API; nothing
created.

- `deploy/k8s/30-actuator-rbac.yaml` — namespaced **Role**, not ClusterRole.
  Read Deployments and pods, write only `deployments/scale`. No pod creation,
  no Secrets, no other namespace.
- `deploy/baselines/hpa-default.yaml` — stock HPA, CPU 70%.
- `deploy/baselines/hpa-tuned.yaml` — CPU 50%, zero scale-up stabilisation,
  +100%/+4 pods per 15 s, scale-down window matched to ours.

Baselines live **outside** `deploy/k8s/` deliberately, so a routine
`kubectl apply -f deploy/k8s/` cannot start a second controller that fights
the arbiter.

**`hpa-tuned` exists because beating a stock HPA proves little.** A reviewer
will ask whether a well-configured HPA closes the gap; we answer it ourselves.
If CloudPulse cannot beat that, that is the finding.

---

### Stage 9 — Version control with the team · `e483e2e`

**Problem.** The local repo began with a fresh `git init`, so it had **no
common ancestor** with the team's GitHub repo, which held 8 commits.

```
git push          →  rejected
git push --force  →  deletes all 8 of the team's commits
```

A force push is the "obvious" fix when a push is rejected, and it would have
silently destroyed the teammates' entire history on someone else's repository.

**Resolution.** Verified by **content hash** that all 16 remote files already
existed in our tree — 13 byte-identical under their new names in `docs/`, and
3 changed deliberately (`.gitignore`, `README.md`, the pin-to-pin guide). Then
merged their history in with strategy `ours`.

Verified after: tree identical to pre-merge, `origin/main` now an ancestor so
the push is an ordinary fast-forward, backup branch kept.

**Access snag.** Push returned `403 — denied to brundareddycse`: Git
Credential Manager was reusing a cached credential for the wrong account.
Pinned the remote to `https://shawaz03@github.com/...` so lookups are
per-username and the other login stays intact. Authentication itself had to be
done in a human terminal — this session has no TTY for a credential prompt.

---

### Stage 10 — Dashboard · `e75630f` `040c10b` `31077ae`

**Sequencing note.** The dashboard is Phase 6 and depends on Phase 5, which
does not exist. It therefore runs on a **seeded simulation**, labelled as such
in the header at all times.

Worth doing anyway: writing the UI forced `TelemetryFrame`, `ForecastFrame`
and `Decision` to be defined concretely. **Those are now the WebSocket
contract T6.1 must implement** — an open question became a specification.

Stack: Next.js 16, React 19, Tailwind 4, Recharts 3, GSAP. shadcn's
*convention* (owned components on Radix + CVA), not the CLI.

**Aceternity UI and React Bits were deliberately not used.** Their signature
look — spotlight cards, beams, meteors, gradient borders — is precisely the
generic AI-startup aesthetic to be avoided here, and it would fight the data
for attention.

**Chart rules followed:** colour reserved for data, all chrome greyscale;
three scales means three stacked plots on one shared time axis with a synced
crosshair, **never a dual axis**; legend on every multi-series plot; a table
view exposing every value with no colour encoding.

**Two redesign rounds.** The first pass read generic — cause was the chrome,
not the chart rules: flat cards on `#191919`, the most common dashboard
surface there is. Rebuilt with tinted surfaces, a surveyor's dot grid, film
grain, corner registration ticks, section indices, and hard typographic scale
contrast. Second round moved the ground to a warm sand gradient and removed
the periwinkle series colour, which read as purple.

**Palette computed, never eyeballed.** Candidates were run through a validator
and failures discarded:

| candidate | result |
|---|---|
| cyan / amber / mint | FAIL — outside the lightness band |
| violet / amber / lime | FAIL — lime vs amber CVD ΔE **3.4** |
| blue / rust / dark teal | FAIL — teal chroma 0.088, reads grey |
| **blue / rust / teal-green** | **PASS** both modes |

The lime/amber pair is the lesson: obviously distinct to normal vision,
near-identical to roughly 6% of men. No amount of staring finds that.

Final: sand `#fbf8f1` → `#2060c8` `#cc5a1a` `#009384`; espresso `#1c1815` →
`#5a9bea` `#eb7b33` `#13ad97`. Both clear every check at ≥ 3:1.

**Not done: nobody has looked at the render.** The build compiles and the page
returns HTTP 200 with no runtime errors, but visual inspection for label
collisions, gradient banding and overflow is outstanding.

---

## Measured constants

All measured on the development laptop, 26 Nov 2026. **Re-measure on any
machine that runs benchmarks.**

| Quantity | Value | Source |
|---|---|---|
| Node allocatable | 8 CPU · 5.79 GiB · 110 pods | `deploy/limits.md` |
| **MAX_PODS** | **15** — zero Pending | T1.4.1 |
| Control plane | 474 Mi | T1.2.1 |
| Prometheus | 27 Mi | T1.3.1 |
| Whole cluster (3 pods) | 785 Mi | T1.3 |
| Per pod, idle | ~37 Mi · 3–4 m CPU | T1.4.1 |
| Platform startup overhead | **4.5 – 5.7 s** | T2.3.1 |
| Readiness | `WARMUP_SECONDS + ~5 s` | T2.3.1 |
| p50 / p90 / p99 latency | 8.1 / 14.1 / 34.6 ms | T1.3.3 |
| CPU per request | ~13 ms at 15 000 iterations | T1.1 |

**Still to measure:** capacity constant `C` (T2.2) and horizon `H` (T2.3.2).

---

## Problems encountered, and how each was resolved

| # | Problem | Resolution |
|---|---|---|
| 1 | Replica formula could never exceed `MIN_PODS` | Forecast demand ÷ measured capacity; new blocking Phase 2 |
| 2 | Latency quantiles fabricated by 4-bucket histogram | `LATENCY_BUCKETS` with an exact 0.1 boundary |
| 3 | Pods Ready in 2.6 s, not 30–90 s | `WARMUP_SECONDS` as a declared, swept variable |
| 4 | `kubectl` hung — `host.docker.internal` → VPN adapter | Repointed context at `127.0.0.1`; documented |
| 5 | Batched readiness samples were correlated | Record cycle maxima as the independent statistic |
| 6 | Unrelated git histories; force push would delete 8 commits | Content-hash verification, then merge with strategy `ours` |
| 7 | `403` — wrong cached GitHub account | Pinned remote to the `shawaz03@` form |
| 8 | Laptop RAM pressure | `.wslconfig` cap + `autoMemoryReclaim`; k3d; trimmed stack |
| 9 | Chart palette failed CVD checks | Validator-driven search instead of eyeballing |
| 10 | `create-next-app` refused a non-empty directory | Stashed placeholder docs, scaffolded, restored |

---

## Current state

**14 tasks done · 1 partial · 54 open.** 95 tracked files, 25 commits, 2.0 MB
repository.

| Phase | Status |
|---|---|
| 0 — Repo & reproducibility | `[~]` T0.1 done; Makefile, seeds, manifest schema open |
| 1 — Cluster & observability | `[~]` all tasks done; exit gate awaits the k6 sustained run |
| 2 — Capacity & load harness | `[~]` T2.3.1 done; `C` and `H` outstanding |
| 3–5 | `[ ]` not started |
| 6 — Dashboard | `[~]` shell built on simulated data |
| 7 — Evaluation | `[~]` HPA baselines written |
| 8 — Thesis & viva | `[ ]` not started |

**Live:** k3d cluster `cloudpulse`, target-app 3/3 Ready, Prometheus scraping
at 5 s.

### Open decisions

1. **HPA measures utilisation against `requests`, not `limits`.** Updated
   `deploy/k8s/10-target-app-deployment.yaml` on 2026-11-27 to `requests == limits`
   (`250m` CPU / `128Mi` memory, Guaranteed QoS) — **re-apply & re-verify on the
   cluster laptop** before Phase 2 finalises.
2. **Phase 1 exit gate** deliberately open — needs the k6 sustained run (T2.1).
3. **Dashboard not visually inspected.**

---

## Session 2 — 27 November 2026 (7.9 GB DEV/ML/UI Laptop)

### Step P.1 — Align `target-app` CPU & memory `requests == limits` (Guaranteed QoS)

**Why.** `deploy/k8s/10-target-app-deployment.yaml` previously set
`requests.cpu: 50m` / `limits.cpu: 500m` and `requests.memory: 64Mi` /
`limits.memory: 128Mi`. Because Kubernetes HPA computes utilisation against
`requests` (`usage / requests`), HPA would have tripped at ~35m CPU and handed
CloudPulse an invalid artifact win. Setting equal `requests` and `limits` on
**both** CPU and memory also puts every pod into Kubernetes `Guaranteed` QoS.

**Choice (`250m` CPU / `128Mi` memory).** At `500m`, 15 pods would request
`7.5` of the `8` allocatable cores on the k3d node and risk `Insufficient cpu`
(`Pending`) alongside system and Prometheus pods. At `250m` / `128Mi`, 15 pods
request `3750m` CPU (`47%`) and `1920Mi` memory (`32%`), leaving clean headroom
while keeping per-pod capacity in a convenient RPS range for local k6 load.

**Not yet checked (requires the 15.6 GB cluster laptop):**
- Applying `deploy/k8s/10-target-app-deployment.yaml` to `k3d-cloudpulse`
- Re-confirming `MAX_PODS = 15` with `0 Pending` (`T1.4.1`)
- Re-running `T2.3.1` readiness (`experiments/measure_readiness.py`) and
  measuring `C` (`T2.2`) against the `250m` CPU limit.

### Next actions

| Member | Next | Blocked by |
|---|---|---|
| **M3** | **T2.1 — k6 open-loop harness.** Critical path | nothing |
| **M2** | T2.3.2 derive `H`; then the actuator (T5.3.2) | nothing |
| **M4** | Take `experiments/` and `analysis/` now, not week 12 | nothing |
| **M1** | MAPIE + ACI reading; cannot train until `C`, `H` and data exist | Phases 2–3 |

**T2.1 is the critical path.** It unblocks the Phase 1 exit gate, the capacity
constant `C`, and therefore all of Phases 3, 4 and 5.
