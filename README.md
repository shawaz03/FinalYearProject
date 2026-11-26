# CloudPulse: Smart Cloud Resource Optimization
### Load-Aware Predictive Auto-Scaling Framework in Cloud Systems (Team A8-8)

[![GitHub repo](https://img.shields.io/badge/GitHub-FinalYearProject-blue?logo=github)](https://github.com/shawaz03/FinalYearProject)
[![MBU Capstone](https://img.shields.io/badge/MBU-Capstone%20Project-green)](#)
[![Domain](https://img.shields.io/badge/Domain-Cloud%20Computing%20%7C%20Auto--scaling%20%7C%20ML-orange)](#)

---

## 📌 Project Overview

**CloudPulse** is an uncertainty-aware, predictive auto-scaling framework designed to eliminate cold-start latency and service degradation in cloud systems under dynamic, bursty workloads.

Traditional auto-scalers (e.g., standard Kubernetes Horizontal Pod Autoscaler - HPA) operate **reactively**: they wait until resource utilization (like CPU or memory) breaches a threshold before triggering new container instances. However, because new containers take **30 to 90 seconds** to initialize, schedule, and become ready, users experience slow responses, timeouts, and severe SLA violations during sudden traffic spikes.

**CloudPulse** introduces a **predictive + reactive hybrid model**:
1. **Predictive Layer:** Continuously evaluates real-time workload trends (past 5 minutes) to forecast incoming load 60 seconds into the future and pre-warms containers *before* the spike arrives.
2. **Uncertainty & Safety Layer:** Wraps predictions with confidence intervals and features an emergency reactive guard (e.g., instant scale-up if CPU exceeds 85%) to handle unpredicted anomaly surges.

---

## 🏗️ Architecture & Technology Stack

```
                          [ Incoming HTTP Traffic ]
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │     Locust Load Test      │
                        │ (Bursty & Organic Traffic)│
                        └─────────────┬─────────────┘
                                      │
                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster (Minikube)                   │
│                                                                        │
│   ┌────────────────────┐                   ┌───────────────────────┐   │
│   │  Application Pods  │ <── Auto-scaled ──┤ CloudPulse Controller │   │
│   │ (Docker Container) │                   │  (Predictive + Hybrid)│   │
│   └─────────┬──────────┘                   └───────────▲───────────┘   │
│             │ Metrics                                  │               │
│             ▼                                          │ Predictions   │
│   ┌────────────────────┐                   ┌───────────┴───────────┐   │
│   │ Prometheus Metrics │ ────────────────> │ Machine Learning Model│   │
│   │      Exporter      │                   │ (Time-Series / ACCI)  │   │
│   └────────────────────┘                   └───────────────────────┘   │
└─────────────────────────────────────┬──────────────────────────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │  Live React Dashboard UI  │
                        │ (CPU, Pod Count, Latency) │
                        └───────────────────────────┘
```

- **Containerization & Orchestration:** Docker, Kubernetes (Minikube)
- **Monitoring & Telemetry:** Prometheus
- **Load Generation & Stress Testing:** Locust (HTTP synthetic load generation)
- **Forecasting Models:** Time-series forecasting (Polynomial Regression / ACCI Conformal Prediction)
- **Visualization:** React.js Live Metric Dashboard

---

## 📂 Repository Deliverables & Structure

This repository contains all technical blueprints, presentation slide decks, evaluation docs, and defense preparation resources for the Capstone Project:

| File / Resource | Description |
| :--- | :--- |
| **`CLOUDPULSE_TECHNICAL_BLUEPRINT.md`** | Comprehensive architectural blueprint, domain breakdown, IEEE paper mapping, and phase-by-phase execution plan. |
| **`CLOUDPULSE_PROJECT_EVALUATION.md`** / **`.pdf`** / **`.html`** | Rigorous evaluation metrics, performance graphs, benchmark comparisons, and latency analysis. |
| **`Complete_Mentor_Defense_Guide.md`** / **`.docx`** | Detailed question-and-answer defense guide with ready-to-speak scripts for mentor reviews and viva evaluations. |
| **`Smart_Cloud_Resource_Optimization_10pages_MBU_format.docx`** | Complete 10-page academic project documentation formatted according to MBU Capstone guidelines. |
| **`Review_0_A8-8.pptx`** / **`review 0.pptx`** | Formal presentation slide decks for Review 0 milestone presentation. |
| **`Review_0_PPT_Content.docx`** | Speaker notes, slide breakdown, and script for the Review 0 presentation deck. |
| **`MBU Capstone Project Review Presentation - Template.pptx`** | Official departmental presentation template for upcoming review stages. |
| **`there idea.jpeg`** | Conceptual system architecture diagram and workflow sketch. |
| **`there abstract.docx`** | Project abstract and preliminary problem definition. |

---

## 🎯 Key Project Highlights & Defense Points

- **Zero-Downtime Scaling:** Eliminates 30–90 second pod provisioning delays by scaling proactively.
- **Hybrid Resilience:** Combines proactive forecasting with threshold-based reactive fallback protection.
- **Realistic Validation:** Tested with non-linear, multi-step traffic patterns rather than purely theoretical synthetic data.
- **Production-Oriented Tooling:** Built on industry standard cloud tools (Kubernetes, Prometheus, Locust).

---

## 👥 Project Team & Affiliation

- **Team ID:** A8-8
- **Project Lead / Contributor:** [shawaz03](https://github.com/shawaz03)
- **Institution:** Mohan Babu University (MBU)
- **Degree:** Bachelor of Technology - Capstone Project
