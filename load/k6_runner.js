/**
 * CloudPulse open-loop k6 load generator (Phase 2, T2.1).
 *
 * Uses k6 `constant-arrival-rate` scenarios driven exclusively from JSON config
 * (`load/profiles/*.json`) or an explicit single-step TARGET_RPS / DURATION_S
 * override (used by `experiments/measure_capacity.py` in T2.2).
 *
 * Open-loop execution avoids coordinated omission: arrival cadence stays
 * fixed regardless of response time, exposing real queuing latency under load.
 *
 * Examples:
 *   k6 run -e TARGET_URL=http://127.0.0.1:8000 -e PROFILE=flash \
 *     -e K6_SUMMARY_OUT=experiments/results/test-01/k6_summary.json load/k6_runner.js
 *
 *   k6 run -e TARGET_URL=http://127.0.0.1:8000 -e TARGET_RPS=15 -e DURATION_S=60 \
 *     -e K6_SUMMARY_OUT=experiments/results/cap-step/k6_summary.json load/k6_runner.js
 */

import http from "k6/http";
import { check } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const TARGET_URL = (__ENV.TARGET_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const PROFILE_NAME = __ENV.PROFILE || "flash";
const SUMMARY_OUT = __ENV.K6_SUMMARY_OUT || "k6_summary.json";
const RPS_SCALE = Number(__ENV.RPS_SCALE || "1.0");
const DURATION_SCALE = Number(__ENV.DURATION_SCALE || "1.0");
const REQUEST_TIMEOUT = __ENV.REQUEST_TIMEOUT || "5s";

if (!Number.isFinite(RPS_SCALE) || RPS_SCALE <= 0) {
  throw new Error(`Invalid RPS_SCALE: ${__ENV.RPS_SCALE}`);
}
if (!Number.isFinite(DURATION_SCALE) || DURATION_SCALE <= 0) {
  throw new Error(`Invalid DURATION_SCALE: ${__ENV.DURATION_SCALE}`);
}

function loadSchedule() {
  if (__ENV.TARGET_RPS) {
    const targetRps = Number(__ENV.TARGET_RPS);
    const durationS = Number(__ENV.DURATION_S || "60");
    const sloMs = Number(__ENV.SLO_MS || "100");
    if (!Number.isFinite(targetRps) || targetRps <= 0) {
      throw new Error(`Invalid TARGET_RPS: ${__ENV.TARGET_RPS}`);
    }
    if (!Number.isFinite(durationS) || durationS <= 0) {
      throw new Error(`Invalid DURATION_S: ${__ENV.DURATION_S}`);
    }
    return {
      profile: "constant-step",
      slo_ms: sloMs,
      target_path: __ENV.TARGET_PATH || "/api/work",
      segments: [
        { name: "constant-step", duration_s: durationS, target_rps: targetRps },
      ],
    };
  }

  const profilePath = __ENV.PROFILE_PATH || `./profiles/${PROFILE_NAME}.json`;
  const parsed = JSON.parse(open(profilePath));
  if (!Array.isArray(parsed.segments) || parsed.segments.length === 0) {
    throw new Error(`Profile ${profilePath} has no segments`);
  }
  return parsed;
}

const schedule = loadSchedule();
const SLO_MS = Number(schedule.slo_ms || 100);
const TARGET_PATH = schedule.target_path || "/api/work";

const scenarios = {};
let offsetS = 0;
let plannedRequests = 0;

schedule.segments.forEach((seg, idx) => {
  const durationS = Math.max(1, Math.round(Number(seg.duration_s) * DURATION_SCALE));
  const rate = Math.max(1, Math.round(Number(seg.target_rps) * RPS_SCALE));
  const preAllocatedVUs = Math.max(10, Math.min(200, Math.ceil(rate * 0.5)));
  const maxVUs = Math.max(preAllocatedVUs * 2, Math.min(800, rate * 4));

  const key = `seg_${String(idx).padStart(2, "0")}_${String(seg.name || "step").replace(/[^a-zA-Z0-9_]/g, "_")}`;
  scenarios[key] = {
    executor: "constant-arrival-rate",
    rate,
    timeUnit: "1s",
    duration: `${durationS}s`,
    startTime: `${offsetS}s`,
    preAllocatedVUs,
    maxVUs,
    gracefulStop: "2s",
  };

  offsetS += durationS;
  plannedRequests += rate * durationS;
});

const TOTAL_DURATION_S = offsetS;
const WEIGHTED_TARGET_RPS = TOTAL_DURATION_S > 0 ? plannedRequests / TOTAL_DURATION_S : 0;

export const slaViolations = new Rate("sla_violations");
export const clientLatencyMs = new Trend("client_latency_ms", true);
export const requestsOk = new Counter("requests_ok");
export const requestsFailed = new Counter("requests_failed");

export const options = {
  scenarios,
  // Explicitly request p(50) and p(99): k6 defaults omit both from handleSummary.
  summaryTrendStats: ["avg", "min", "med", "max", "p(50)", "p(90)", "p(95)", "p(99)"],
  discardResponseBodies: true,
};

export default function () {
  const res = http.get(`${TARGET_URL}${TARGET_PATH}`, {
    timeout: REQUEST_TIMEOUT,
    tags: { profile: schedule.profile || PROFILE_NAME },
  });

  const ok = check(res, {
    "status is 200": (r) => r.status === 200,
    "within SLO": (r) => r.timings.duration <= SLO_MS,
  });

  clientLatencyMs.add(res.timings.duration);
  slaViolations.add(!ok);
  if (res.status === 200) {
    requestsOk.add(1);
  } else {
    requestsFailed.add(1);
  }
}

function metricValue(data, name, field, fallback = 0) {
  const m = data.metrics && data.metrics[name];
  if (!m || !m.values || m.values[field] === undefined || m.values[field] === null) {
    return fallback;
  }
  return Number(m.values[field]);
}

function round(n, dp = 3) {
  const factor = 10 ** dp;
  return Math.round(n * factor) / factor;
}

export function handleSummary(data) {
  const totalReqs = metricValue(data, "http_reqs", "count", 0);
  const dropped = metricValue(data, "dropped_iterations", "count", 0);
  const achievedRps = TOTAL_DURATION_S > 0 ? totalReqs / TOTAL_DURATION_S : 0;
  const trackingErrorPct =
    WEIGHTED_TARGET_RPS > 0
      ? ((achievedRps - WEIGHTED_TARGET_RPS) / WEIGHTED_TARGET_RPS) * 100
      : 0;

  const p50 = metricValue(data, "http_req_duration", "p(50)", metricValue(data, "http_req_duration", "med", 0));
  const p90 = metricValue(data, "http_req_duration", "p(90)", 0);
  const p95 = metricValue(data, "http_req_duration", "p(95)", 0);
  const p99 = metricValue(data, "http_req_duration", "p(99)", 0);

  const summary = {
    profile: schedule.profile || PROFILE_NAME,
    target_url: `${TARGET_URL}${TARGET_PATH}`,
    slo_ms: SLO_MS,
    rps_scale: RPS_SCALE,
    duration_scale: DURATION_SCALE,
    total_duration_s: TOTAL_DURATION_S,
    planned_requests: plannedRequests,
    total_requests: totalReqs,
    requests_ok: metricValue(data, "requests_ok", "count", 0),
    requests_failed: metricValue(data, "requests_failed", "count", 0),
    dropped_iterations: dropped,
    target_rps: round(WEIGHTED_TARGET_RPS, 3),
    achieved_rps: round(achievedRps, 3),
    tracking_error_pct: round(trackingErrorPct, 3),
    http_req_failed_rate: round(metricValue(data, "http_req_failed", "rate", 0), 6),
    sla_violation_rate: round(metricValue(data, "sla_violations", "rate", 0), 6),
    latency_ms: {
      avg: round(metricValue(data, "http_req_duration", "avg", 0), 3),
      min: round(metricValue(data, "http_req_duration", "min", 0), 3),
      p50: round(p50, 3),
      p90: round(p90, 3),
      p95: round(p95, 3),
      p99: round(p99, 3),
      max: round(metricValue(data, "http_req_duration", "max", 0), 3),
    },
  };

  const banner = [
    `CloudPulse k6 summary [${summary.profile}]`,
    `  duration     : ${summary.total_duration_s}s`,
    `  target / ach : ${summary.target_rps} / ${summary.achieved_rps} RPS (err ${summary.tracking_error_pct}%)`,
    `  dropped      : ${summary.dropped_iterations}`,
    `  p50/p90/p99  : ${summary.latency_ms.p50} / ${summary.latency_ms.p90} / ${summary.latency_ms.p99} ms`,
    `  sla_viol     : ${(summary.sla_violation_rate * 100).toFixed(2)}% (SLO <= ${SLO_MS} ms)`,
    `  output       : ${SUMMARY_OUT}`,
  ].join("\n");

  return {
    [SUMMARY_OUT]: JSON.stringify(summary, null, 2),
    stdout: `${banner}\n`,
  };
}
