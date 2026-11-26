> ⚠️ **SUPERSEDED — DO NOT REHEARSE VIVA ANSWERS FROM THIS DOCUMENT.**
>
> Written before the architecture review of 2026-11-26. Sections 2, 4 and 5
> still describe the superseded design:
>
> - the replica formula `ceil(predicted_upper_cpu / TARGET_CPU_PER_POD)` is
>   dimensionally wrong. With `TARGET_CPU_PER_POD = 60` it can never return
>   more than 2 pods at any CPU value, and `MIN_PODS` is already 2 — so the
>   predictive branch can never scale above the floor. See
>   `docs/PROJECT_JOURNAL.md` §4.
> - the forecast target is CPU utilisation %, which is endogenous.
> - Locust is closed-loop and hides overload latency; we use k6.
> - Minikube has been replaced by k3d.
> - "hardware-level reactive safety guard" (Q3) is wrong wording — it is a
>   software control loop reading Prometheus.
>
> Kept as a record of the project's development. **`CLAUDE.md` is
> authoritative.** This file gets corrected in Phase 8 (T8.1).

# CloudPulse: Pin-to-Pin Complete Project Master Guide
## Load-Aware Predictive Auto-Scaling Framework in Cloud Systems
### Capstone Project | Team A8-8 | Mohan Babu University (MBU)

---

## 📑 Table of Contents
1. [The Core Problem (From First Principles)](#1-the-core-problem-from-first-principles)
2. [Complete Technology Stack & Why Each Tool Is Used](#2-complete-technology-stack--why-each-tool-is-used)
3. [Pin-to-Pin System Architecture](#3-pin-to-pin-system-architecture)
4. [Step-by-Step Data Flow (Lifecycle of a Traffic Spike)](#4-step-by-step-data-flow-lifecycle-of-a-traffic-spike)
5. [Decision Arbiter Mathematical Logic & Pseudocode](#5-decision-arbiter-mathematical-logic--pseudocode)
6. [Our 3 Core Technical Novelties (Viva Defense Goldmines)](#6-our-3-core-technical-novelties-viva-defense-goldmines)
7. [Team Workload & Module Division (4 Members)](#7-team-workload--module-division-4-members)
8. [Frequently Asked Viva Questions & Ready-to-Speak Answers](#8-frequently-asked-viva-questions--ready-to-speak-answers)

---

# 1. The Core Problem (From First Principles)

### What is Auto-Scaling?
When an application experiences an influx of incoming web requests, a single server instance becomes saturated. Auto-scaling dynamically provisions additional server instances (containers/pods) to distribute the incoming load horizontally.

### The Fatal Flaw of Existing Cloud Systems:
Default cloud orchestrators—like the standard **Kubernetes Horizontal Pod Autoscaler (HPA)**—operate **reactively**:
1. **Observation:** The HPA checks metrics periodically (every 15–30 seconds).
2. **Threshold Breach:** It acts only **after** average CPU or memory utilization crosses a danger threshold (e.g., > 70%).
3. **The 30–90 Second "Cold-Start" Delay:**
   A new container cannot serve traffic instantly. It must:
   - Schedule on a cluster node.
   - Download the container image (if not cached).
   - Initialize the application runtime (Python interpreter, JVM, Node runtime).
   - Establish database connection pools and pass Kubernetes readiness probes.
4. **The Cascading Outage:**
   During this 30–90 second startup window, existing pods run at 100% capacity. Queues overflow, incoming requests fail with **HTTP 504 Gateway Timeouts**, and the business suffers severe Service Level Agreement (SLA) violations.

### What We Are Building (CloudPulse):
**CloudPulse** replaces reactive panic with **proactive forecasting**:
- It evaluates real-time metric trends from Prometheus.
- It uses Machine Learning with **Conformal Prediction** to forecast resource demands **60 seconds into the future**.
- It triggers Kubernetes pod creation *before* the traffic spike hits the cluster.
- When user traffic arrives, the containers are already initialized, warmed up, and ready to serve requests.

---

# 2. Complete Technology Stack & Why Each Tool Is Used

| Layer | Technology | Exact Role in CloudPulse | Why We Selected This Tool |
| :--- | :--- | :--- | :--- |
| **Local Cluster** | **Minikube** | Hosts a real Kubernetes single-node or multi-node cluster locally. | Zero cloud billing, free, supports complete Kubernetes API and container networking. |
| **Containerization** | **Docker** | Builds container images for the target application and control plane. | Industry-standard isolation ensuring identical execution across environments. |
| **Target Application** | **FastAPI (Python)** | Simulates a production backend microservice (e.g., campus ride-booking API). | Asynchronous, low overhead, and natively exposes custom Prometheus metrics endpoints. |
| **Telemetry Scraping** | **Prometheus** | Scrapes CPU, RAM, request rate, and latency metrics every **5 seconds**. | High-performance time-series database with PromQL query capabilities. |
| **Load Testing** | **Locust (Python)** | Generates user traffic profiles (gradual morning rush, instant 10x spikes, diurnal waves). | Scriptable in pure Python; easily simulates thousands of concurrent users hitting endpoints. |
| **Base ML Forecaster** | **XGBoost / Gradient Boosting** | Predicts CPU and request volume based on rolling metrics and lag features. | Superior tabular time-series accuracy with sub-10ms inference latency. |
| **Uncertainty Layer** | **MAPIE (Conformal Prediction)** | Computes distribution-free $[Lower, Upper]$ 90% confidence intervals. | Eliminates brittle point predictions; provides statistically guaranteed error bounds. |
| **Kubernetes Actuator** | **Python `kubernetes` Client** | Controller script that communicates with the Kubernetes API Server. | Uses `patch_namespaced_deployment_scale` to update replica counts directly. |
| **Historical Storage** | **PostgreSQL / TimescaleDB** | Stores historical telemetry metrics, inference logs, and scaling action audits. | Robust SQL database optimized for time-series aggregation and benchmarking. |
| **Live Dashboard** | **Next.js 14 / React + Recharts** | Web interface displaying real-time graphs, pod gauges, and prediction cones. | Connected via WebSockets for zero-refresh, real-time telemetry streaming. |

---

# 3. Pin-to-Pin System Architecture

```
                          [ LOCUST LOAD GENERATOR ]
                   (Injects Synthetic Bursty HTTP Traffic)
                                      │
                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      KUBERNETES CLUSTER (MINIKUBE)                     │
│                                                                        │
│   ┌───────────────────────────┐          ┌─────────────────────────┐   │
│   │    Target App Replicas    │ ◄─────── │  CloudPulse Controller  │   │
│   │ (Docker / FastAPI Pods)   │  Scale   │  (Python Kubernetes API)│   │
│   └─────────────┬─────────────┘          └────────────▲────────────┘   │
│                 │ Exposes                             │ Desired        │
│                 │ /metrics                            │ Pod Count      │
│                 ▼                                     │                │
│   ┌───────────────────────────┐          ┌────────────┴────────────┐   │
│   │     Prometheus Server     │          │ Hybrid Decision Arbiter │   │
│   │ (Scrapes Every 5 Seconds) │          │  (Upper Bound + Guard)  │   │
│   └─────────────┬─────────────┘          └────────────▲────────────┘   │
│                 │ PromQL                              │ [Lower, Upper] │
│                 │ Telemetry                           │ Intervals      │
│                 ▼                                     │                │
│   ┌───────────────────────────┐          ┌────────────┴────────────┐   │
│   │ Telemetry Ingestion Layer │ ───────► │   ML Inference Engine   │   │
│   │ (Lag & Feature Extraction)│  Vectors │  (XGBoost + MAPIE CP)   │   │
│   └───────────────────────────┘          └─────────────────────────┘   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ WebSocket Broadcast
                                    ▼
                 ┌──────────────────────────────────────┐
                 │       NEXT.JS / REACT DASHBOARD      │
                 │ • Real-Time Shaded Prediction Cone   │
                 │ • Active Pod Gauge (Animated)        │
                 │ • P99 Response Latency (< 100ms)     │
                 │ • Cost Savings vs. Static Allocator  │
                 └──────────────────────────────────────┘
```

---

# 4. Step-by-Step Data Flow (Lifecycle of a Traffic Spike)

### Step 1: Traffic Injection (Locust)
* A Locust test script ramps virtual users from **20 users $\rightarrow$ 400 users** over a 30-second window.
* Requests hit the Kubernetes Service endpoint (`http://target-app.default.svc.cluster.local:8000/api/rides/book`).

### Step 2: Metric Exposure & Scraping (Prometheus)
* Every time a request finishes, the FastAPI app increments `http_requests_total` and logs the latency histogram `http_request_duration_seconds`.
* Prometheus scrapes the pods every **5 seconds** and records:
  - `rate(container_cpu_usage_seconds_total[1m])`
  - `rate(http_requests_total[1m])`
  - `container_memory_working_set_bytes`

### Step 3: Feature Engineering & Model Inference (ML Engine)
* Every 30 seconds, the Python Telemetry Collector pulls the past 5 minutes of metrics.
* It extracts dynamic features:
  - **Lags:** CPU at $T-1$, $T-2$, $T-5$ minutes.
  - **Rolling Window Stats:** 5-minute moving average and rolling standard deviation.
  - **First Derivative (Rate of Change):** $(CPU_t - CPU_{t-1}) / \Delta t$ to detect the start of a ramp.
* The **XGBoost + MAPIE** conformal model predicts workload 60 seconds into the future:
  $$\text{Predicted CPU Interval} = [L=64\%, \ U=86\%] \quad (\text{Coverage guarantee } 1-\alpha = 0.90)$$

### Step 4: Hybrid Decision Arbitration (The Controller Brain)
The controller evaluates the predictions against two independent criteria:
1. **Predictive Requirement:** Uses the **Upper Bound ($U=86\%$)** to calculate required replicas so the cluster has sufficient buffer.
2. **Emergency Reactive Guard:** Checks if the live CPU currently exceeds **85%**. If yes, it immediately adds $+2$ emergency pods regardless of model output.
3. **Safety Clamping:** Clamps the desired replica count between cluster constraints ($\text{Min}=2, \ \text{Max}=15$).

### Step 5: Cluster Scaling Actuation (Kubernetes API)
* If the desired replica count differs from current replicas, the controller executes:
  ```python
  apps_v1.patch_namespaced_deployment_scale(
      name="target-app",
      namespace="default",
      body={"spec": {"replicas": desired_replicas}}
  )
  ```
* Kubernetes immediately provisions the additional pods.
* **The Victory:** Because scaling was initiated **60 seconds early**, the new pods finish initialization and pass health checks **right when the Locust traffic peak arrives**.

### Step 6: Real-Time Visualization (Next.js Dashboard)
* The scaling event and metrics are streamed via WebSocket to the dashboard.
* The operator sees:
  - The actual traffic curve tracking inside the shaded prediction band.
  - The pod count incrementing smoothly.
  - P99 latency remaining flat under **100ms** (compared to 800–1200ms under standard HPA).

---

# 5. Decision Arbiter Mathematical Logic & Pseudocode

### 1. Asymmetric Loss Function
Standard regression models treat under-predicting and over-predicting equally. In cloud systems, under-predicting causes site outages, whereas over-predicting merely wastes minor pod idle capacity:
$$L(\hat{y}, y) = \begin{cases} 3 \cdot |y - \hat{y}| & \text{if } \hat{y} < y \quad \text{(Under-prediction: Catastrophic penalty)} \\ 1 \cdot |y - \hat{y}| & \text{if } \hat{y} \ge y \quad \text{(Over-prediction: Minor penalty)} \end{cases}$$

### 2. Decision Logic Pseudocode
```python
import math
from kubernetes import client

TARGET_CPU_PER_POD = 60.0  # Desired optimal pod utilization (%)
MIN_PODS = 2
MAX_PODS = 15

def calculate_optimal_replicas(live_cpu, predicted_upper_cpu, current_replicas):
    # 1. Proactive Scaling Target (from 90% Conformal Upper Bound)
    predictive_replicas = math.ceil(predicted_upper_cpu / TARGET_CPU_PER_POD)

    # 2. Reactive Emergency Fallback
    # Protects against zero-day flash surges or ML prediction errors
    if live_cpu > 85.0:
        reactive_replicas = current_replicas + 2
    else:
        reactive_replicas = current_replicas

    # 3. Hybrid Arbitration: Take the safer of both
    target_replicas = max(predictive_replicas, reactive_replicas)

    # 4. Clamp within cluster resource limits
    final_replicas = min(max(target_replicas, MIN_PODS), MAX_PODS)

    return final_replicas
```

---

# 6. Our 3 Core Technical Novelties (Viva Defense Goldmines)

If a mentor, examiner, or interviewer asks: *"What makes this an advanced engineering capstone instead of a basic automation script?"*, use these 3 points:

1. **Conformal Prediction Over Fragile Point Estimates:**
   Standard regression models produce single deterministic numbers (e.g., *"CPU will be 74%"*). If the real load is 88%, the cluster fails. We use **Conformal Inference (MAPIE)** to produce mathematically guaranteed prediction intervals $[L, U]$ without making Gaussian noise assumptions. Scaling against the **upper bound $U$** ensures cluster headroom.

2. **Asymmetric Cost-Aware Training:**
   We penalize under-provisioning $3\times$ heavier than over-provisioning during model optimization. The model inherently prioritizes service availability over marginal idle resource savings.

3. **Closed-Loop Actuation with Hybrid Safety Arbitration:**
   The IEEE base paper (*IEEE TKDE, May 2026*) was purely theoretical: it tested forecasting models offline on static CSV files. **Our contribution is operationalizing this into a live, closed-loop Kubernetes controller** featuring an emergency reactive guard that guarantees stability even if the ML model fails.

---

# 7. Team Workload & Module Division (4 Members)

| Member | Domain | Key Deliverables |
| :--- | :--- | :--- |
| **Member 1** | **Machine Learning & Conformal Prediction** | Feature engineering pipeline, XGBoost training, MAPIE conformal intervals, and asymmetric loss implementation. |
| **Member 2** | **Cloud Orchestration & Actuator** | Docker containerization, Minikube cluster setup, Prometheus Helm deployment, and Python Kubernetes API scaling actuator. |
| **Member 3** | **Backend & Load Testing** | FastAPI Telemetry Ingestion service, Locust load profiles (Ramp, Spike, Diurnal), PostgreSQL schema, and WebSocket streaming. |
| **Member 4** | **Frontend UI & Benchmark Evaluation** | Next.js 14 dashboard, Recharts prediction cone visualization, experimental benchmarking runs, thesis documentation, and slide decks. |

---

# 8. Frequently Asked Viva Questions & Ready-to-Speak Answers

### Q1: "Explain your project in 30 seconds."
> *"Our project eliminates the cold-start latency delay in cloud autoscaling. Default Kubernetes HPA is reactive: it waits until CPU is already overloaded, taking 60+ seconds to boot pods while users suffer 504 timeouts. We built CloudPulse, a predictive framework using Conformal Machine Learning to forecast traffic 60 seconds early and pre-scale pods, backed by an emergency reactive safety guard."*

### Q2: "What is your novel contribution compared to the IEEE TKDE base paper?"
> *"The IEEE paper is purely algorithmic—it proposes IPOC and tests it on offline CSV traces. Our novelty is **operationalizing** this theory: we built a closed-loop Kubernetes actuator, an asymmetric loss penalty (heavier penalty for under-provisioning), a hybrid reactive fail-safe, and a live monitoring control plane."*

### Q3: "What happens if your machine learning model makes a bad prediction?"
> *"We have two layers of protection: First, we scale based on the **upper bound of the 90% conformal interval**, which accounts for statistical uncertainty. Second, we built a **hardware-level reactive safety guard**—if cluster CPU ever crosses 85%, the system immediately triggers emergency pod additions regardless of the ML forecast."*

### Q4: "Why did you use Conformal Prediction instead of standard ML?"
> *"Standard ML only gives a single point estimate (e.g., 'CPU will be 70%'). If that estimate is wrong by 15%, the system crashes. Conformal prediction provides mathematically guaranteed prediction intervals without assuming a normal distribution, giving us a safe upper bound to scale against."*

---

*Document compiled for [shawaz03/FinalYearProject](https://github.com/shawaz03/FinalYearProject) | Capstone Project A8-8*
