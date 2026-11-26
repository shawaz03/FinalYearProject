# analysis

Turns `experiments/results/` into the tables and figures used in the thesis.

- `metrics.py` — SLA violation %, p99 latency, pod-seconds, actuation count,
  time-to-ready-capacity, coverage
- `stats.ipynb` — Kruskal-Wallis + Dunn post-hoc (latency is not normal),
  effect sizes and CIs, n stated per cell
- `figures/` — committed plots

Primary cost metric is **pod-seconds**, not dollars. Dollar figures are a
derived display and must print their price assumption. CLAUDE.md §4, D14.

Phase 7. Owner: M4.
