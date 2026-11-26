import type { ForecastFrame, TelemetryFrame } from "./telemetry";

/**
 * One row of the aligned timeline. Every chart in the stack renders from this
 * same array and the same x domain, so the three plots line up exactly and a
 * crosshair means the same instant in all of them.
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

function lerp(a: number, b: number, w: number) {
  return a + (b - a) * w;
}

/**
 * Interpolate the forecast series onto the telemetry timestamps.
 *
 * Forecasts arrive every 15s while telemetry is every 5s. Rendering the sparse
 * series directly would leave the band full of gaps, and stacked areas with
 * nulls render unpredictably. Interpolating gives a continuous band without
 * inventing precision: the interpolated points sit between two real forecasts.
 */
export function mergeSeries(
  telemetry: TelemetryFrame[],
  forecasts: ForecastFrame[],
  capacityRps: number,
): Row[] {
  const byTarget = [...forecasts].sort((a, b) => a.targetT - b.targetT);

  const at = (t: number) => {
    if (byTarget.length === 0) return null;
    if (t < byTarget[0].targetT || t > byTarget[byTarget.length - 1].targetT) {
      return null;
    }
    let hi = byTarget.findIndex((f) => f.targetT >= t);
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

  return telemetry.map((frame) => {
    const f = at(frame.t);
    const lower = f ? round(f.lower, 1) : null;
    const upper = f ? round(f.upper, 1) : null;
    return {
      t: frame.t,
      rps: frame.rps,
      fPoint: f ? round(f.point, 1) : null,
      fLower: lower,
      fUpper: upper,
      fRange: lower != null && upper != null ? [lower, upper] : null,
      replicas: frame.readyReplicas,
      desired: frame.desiredReplicas,
      p99: frame.p99,
      capacity: round(frame.readyReplicas * capacityRps, 1),
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
