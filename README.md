# CloudPulse: Smart Cloud Resource Optimization
## Load-Aware Predictive Auto-Scaling Framework in Cloud Systems
### Capstone Project | Team A8-8 | Mohan Babu University (MBU)

[![GitHub Repo](https://img.shields.io/badge/GitHub-FinalYearProject-blue?logo=github)](https://github.com/shawaz03/FinalYearProject)
[![MBU Capstone](https://img.shields.io/badge/MBU-Capstone%20Project%202026-green)](#)
[![Domain](https://img.shields.io/badge/Domain-Cloud%20Computing%20%7C%20Kubernetes%20%7C%20Machine%20Learning-orange)](#)
[![IEEE Reference](https://img.shields.io/badge/IEEE%20TKDE-May%202026-red)](https://ieeexplore.ieee.org/abstract/document/11435627)

---

## 📑 Table of Contents
1. [Executive Summary](#-executive-summary)
2. [IEEE Base Paper Reference & Verification](#-ieee-base-paper-reference--verification)
3. [The Core Problem: Reactive HPA vs. Flash Traffic](#-the-core-problem-reactive-hpa-vs-flash-traffic)
4. [Research Gap & Our Extension (CloudPulse)](#-research-gap--our-extension-cloudpulse)
5. [Core Mathematical & Algorithmic Foundations](#-core-mathematical--algorithmic-foundations)
6. [End-to-End System Architecture](#-end-to-end-system-architecture)
7. [Team Domain Breakdown (4-Member Workload Distribution)](#-team-domain-breakdown-4-member-workload-distribution)
8. [Phase-by-Phase Execution Roadmap (Phases 1–6)](#-phase-by-phase-execution-roadmap-phases-16)
9. [Experimental Benchmarking & Target Metrics](#-experimental-benchmarking--target-metrics)
10. [Mentor Defense & Viva Quick Reference](#-mentor-defense--viva-quick-reference)
11. [Repository Structure & File Index](#-repository-structure--file-index)

---

## 📌 Executive Summary

Modern cloud applications (microservices, serverless, DBaaS) experience highly volatile, bursty workloads. Default cloud autoscaling mechanisms—such as the **Kubernetes Horizontal Pod Autoscaler (HPA)**—operate **reactively**: they observe sustained high CPU or memory utilization and *then* trigger replica scale-up.

However, provisioning new application containers requires **30 to 90 seconds** (image pull, container initialization, JVM/runtime warmup, readiness probes). During this latency gap, existing pods become saturated, triggering severe tail-latency spikes, request timeouts, and SLA violations.

**CloudPulse** solves this challenge by implementing an **uncertainty-aware predictive auto-scaler**:
- **Proactive Scaling:** Forecasts incoming workload 60 seconds into the future using machine learning and pre-warms container pods *before* traffic reaches the cluster.
- **Uncertainty Quantification:** Replaces brittle single-number point forecasts with rigorous statistical confidence intervals using **Conformal Prediction**, preventing over-fitting to noisy metrics.
- **Hybrid Fail-Safe:** Combines predictive scaling with a reactive safety threshold (instant fallback if CPU > 85%), guaranteeing stability against unpredictable anomalies.

---

## 📚 IEEE Base Paper Reference & Verification

* **Paper Title:** *"Uncertainty-Aware Online Time Series Multi-Step Forecasting Framework in Cloud Systems"*
* **Authors:** Jiadong Chen, Yang Luo, Xiuqi Huang, Fuxin Jiang, Yangguang Shi, Tieying Zhang, Xiaofeng Gao
* **Affiliations:** Shanghai Jiao Tong University & ByteDance Inc. (TikTok/Douyin infrastructure)
* **Journal:** **IEEE Transactions on Knowledge and Data Engineering (TKDE)**
* **Publication Date:** May 2026 (Early Access: March 2026) | **Volume:** 38, **Issue:** 5, **Pages:** 3277–3290
* **DOI:** [`10.1109/TKDE.2026.3674583`](https://doi.org/10.1109/TKDE.2026.3674583)
* **IEEE Xplore URL:** [https://ieeexplore.ieee.org/abstract/document/11435627](https://ieeexplore.ieee.org/abstract/document/11435627)
* **Indexing & Tier:** **SCI / SCIE Indexed**, Scopus, Clarivate Analytics Master Journal List, **CORE Rank A\***, Impact Factor: **~8.9** (Q1 Premier Computer Science Journal).

---

## ⚡ The Core Problem: Reactive HPA vs. Flash Traffic

### The Real-World Breakdown (e.g., E-Commerce Flash Sale / Ticket Drop)
1. **12:00:00 AM:** Traffic spikes instantly from 1,000 to 50,000 requests per second.
2. **12:00:15 AM:** Prometheus scrapes metrics; HPA detects average CPU > 70%.
3. **12:00:30 AM:** HPA issues command to scale deployment from 3 to 15 pods.
4. **12:00:30 – 12:01:30 AM:** Pods take 60 seconds to download images, schedule nodes, and pass health checks.
5. **The Damage:** For **90 seconds**, the 3 initial pods run at 100% CPU. Requests fail with HTTP 504 Gateway Timeouts. Amazon reports that every 100ms of added latency costs 1% of sales.

```
Reactive (Standard K8s HPA):
Traffic Spike  ─────────────► [CPU > 70%] ──(Wait 15s)──► [Scale Pods] ──(Wait 60s Boot)──► Pods Ready
                                  ▲
                                  └──────── 75-90s Total Latency Gap (Downtime/SLA Breaches) ────────┘

Predictive (CloudPulse):
Workload Trend ──► [ML Forecast at T-60s] ──► [Pre-scale Pods] ──► Pods Warm & Ready ──► Traffic Arrives
                                                                        ▲
                                                                        └─ Zero SLA Violation Window
```

---

## 🔬 Research Gap & Our Extension (CloudPulse)

| Capability / Feature | IEEE Paper (IPOC) | Our Implementation (CloudPulse) |
| :--- | :---: | :---: |
| Workload time-series forecasting | ✅ Yes | ✅ Yes |
| Conformal prediction intervals | ✅ Yes (ACCI) | ✅ Yes (MAPIE quantile regressor) |
| Concept drift online tracking | ✅ Yes (Chasing Oracle) | ✅ Yes (Incremental sliding-window) |
| **Real Kubernetes Cluster Deployment** | ❌ No (Offline trace evaluation) | ✅ **Yes — Minikube / K3s Cluster** |
| **Live Closed-Loop Scaling Actuation** | ❌ No (Passive analysis) | ✅ **Yes — Custom Python K8s Controller** |
| **Hybrid Reactive Fallback Guard** | ❌ No | ✅ **Yes — Instant spin-up if CPU > 85%** |
| **Cost vs. SLA Multi-Objective Optimization** | ❌ No | ✅ **Yes — Dynamic replica clamping** |
| **Interactive Real-Time Web Dashboard** | ❌ No | ✅ **Yes — Next.js / React UI Control Plane** |
| **Live Synthetic Load Benchmarking** | ❌ No (Static CSV datasets) | ✅ **Yes — Locust dynamic load profiles** |

### Defense Summary of Our Research Contribution:
> *"The IEEE paper develops the theoretical IPOC/ACCI forecasting formulation but only tests it offline against static trace files. Our capstone operationalizes this theory into a deployable, closed-loop engineering reality: an end-to-end Kubernetes control plane with real-time Prometheus telemetry scraping, asymmetric loss optimization, hybrid reactive fail-safes, and a live monitoring dashboard."*

---

## 🧮 Core Mathematical & Algorithmic Foundations

### 1. Base Multi-Step Forecaster
Predicts future workload metrics $y_{t+1}, \dots, y_{t+H}$ given historical telemetry vector $\mathbf{x}_t = [CPU_t, RPS_t, RAM_t, Latency_t, Lags]$.

### 2. Adaptive Conformal Prediction (via MAPIE)
Traditional Gaussian confidence bounds assume error normality, which fails under bursty cloud traffic. Conformal prediction computes distribution-free, non-parametric prediction intervals:
$$P\left(y_{t+h} \in [L_{t+h}, U_{t+h}]\right) \ge 1 - \alpha$$
Where $\alpha = 0.10$ provides a mathematically guaranteed **90% coverage rate**. Scaling decisions scale based on the **upper bound $U_{t+h}$** to prioritize reliability over under-provisioning.

### 3. Asymmetric Loss Function
In cloud scaling, under-predicting leads to customer-facing outages, while over-predicting merely leads to brief idle pod spend. We formulate an asymmetric penalty:
$$L(\hat{y}, y) = \begin{cases} 3 \cdot |y - \hat{y}| & \text{if } \hat{y} < y \quad \text{(Under-prediction / High Penalty)} \\ 1 \cdot |y - \hat{y}| & \text{if } \hat{y} \ge y \quad \text{(Over-provisioning / Low Penalty)} \end{cases}$$

### 4. Hybrid Decision Arbiter Logic
Every 30 seconds, the Arbiter executes:
```python
# 1. Predictive scale target from upper conformal bound
predictive_replicas = math.ceil(predicted_upper_cpu / target_cpu_per_pod)

# 2. Emergency reactive safety check
if current_live_cpu > 85.0:
    reactive_replicas = current_replicas + 2
else:
    reactive_replicas = current_replicas

# 3. Hybrid arbitration with safety clamping
desired_replicas = max(predictive_replicas, reactive_replicas)
final_replicas = min(max(desired_replicas, MIN_PODS), MAX_PODS)

# 4. Actuation
if final_replicas != current_replicas:
    k8s_client.patch_namespaced_deployment_scale(name="target-app", replicas=final_replicas)
```

---

## 🏛️ End-to-End System Architecture

```
                                [ LOCUST LOAD GENERATOR ]
                      (Simulates Organic Ramps, Spikes & Sine Waves)
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              KUBERNETES CLUSTER (MINIKUBE)                             │
│                                                                                        │
│   ┌────────────────────────────────┐                 ┌─────────────────────────────┐   │
│   │     Target Application Pods    │ ◄─ Scale Up/Dn ─┤  CloudPulse K8s Controller  │   │
│   │ (FastAPI Mock Ride-Booking App)│                 │ (Python Client / Scale API) │   │
│   └───────────────┬────────────────┘                 └──────────────▲──────────────┘   │
│                   │                                                 │                  │
│                   │ Exposes /metrics                                │ Desired Replicas │
│                   ▼                                                 │                  │
│   ┌────────────────────────────────┐                 ┌──────────────┴──────────────┐   │
│   │       Prometheus Server        │                 │    Hybrid Decision Arbiter  │   │
│   │ (Scrapes metrics every 5 secs) │                 │  (Upper Bound + Fallback)   │   │
│   └───────────────┬────────────────┘                 └──────────────▲──────────────┘   │
│                   │                                                 │                  │
│                   │ PromQL Query                                    │ [Lower, Upper]   │
│                   ▼                                                 │ Intervals        │
│   ┌────────────────────────────────┐                 ┌──────────────┴──────────────┐   │
│   │   Telemetry Collector Engine   │ ──────────────► │      ML Inference Engine    │   │
│   │  (TimescaleDB / PostgreSQL)    │  Recent Windows │   (XGBoost + MAPIE Conformal│   │
│   └────────────────────────────────┘                 └─────────────────────────────┘   │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ WebSocket Event Stream
                                            ▼
                        ┌───────────────────────────────────────┐
                        │       NEXT.JS / REACT DASHBOARD       │
                        │ • Real-time Prediction Cone Chart     │
                        │ • Live Pod Count & Health Status      │
                        │ • P50/P90/P99 Response Latency Gauge  │
                        │ • Cost Savings vs. Static Allocator   │
                        └───────────────────────────────────────┘
```

---

## 👥 Team Domain Breakdown (4-Member Workload Distribution)

| Member | Primary Domain | Core Responsibilities & Deliverables |
| :--- | :--- | :--- |
| **Member 1** | **Machine Learning & Conformal Prediction** | • Alibaba/Google trace preprocessing & feature engineering.<br>• Base XGBoost/GBR regressor training with lag & rolling metrics.<br>• MAPIE Conformal Inference integration for 90% confidence intervals.<br>• Asymmetric loss function implementation. |
| **Member 2** | **Cloud Orchestration & Actuation** | • Minikube cluster setup, Docker containerization, and K8s manifests.<br>• Prometheus Helm deployment and ServiceMonitor metrics scraping.<br>• Custom Python Kubernetes scaling actuator (`AppsV1Api`).<br>• RBAC role definitions and cluster stability management. |
| **Member 3** | **Backend Engineering & Load Testing** | • FastAPI Telemetry Ingestion and Decision Arbiter service.<br>• Locust synthetic workload test suite (Gradual, Flash Spike, Diurnal).<br>• PostgreSQL/TimescaleDB time-series storage pipeline.<br>• WebSocket server for pushing live updates to the frontend. |
| **Member 4** | **Frontend UI & Scientific Evaluation** | • Next.js 14 control plane with Tailwind CSS & Recharts.<br>• Prediction cone graph (actual vs. forecast vs. confidence intervals).<br>• Benchmark data analysis, ANOVA/t-tests, and SLA violation calculation.<br>• Project documentation, IEEE paper draft, and slide presentations. |

---

## 🗓️ Phase-by-Phase Execution Roadmap (Phases 1–6)

```
Phase 1: Foundation (Weeks 1–3) ────► Working Minikube, Prometheus, Locust & Target App
Phase 2: ML Engine (Weeks 4–6)   ────► Feature pipeline, XGBoost model & MAPIE [lower, upper] API
Phase 3: Decision Engine (Weeks 7–8) ─► Hybrid Arbiter + Python K8s automatic scaler
Phase 4: Dashboard (Weeks 9–10)  ────► Next.js live telemetry control plane & gauges
Phase 5: Benchmarking (Weeks 11–13) ─► Comparative runs: CloudPulse vs HPA vs Static
Phase 6: Final Review (Weeks 14–16) ─► Capstone documentation, IEEE paper & viva defense
```

- **Phase 1 (Foundation):** Set up Minikube, deploy target microservice, configure Prometheus scraping every 5s, verify Locust traffic bursts.
- **Phase 2 (ML Engine):** Build training data from traces, train point predictor, wrap with MAPIE quantile conformal regressor, containerize as `/predict` service.
- **Phase 3 (Decision Engine):** Build Decision Arbiter with upper-bound provisioning, 85% CPU reactive safety fallback, and call Kubernetes `patch_namespaced_deployment_scale`.
- **Phase 4 (Dashboard):** Implement Next.js web application visualizing live cluster metrics, pod replica transitions, latency heatmaps, and cost savings.
- **Phase 5 (Benchmarking):** Run repeatable tests under 3 traffic profiles comparing CloudPulse against default Kubernetes HPA and static over-provisioning.
- **Phase 6 (Defense & Documentation):** Compile full capstone thesis, presentation slide decks, and live demo test script.

---

## 📊 Experimental Benchmarking & Target Metrics

> ⚠️ **TARGET VALUES — NOT YET MEASURED.** No experiment has been run. The
> figures below are design targets stated in advance, not results. They will be
> replaced by values generated from `experiments/results/` in Phase 7, each
> traceable to a `run_id`. Do not cite them in any submission or review until
> then. See `CLAUDE.md` §2 rule 1 and §4 finding D1.

| Evaluation Metric | Static Allocation (10 Pods) | Default Kubernetes HPA | CloudPulse (Our System) |
| :--- | :---: | :---: | :---: |
| **P99 Latency during Flash Spike** | ~45 ms | 800 – 1,200 ms | **< 100 ms** |
| **SLA Violation Rate (Req > 100ms)** | ~0% | 15% – 25% | **< 2.0%** |
| **Average Pod Replicas** | 10.0 (Fixed) | ~4.5 | **~4.8** |
| **Relative Cloud Infrastructure Cost** | High (100%) | Low (~45%) | **Optimal (~48%)** |
| **Scaling Reaction Time** | N/A (Static) | 45–90 seconds delay | **Proactive (Pre-warmed)** |

**Key Academic Takeaway:** CloudPulse achieves the near-zero SLA violation performance of static over-provisioning while maintaining the cost efficiency of dynamic auto-scaling.

---

## 🎙️ Mentor Defense & Viva Quick Reference

### Q1: "Explain your project in 30 seconds."
> *"Our project solves the cold-start delay in cloud autoscaling. Standard Kubernetes HPA is reactive—it waits for CPU to spike, taking 60+ seconds to spin up pods while users suffer timeouts. We built CloudPulse, a predictive framework using Conformal Machine Learning to forecast traffic 60 seconds early and pre-scale pods, combined with a reactive safety guard for emergency surges."*

### Q2: "What is your novel contribution compared to the IEEE TKDE paper?"
> *"The IEEE paper is purely algorithmic—it proposes IPOC and tests it on offline CSV traces. It never built a real cloud system. Our novelty is **operationalizing** this theory: we built a closed-loop Kubernetes actuator, an asymmetric loss penalty (heavier penalty for under-provisioning), a hybrid reactive fail-safe, and a live monitoring control plane."*

### Q3: "What happens if your machine learning model makes a bad prediction?"
> *"We address this with two protective layers: First, we scale based on the **upper bound of the 90% conformal interval**, which accounts for statistical uncertainty. Second, we built a **hardware-level reactive safety guard**—if cluster CPU ever crosses 85%, the system immediately triggers emergency pod additions regardless of the ML forecast."*

### Q4: "Why did you use Conformal Prediction instead of standard ML?"
> *"Standard ML only gives a single point estimate (e.g., 'CPU will be 70%'). If that estimate is wrong by 15%, the system crashes. Conformal prediction provides mathematically guaranteed prediction intervals without assuming a normal distribution, giving us a safe upper bound to scale against."*

---

## 📁 Repository Structure & File Index

> Restructured 2026-11-26. All documents were moved into `docs/` and renamed to
> consistent kebab-case; nothing was deleted. `docs/README.md` maps every new
> name back to its original. **`CLAUDE.md` is the authoritative plan** — it
> supersedes the architecture described in the older planning documents.

### Code (skeleton created; implementation begins at Phase 1)

| Path | Purpose |
| :--- | :--- |
| **`CLAUDE.md`** | Authoritative architecture, phase plan and status. Start here. |
| **`cloudpulse_core/`** | Shared feature/seed/config package imported by both training and serving. |
| **`apps/target-app/`** | FastAPI workload under test; hosts the `WARMUP_SECONDS` startup-delay control. |
| **`services/collector/`** | Prometheus → PostgreSQL telemetry pipeline. |
| **`services/forecaster/`** | Conformalised quantile forecaster, `POST /predict`. |
| **`services/arbiter/`** | Capacity model, control layer and Kubernetes actuator. |
| **`ml/`** | Offline training, datasets and model artifacts. |
| **`deploy/`** | Kubernetes manifests, Helm values, and measured cluster constants. |
| **`load/`** | k6 open-loop traffic profiles. |
| **`experiments/`** | Benchmark runner and per-run results. |
| **`analysis/`** | Metrics, statistics and figures. |
| **`dashboard/`** | Next.js control plane. |
| **`tests/`** · **`scripts/`** | Test suite; operational helpers. |

### Documents

| Path | Purpose |
| :--- | :--- |
| **`docs/planning/technical-blueprint.md`** | Technical guide, IEEE mapping, domain concepts, pseudocode. |
| **`docs/planning/project-evaluation.{md,pdf,html}`** | Academic evaluation, journal verification, research gaps. |
| **`docs/reports/capstone-report-mbu-10page.docx`** | Official 10-page capstone report in MBU format. |
| **`docs/reports/project-abstract.docx`** | Initial project abstract submission. |
| **`docs/viva/mentor-defense-guide.{md,docx}`** | Viva scripts and defense answers. |
| **`docs/presentations/review-0-final-A8-8.pptx`** | Review 0 deck presented to the committee. |
| **`docs/presentations/review-0-draft.pptx`** | Earlier draft (differs from final; kept deliberately). |
| **`docs/presentations/review-0-speaker-notes.docx`** | Slide-by-slide script and speaker notes. |
| **`docs/presentations/mbu-review-template.pptx`** | Department slide template. |
| **`docs/assets/initial-architecture-sketch.jpeg`** | Conceptual architecture sketch. |

---

*Repository maintained by [shawaz03](https://github.com/shawaz03) | Team A8-8 | Mohan Babu University (MBU)*
