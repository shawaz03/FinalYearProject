# experiments

The benchmark harness and its output. This is the long pole of the project —
owned from Phase 2 onward, not Phase 7.

- `run.py` — takes (scaler, profile, warmup, seed, rep); enforces a clean
  cluster and a settle period; writes `manifest.json`
- `results/<run_id>/` — manifest, k6 summary, decision log, telemetry export

**One controller per run.** CloudPulse, HPA and static never run against the
same Deployment simultaneously; the runner refuses to start if an HPA exists
on the target (T7.1.2).

Every number in any report must trace back to a `run_id` here. Owner: M4.
