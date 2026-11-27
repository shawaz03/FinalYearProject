import type { ForecastFrame, TelemetryFrame } from "./telemetry";

/**
 * One row of the aligned timeline. Every chart in the stack renders from this
 * same array and the same x domain, so the three plots line up exactly and a
 * crosshair means the same instant in all of them.
 *
 * Rows beyond the latest measured telemetry represent the leading forecast
 * horizon `[now, now + H]`: measured fields are `null` while `fPoint`,
 * `fLower`, `fUpper`, and `fRange` extend forward ahead of the demand curve.
 */
export interface Row {
  t: number;
  /** measured demand */
  rps: number | null;
  /** forecast, positioned at the time it DESCRIBES (t + H), not when it was made */
  fPoint: number | null;
  fLower: number | null;
  fUpper: number | null;
  /** [lower, upper] as a Recharts range area — null-safe, unlike stacked areas */
  fRange: [number, number] | null;
  replicas: number | null;
  desired: number | null;
  p99: number | null;
  /** capacity the running replicas can serve, in RPS — the honest "is it enough" line */
  capacity: number | null;
}

const STEP_MS = 5_000;

function lerp(a: number, b: number, w: number) {
  return a + (b - a) * w;
}

/**
 * Interpolate the forecast series onto telemetry timestamps plus any leading
 * horizon timestamps up to `max(forecasts.targetT)`.
 */
export function mergeSeries(
  telemetry: TelemetryFrame[],
  forecasts: ForecastFrame[],
  capacityRps: number,
): Row[] {
  const byTarget = [...forecasts].sort((a, b) => a.targetT - b.targetT);
  const byTime = new Map<number, TelemetryFrame>();
  for (const frame of telemetry) {
    byTime.set(frame.t, frame);
  }

  const times = new Set<number>(telemetry.map((f) => f.t));
  if (telemetry.length > 0 && byTarget.length > 0) {
    const lastTelemetryT = telemetry[telemetry.length - 1].t;
    const maxTargetT = byTarget[byTarget.length - 1].targetT;
    for (let t = lastTelemetryT + STEP_MS; t < maxTargetT; t += STEP_MS) {
      times.add(t);
    }
    if (maxTargetT > lastTelemetryT) {
      times.add(maxTargetT);
    }
  } else {
    for (const f of byTarget) {
      times.add(f.targetT);
    }
  }

  const sortedTimes = [...times].sort((a, b) => a - b);

  const at = (t: number) => {
    if (byTarget.length === 0) return null;
    if (t < byTarget[0].targetT || t > byTarget[byTarget.length - 1].targetT) {
      return null;
    }
    const hi = byTarget.findIndex((f) => f.targetT >= t);
    if (hi < 0) return null;
    if (hi === 0) return byTarget[0];
    const lo = byTarget[hi - 1];
    const h = byTarget[hi];
    const span = h.targetT - lo.targetT;
    const w = span === 0 ? 0 : (t - lo.targetT) / span;
    return {
      point: lerp(lo.point, h.point, w),
      lower: lerp(lo.lower, h.lower, w),
      upper: lerp(lo.upper, h.upper, w),
    };
  };

  return sortedTimes.map((t) => {
    const frame = byTime.get(t);
    const f = at(t);
    const lower = f ? round(f.lower, 1) : null;
    const upper = f ? round(f.upper, 1) : null;
    return {
      t,
      rps: frame ? frame.rps : null,
      fPoint: f ? round(f.point, 1) : null,
      fLower: lower,
      fUpper: upper,
      fRange: lower != null && upper != null ? [lower, upper] : null,
      replicas: frame ? frame.readyReplicas : null,
      desired: frame ? frame.desiredReplicas : null,
      p99: frame ? frame.p99 : null,
      capacity: frame ? round(frame.readyReplicas * capacityRps, 1) : null,
    };
  });
}

function round(n: number, dp: number) {
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

/** Nice round axis ticks, so the y scale reads 0 / 50 / 100 rather than 0 / 47 / 94. */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = 0; v <= max + step * 0.5; v += step) out.push(round(v, 4));
  return out;
}
