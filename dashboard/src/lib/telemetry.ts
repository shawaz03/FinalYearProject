/**
 * The CloudPulse telemetry contract.
 *
 * These types are the WebSocket message shape that T6.1 must emit. The
 * dashboard is written against them, so building it now PINS THE CONTRACT
 * rather than guessing at it later: whatever the arbiter broadcasts has to
 * match what is here.
 *
 * Field semantics follow deploy/queries.md exactly. In particular:
 *   - `rps` is the replica-independent demand signal the model forecasts.
 *     It is NOT CPU utilisation percent (CLAUDE.md §3.2).
 *   - latency quantiles are CLIENT-SIDE (k6) values. Server histograms are
 *     advisory only (CLAUDE.md §3.5).
 */

export type ScalerName = "cloudpulse" | "hpa-default" | "hpa-tuned" | "static";

/** One 5-second telemetry sample. */
export interface TelemetryFrame {
  /** epoch ms */
  t: number;
  /** requests/sec, aggregate across pods — the forecast target */
  rps: number;
  /** sum of per-pod CPU, millicores */
  cpuMillicores: number;
  /** client-side latency, milliseconds */
  p50: number;
  p90: number;
  p99: number;
  /** authoritative count from the Kubernetes API, not Prometheus */
  readyReplicas: number;
  desiredReplicas: number;
  /** share of requests slower than the SLO, 0..1 */
  slaViolationRate: number;
}

/** One interval forecast, made at `t`, describing demand at `targetT`. */
export interface ForecastFrame {
  /** when the forecast was produced */
  t: number;
  /** horizon in seconds (CLAUDE.md §3.3) */
  horizonS: number;
  /** t + horizonS — the time this interval describes */
  targetT: number;
  point: number;
  /** conformal interval; the arbiter scales on `upper` */
  lower: number;
  upper: number;
  modelVersion: string;
}

/** Why the arbiter chose a replica count. Mirrors the Phase 5 control layer. */
export type DecisionMode =
  | "predictive" // upper bound / C drove the result
  | "reactive" // the 85% CPU guard fired
  | "hold" // deadband or dwell time suppressed a change
  | "stabilising" // scale-down held by the 300s window
  | "clamped" // hit MIN_PODS / MAX_PODS
  | "degraded"; // forecaster timed out — reactive only

export interface Decision {
  t: number;
  mode: DecisionMode;
  fromReplicas: number;
  toReplicas: number;
  /** ceil(upper / C) */
  predictiveReplicas: number;
  /** current + 2 when per-pod CPU > 85%, else current */
  reactiveReplicas: number;
  /** the conformal upper bound used, in RPS */
  upperBound: number;
  /** measured capacity constant C, RPS per pod (Phase 2, T2.2) */
  capacityRps: number;
  reason: string;
}

export interface RunMeta {
  runId: string;
  scaler: ScalerName;
  profile: "gradual" | "flash" | "diurnal";
  warmupSeconds: number;
  horizonS: number;
  capacityRps: number;
  minPods: number;
  maxPods: number;
  sloMs: number;
  /** false until a real arbiter is connected (Phase 5) */
  live: boolean;
}

// ---------------------------------------------------------------------------
// Simulator
//
// Until the Phase 5 arbiter exists there is no live feed, so the dashboard
// runs against a deterministic simulation of a `flash` profile. It is seeded,
// so the demo is reproducible and reviewable.
//
// It is labelled as simulated in the UI at all times. A dashboard that cannot
// tell you whether its numbers are real is worse than no dashboard.
// ---------------------------------------------------------------------------

/** Deterministic PRNG (mulberry32) so every reload renders the same run. */
function makeRng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const RUN: RunMeta = {
  runId: "sim-flash-001",
  scaler: "cloudpulse",
  profile: "flash",
  warmupSeconds: 30,
  horizonS: 45,
  // Placeholder until Phase 2 T2.2 measures the real C. Shown in the UI as
  // provisional so nobody mistakes it for a measurement.
  capacityRps: 18,
  minPods: 2,
  maxPods: 15,
  sloMs: 100,
  live: false,
};

const STEP_MS = 5_000;

/** Offered load over the run: calm, ramp, flash spike, decay. */
function demandAt(minute: number): number {
  if (minute < 3) return 26 + Math.sin(minute * 2.1) * 4;
  if (minute < 6) return 26 + (minute - 3) * 14;
  if (minute < 7.5) return 68 + (minute - 6) * 92; // the flash
  if (minute < 11) return 206 - (minute - 7.5) * 14;
  if (minute < 14) return 157 - (minute - 11) * 34;
  return Math.max(24, 55 - (minute - 14) * 9);
}

export interface SimulatedRun {
  telemetry: TelemetryFrame[];
  forecasts: ForecastFrame[];
  decisions: Decision[];
}

export function simulateRun(seed = 7, minutes = 17): SimulatedRun {
  const rng = makeRng(seed);
  const t0 = Date.UTC(2026, 10, 26, 14, 0, 0);
  const steps = Math.floor((minutes * 60_000) / STEP_MS);

  const telemetry: TelemetryFrame[] = [];
  const forecasts: ForecastFrame[] = [];
  const decisions: Decision[] = [];

  let replicas = RUN.minPods + 1;
  let desired = replicas;
  // Pods requested but not yet serving, with the time they become Ready.
  const pending: number[] = [];
  let lastActuation = -Infinity;
  let cumulativeViolations = 0;
  let cumulativeRequests = 0;

  for (let i = 0; i < steps; i++) {
    const t = t0 + i * STEP_MS;
    const minute = (i * STEP_MS) / 60_000;

    const noise = (rng() - 0.5) * 7;
    const rps = Math.max(4, demandAt(minute) + noise);

    // Pods that have finished warming are now serving.
    while (pending.length && pending[0] <= t) {
      pending.shift();
      replicas += 1;
    }

    const capacity = replicas * RUN.capacityRps;
    const utilisation = rps / capacity;

    // Latency degrades sharply past saturation — the queueing effect the
    // whole project exists to avoid.
    const pressure = Math.max(0, utilisation - 0.72);
    const p50 = 8 + pressure * 120 + rng() * 2;
    const p90 = p50 * 1.7 + pressure * 210;
    const p99 = p50 * 2.4 + pressure * 680 + rng() * 6;

    const violated = p99 > RUN.sloMs ? Math.min(1, pressure * 2.4) : 0;
    cumulativeRequests += rps * (STEP_MS / 1000);
    cumulativeViolations += rps * (STEP_MS / 1000) * violated;

    telemetry.push({
      t,
      rps: round(rps, 1),
      cpuMillicores: round(rps * 13.2, 0),
      p50: round(p50, 1),
      p90: round(p90, 1),
      p99: round(p99, 1),
      readyReplicas: replicas,
      desiredReplicas: desired,
      slaViolationRate: cumulativeRequests
        ? cumulativeViolations / cumulativeRequests
        : 0,
    });

    // --- forecast, every 15s -------------------------------------------
    if (i % 3 === 0) {
      const targetMinute = minute + RUN.horizonS / 60;
      const truth = demandAt(targetMinute);
      // Model error grows with how fast demand is changing.
      const slope = Math.abs(truth - demandAt(minute));
      const bias = (rng() - 0.45) * (6 + slope * 0.1);
      const point = Math.max(4, truth + bias);
      // Conformal half-width widens under drift (ACI behaviour, §3.4).
      const halfWidth = 9 + slope * 0.42 + rng() * 5;
      forecasts.push({
        t,
        horizonS: RUN.horizonS,
        targetT: t + RUN.horizonS * 1000,
        point: round(point, 1),
        lower: round(Math.max(0, point - halfWidth), 1),
        upper: round(point + halfWidth, 1),
        modelVersion: "cqr-aci-0.3.1",
      });
    }

    // --- arbiter, every 30s --------------------------------------------
    if (i % 6 === 0) {
      const f = forecasts[forecasts.length - 1];
      const perPodCpuPct = Math.min(99, utilisation * 100);
      const predictive = f
        ? Math.ceil(f.upper / RUN.capacityRps)
        : replicas;
      const reactive = perPodCpuPct > 85 ? replicas + 2 : replicas;
      const raw = Math.max(predictive, reactive);
      const target = Math.min(RUN.maxPods, Math.max(RUN.minPods, raw));

      const total = replicas + pending.length;
      const dwellOk = t - lastActuation >= 60_000;
      let mode: DecisionMode;
      let applied = total;

      if (target === total) {
        mode = "hold";
      } else if (target > total) {
        mode = reactive > predictive ? "reactive" : "predictive";
        if (raw > RUN.maxPods) mode = "clamped";
        applied = target;
      } else if (!dwellOk) {
        mode = "stabilising";
      } else {
        mode = "predictive";
        applied = Math.max(target, total - 1); // scale down one at a time
      }

      if (applied !== total) {
        if (applied > total) {
          for (let k = 0; k < applied - total; k++) {
            // Readiness = WARMUP_SECONDS + ~5s platform overhead.
            // Measured in T2.3.1: overhead was 4.5-5.7s across the sweep.
            pending.push(t + (RUN.warmupSeconds + 5) * 1000);
          }
        } else {
          replicas = applied;
        }
        lastActuation = t;
      }
      desired = applied;

      decisions.push({
        t,
        mode,
        fromReplicas: total,
        toReplicas: applied,
        predictiveReplicas: predictive,
        reactiveReplicas: reactive,
        upperBound: f ? f.upper : 0,
        capacityRps: RUN.capacityRps,
        reason: reasonFor(mode, perPodCpuPct, dwellOk),
      });
    }
  }

  return { telemetry, forecasts, decisions };
}

function reasonFor(mode: DecisionMode, cpuPct: number, dwellOk: boolean) {
  switch (mode) {
    case "predictive":
      return "ceil(upper / C)";
    case "reactive":
      return `per-pod CPU ${cpuPct.toFixed(0)}% > 85%`;
    case "hold":
      return "within deadband";
    case "stabilising":
      return dwellOk ? "scale-down window" : "min dwell 60s";
    case "clamped":
      return "at MAX_PODS";
    case "degraded":
      return "forecaster unavailable";
  }
}

function round(n: number, dp: number) {
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

/** Pod-seconds: the integral of replicas over time. The primary cost metric
 *  (CLAUDE.md finding D14 — dollars on a local cluster are fictional). */
export function podSeconds(frames: TelemetryFrame[]): number {
  let total = 0;
  for (let i = 1; i < frames.length; i++) {
    const dt = (frames[i].t - frames[i - 1].t) / 1000;
    total += ((frames[i].readyReplicas + frames[i - 1].readyReplicas) / 2) * dt;
  }
  return total;
}
