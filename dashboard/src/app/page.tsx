"use client";

import * as React from "react";
import gsap from "gsap";
import {
  Activity,
  DollarSign,
  Moon,
  Pause,
  Play,
  Radio,
  RotateCcw,
  Sun,
  TriangleAlert,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { ArbiterTrailMini, DecisionLog } from "@/components/decision-log";
import { MetricBar } from "@/components/metric-bar";
import { PodMatrix } from "@/components/pod-matrix";
import {
  DemandPlot,
  LatencyPlot,
  Legend,
  ReplicaPlot,
} from "@/components/charts/timeline";
import { mergeSeries } from "@/lib/series";
import { RUN, podSeconds, simulateRun } from "@/lib/telemetry";
import {
  DEFAULT_WS_URL,
  useCloudPulseStream,
} from "@/lib/use-cloudpulse-stream";
import { clockHHMMSS, cn, compact } from "@/lib/utils";

/* ----------------------------- theme store -------------------------------- */

function subscribeTheme(onChange: () => void): () => void {
  window.addEventListener("cp-theme-change", onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener("cp-theme-change", onChange);
    window.removeEventListener("storage", onChange);
  };
}

function getThemeSnapshot(): "light" | "dark" {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "dark"
    : "light";
}

function getServerTheme(): "light" | "dark" {
  return "light";
}

/* ----------------------------- replay playhead ---------------------------- */

function useReplay(total: number) {
  const [head, setHead] = React.useState<number | null>(null);
  const [playing, setPlaying] = React.useState(false);
  const proxy = React.useRef({ v: total });
  const tween = React.useRef<gsap.core.Tween | null>(null);

  const stop = React.useCallback(() => {
    tween.current?.kill();
    tween.current = null;
    setPlaying(false);
  }, []);

  const reset = React.useCallback(() => {
    stop();
    setHead(null);
  }, [stop]);

  const play = React.useCallback(
    (from = 0) => {
      tween.current?.kill();
      proxy.current.v = from;
      setHead(from);
      setPlaying(true);
      tween.current = gsap.to(proxy.current, {
        v: total,
        duration: Math.max(6, total * 0.055),
        ease: "none",
        onUpdate: () => setHead(Math.round(proxy.current.v)),
        onComplete: () => {
          setPlaying(false);
          setHead(null);
        },
      });
    },
    [total],
  );

  React.useEffect(() => () => void tween.current?.kill(), []);
  return {
    head: head === null ? total : Math.min(head, total),
    playing,
    play,
    stop,
    reset,
  };
}

function useRoll(value: number) {
  const [shown, setShown] = React.useState(value);
  const proxy = React.useRef({ v: value });
  React.useEffect(() => {
    const t = gsap.to(proxy.current, {
      v: value,
      duration: 0.45,
      ease: "power2.out",
      onUpdate: () => setShown(proxy.current.v),
    });
    return () => void t.kill();
  }, [value]);
  return shown;
}

type ViewTab = "overview" | "pods" | "audit" | "table";

const ASSUMED_USD_PER_POD_HOUR = 0.0425;
const POD_CPU_LIMIT_MILLICORES = 250;

export default function Page() {
  const simRun = React.useMemo(() => simulateRun(), []);
  const stream = useCloudPulseStream(
    process.env.NEXT_PUBLIC_WS_URL || undefined,
  );

  const usingStream =
    stream.status === "connected" &&
    stream.meta !== null &&
    stream.telemetry.length > 0;

  const activeMeta = usingStream && stream.meta ? stream.meta : RUN;
  const activeTelemetry = usingStream ? stream.telemetry : simRun.telemetry;
  const activeForecasts = usingStream ? stream.forecasts : simRun.forecasts;
  const activeDecisions = usingStream ? stream.decisions : simRun.decisions;

  const { head, playing, play, stop, reset } = useReplay(
    activeTelemetry.length,
  );
  const [activeTab, setActiveTab] = React.useState<ViewTab>("overview");
  const [showDollars, setShowDollars] = React.useState(false);
  const theme = React.useSyncExternalStore(
    subscribeTheme,
    getThemeSnapshot,
    getServerTheme,
  );
  const stage = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = gsap.context(() => {
      gsap.from("[data-stagger]", {
        opacity: 0,
        y: 8,
        duration: 0.42,
        ease: "power2.out",
        stagger: 0.05,
      });
    }, stage);
    return () => ctx.revert();
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("cp-theme", next);
    window.dispatchEvent(new Event("cp-theme-change"));
  };

  const visibleCount = usingStream
    ? activeTelemetry.length
    : Math.max(2, head);
  const visibleTelemetry = activeTelemetry.slice(0, visibleCount);
  const latestObs = visibleTelemetry[visibleTelemetry.length - 1];

  const visibleForecasts = React.useMemo(
    () =>
      latestObs
        ? activeForecasts.filter((f) => f.t <= latestObs.t)
        : activeForecasts,
    [activeForecasts, latestObs],
  );

  const rows = React.useMemo(
    () =>
      mergeSeries(visibleTelemetry, visibleForecasts, activeMeta.capacityRps),
    [visibleTelemetry, visibleForecasts, activeMeta.capacityRps],
  );

  const fullRows = React.useMemo(
    () =>
      mergeSeries(activeTelemetry, activeForecasts, activeMeta.capacityRps),
    [activeTelemetry, activeForecasts, activeMeta.capacityRps],
  );

  const domain: [number, number] =
    fullRows.length > 0
      ? [fullRows[0].t, fullRows[fullRows.length - 1].t]
      : [0, 1];
  const decisions = latestObs
    ? activeDecisions.filter((d) => d.t <= latestObs.t)
    : [];

  const cost = podSeconds(visibleTelemetry);
  const elapsedSeconds =
    visibleTelemetry.length >= 2
      ? Math.max(
          5,
          (visibleTelemetry[visibleTelemetry.length - 1].t -
            visibleTelemetry[0].t) /
            1000,
        )
      : 5;

  const violation = latestObs?.slaViolationRate ?? 0;
  const rolled = useRoll(violation * 100);
  const compliancePct = Math.max(0, Math.min(100, 100 - violation * 100));
  const breaching = latestObs ? latestObs.p99 > activeMeta.sloMs : false;
  const capacityNow = latestObs
    ? latestObs.readyReplicas * activeMeta.capacityRps
    : 0;
  const headroom =
    latestObs && latestObs.rps > 0 ? capacityNow / latestObs.rps : 0;
  const over = violation > 0.02;

  const cpuPct = (frame: typeof latestObs | undefined) => {
    if (!frame) return 0;
    const alloc = Math.max(1, frame.readyReplicas * POD_CPU_LIMIT_MILLICORES);
    return Math.min(100, (frame.cpuMillicores / alloc) * 100);
  };
  const currentCpuPct = cpuPct(latestObs);

  const sparkOf = (pick: (i: number) => number) => {
    const n = Math.min(14, visibleTelemetry.length);
    return Array.from({ length: n }, (_, k) =>
      pick(visibleTelemetry.length - n + k),
    );
  };

  const sourceBadge = usingStream
    ? activeMeta.live
      ? {
          tone: "good" as const,
          label: "live",
          title: `Streaming live telemetry from ${stream.wsUrl}`,
        }
      : {
          tone: "forecast" as const,
          label: "ws replay",
          title: `Connected to WebSocket replay stream (${stream.wsUrl})`,
        }
    : {
        tone: "warning" as const,
        label: "simulated",
        title:
          stream.errorMessage || "No live arbiter connected — seeded simulation",
      };

  const latestForecast = visibleForecasts[visibleForecasts.length - 1];

  return (
    <div ref={stage} className="min-h-screen bg-plane text-ink">
      {/* --------------------- Pitch-Black Top Command Shell -------------------- */}
      <header className="sticky top-0 z-30 border-b border-monolith-border bg-monolith text-monolith-ink">
        <div className="mx-auto flex max-w-[1480px] flex-wrap items-center justify-between gap-4 px-6 py-3.5">
          {/* Brand mark */}
          <div className="flex items-center gap-3.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-[7px] bg-monolith-ink text-monolith">
              <Activity size={16} strokeWidth={2.3} aria-hidden />
            </div>
            <div className="leading-tight">
              <h1 className="text-[17px] font-bold tracking-tight text-monolith-ink">
                CloudPulse
              </h1>
              <div className="font-mono text-[9px] uppercase tracking-[0.14em] text-monolith-muted">
                Predictive Kubernetes Autoscaler
              </div>
            </div>
          </div>

          {/* Segmented View Switcher */}
          <nav
            aria-label="Dashboard views"
            className="flex flex-wrap items-center gap-1 rounded-[8px] border border-monolith-border bg-monolith-surface p-1"
          >
            {(
              [
                ["overview", "Control Plane"],
                ["pods", "Pod Matrix"],
                ["audit", "Audit Log"],
                ["table", "Data Table"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setActiveTab(key)}
                aria-pressed={activeTab === key}
                className={cn(
                  "rounded-[6px] px-3.5 py-1.5 text-[12px]",
                  activeTab === key
                    ? "bg-monolith-ink font-semibold text-monolith"
                    : "font-medium text-monolith-muted hover:text-monolith-ink",
                )}
              >
                {label}
              </button>
            ))}
          </nav>

          {/* Status & Connection Controls */}
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              tone={sourceBadge.tone}
              title={sourceBadge.title}
              className="rounded-full border-monolith-border bg-monolith-surface px-2.5 py-1 text-monolith-ink"
            >
              {usingStream ? (
                <Radio size={10} className="text-replicas" aria-hidden />
              ) : (
                <TriangleAlert size={10} className="text-forecast" aria-hidden />
              )}
              <span>
                {sourceBadge.label} · {activeMeta.runId}
              </span>
            </Badge>

            <button
              onClick={() =>
                stream.status === "connected" || stream.status === "connecting"
                  ? stream.disconnect()
                  : stream.connect(DEFAULT_WS_URL)
              }
              aria-pressed={stream.status === "connected"}
              title={`WebSocket endpoint: ${DEFAULT_WS_URL}`}
              className={cn(
                "flex items-center gap-1.5 rounded-[7px] px-3 py-1.5 text-[11px] font-semibold transition-colors",
                stream.status === "connected"
                  ? "border border-monolith-border bg-monolith-surface text-monolith-ink hover:bg-monolith-border"
                  : "bg-monolith-ink text-monolith hover:opacity-90",
              )}
            >
              <Radio size={12} aria-hidden />
              {stream.status === "connected"
                ? "disconnect ws"
                : stream.status === "connecting"
                  ? "connecting..."
                  : "connect ws"}
            </button>

            <button
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              className="rounded-[7px] border border-monolith-border bg-monolith-surface p-2 text-monolith-muted transition-colors hover:text-monolith-ink"
            >
              {theme === "dark" ? <Sun size={13} /> : <Moon size={13} />}
            </button>
          </div>
        </div>
      </header>

      {/* --------------------- Architectural White Metadata Strip --------------- */}
      <div className="border-b border-edge bg-surface">
        <div className="mx-auto flex max-w-[1480px] flex-wrap items-center justify-between gap-4 px-6 py-2.5 font-mono text-[10px]">
          <div className="flex flex-wrap items-center gap-x-7 gap-y-1.5">
            {[
              ["PROFILE", activeMeta.profile],
              ["HORIZON", `t + ${activeMeta.horizonS}s`],
              ["WARMUP", `${activeMeta.warmupSeconds}s`],
              [
                "CAPACITY C",
                usingStream
                  ? `${activeMeta.capacityRps} rps/pod`
                  : `${activeMeta.capacityRps} rps/pod (provisional)`,
              ],
              ["MAX PODS", String(activeMeta.maxPods)],
              ["CLOCK", latestObs ? clockHHMMSS(latestObs.t) : "—"],
            ].map(([k, v]) => (
              <div key={k} className="flex items-center gap-2">
                <span className="tracking-wider text-ink-tertiary">{k}</span>
                <span className="font-medium text-ink tnum">{v}</span>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-3">
            {!usingStream ? (
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => (playing ? stop() : play(0))}
                  className="flex items-center gap-1.5 rounded-[6px] border border-edge px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-ink-secondary transition-colors hover:border-ink hover:text-ink"
                >
                  {playing ? <Pause size={10} /> : <Play size={10} />}
                  {playing ? "pause" : "replay"}
                </button>
                <button
                  onClick={reset}
                  aria-label="Show full run"
                  className="rounded-[6px] border border-edge p-1 text-ink-secondary transition-colors hover:border-ink hover:text-ink"
                >
                  <RotateCcw size={11} />
                </button>
              </div>
            ) : null}
            <span
              className={cn(
                "tracking-wider",
                activeMeta.live ? "text-replicas" : "text-forecast",
              )}
            >
              {usingStream
                ? activeMeta.live
                  ? "LIVE CLUSTER STREAM · LIVE=TRUE"
                  : "WS REPLAY · LIVE=FALSE"
                : "SEEDED SIMULATION · LIVE=FALSE"}
            </span>
          </div>
        </div>
      </div>

      {/* ----------------------------- Main Stage ------------------------------- */}
      <main className="mx-auto max-w-[1480px] px-6 py-7">
        {/* ROW 1: Inverted Black Hero SLA Monolith + 3-Col Architectural Metric Tray */}
        <section
          data-stagger
          className="mb-6 grid grid-cols-1 gap-5 xl:grid-cols-[350px_minmax(0,1fr)]"
        >
          {/* Inverted Pitch-Black SLA Monolith */}
          <div className="monolith-card flex flex-col justify-between gap-4 px-6 py-5">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-monolith-muted">
                SLA violation rate
              </span>
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[9px] uppercase tracking-wider",
                  over
                    ? "border-critical/40 bg-critical/15 text-critical"
                    : "border-replicas/40 bg-replicas/15 text-replicas",
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    over ? "bg-critical" : "bg-replicas",
                  )}
                  aria-hidden
                />
                {over ? "breaching" : "< 2.0% target"}
              </span>
            </div>

            <div className="flex items-baseline gap-1.5">
              <span
                className={cn(
                  "display text-[52px] tnum",
                  over ? "text-critical" : "text-monolith-ink",
                )}
              >
                {rolled.toFixed(2)}
              </span>
              <span className="text-[20px] font-medium text-monolith-muted">
                %
              </span>
            </div>

            <div className="flex flex-col gap-2">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-monolith-border">
                <div
                  className={cn(
                    "h-full rounded-full transition-all duration-300",
                    over ? "bg-critical" : "bg-replicas",
                  )}
                  style={{ width: `${compliancePct}%` }}
                />
              </div>
              <div className="flex items-center justify-between font-mono text-[11px] text-monolith-muted tnum">
                <span>{compliancePct.toFixed(2)}% within {activeMeta.sloMs}ms</span>
                <span>Headroom {headroom.toFixed(2)}×</span>
              </div>
            </div>
          </div>

          {/* Floating Pure White Architectural 3-Column Metric Bar */}
          <MetricBar
            items={[
              {
                id: "demand",
                label: "Offered demand",
                value: latestObs ? latestObs.rps.toFixed(0) : "—",
                unit: "req/s",
                tone: "var(--color-demand)",
                spark: sparkOf((i) => visibleTelemetry[i]?.rps ?? 0),
                hint: `Capacity ${capacityNow.toFixed(0)} req/s · ${
                  latestForecast
                    ? `Upper +${activeMeta.horizonS}s: ${latestForecast.upper.toFixed(0)} rps`
                    : "no forecast yet"
                }`,
              },
              {
                id: "latency",
                label: "Client p99 latency",
                value: latestObs ? latestObs.p99.toFixed(1) : "—",
                unit: "ms",
                tone: breaching
                  ? "var(--color-critical)"
                  : "var(--color-forecast)",
                spark: sparkOf((i) => visibleTelemetry[i]?.p99 ?? 0),
                hint: `p50 ${latestObs ? latestObs.p50.toFixed(1) : "—"} ms · p90 ${latestObs ? latestObs.p90.toFixed(1) : "—"} ms · client-side k6`,
              },
              {
                id: "cpu",
                label: "CPU headroom",
                value: latestObs ? currentCpuPct.toFixed(0) : "—",
                unit: "% used",
                tone:
                  currentCpuPct > 85
                    ? "var(--color-critical)"
                    : "var(--color-replicas)",
                spark: sparkOf((i) => cpuPct(visibleTelemetry[i])),
                hint: `${latestObs ? (latestObs.cpuMillicores / 1000).toFixed(2) : "—"} cores · Reactive guard > 85%`,
              },
            ]}
          />
        </section>

        {/* ----------------------------- Tab Content ---------------------------- */}
        {activeTab === "overview" ? (
          <>
            {/* ROW 2: Floating Pure White Smooth Forecast Chart Stage */}
            <section data-stagger className="studio-card mb-6 px-6 py-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
                <div>
                  <h2 className="text-[16px] font-semibold tracking-tight text-ink">
                    Demand Forecast &amp; Lookahead Window
                  </h2>
                  <p className="text-[12px] text-ink-secondary">
                    Measured arrivals vs conformal 90% prediction cone extending
                    ahead to{" "}
                    <span className="font-mono font-medium text-ink">
                      t + {activeMeta.horizonS}s
                    </span>
                  </p>
                </div>
                <Legend
                  items={[
                    { label: "Measured RPS", color: "var(--color-demand)" },
                    {
                      label: "Forecast median",
                      color: "var(--color-forecast)",
                    },
                    {
                      label: "90% interval",
                      color: "var(--color-forecast)",
                      band: true,
                    },
                    {
                      label: "Capacity",
                      color: "var(--color-replicas)",
                      dashed: true,
                    },
                  ]}
                />
              </div>
              <DemandPlot
                rows={rows}
                domain={domain}
                nowT={latestObs?.t}
                horizonS={activeMeta.horizonS}
              />
            </section>

            {/* ROW 3: Pod Cluster Matrix | Latency Tail Spread | Arbiter Trail & Cost */}
            <section
              data-stagger
              className="grid grid-cols-1 gap-5 lg:grid-cols-3"
            >
              <div className="studio-card flex flex-col justify-between gap-4 px-5 py-5">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-[14px] font-semibold text-ink">
                    Pod Cluster Matrix
                  </h2>
                  <span className="font-mono text-[10px] uppercase tracking-wider text-ink-tertiary">
                    Kubernetes API
                  </span>
                </div>
                <PodMatrix
                  ready={latestObs?.readyReplicas ?? activeMeta.minPods}
                  desired={latestObs?.desiredReplicas ?? activeMeta.minPods}
                  maxPods={activeMeta.maxPods}
                  capacityRps={activeMeta.capacityRps}
                />
              </div>

              <div className="studio-card flex flex-col justify-between gap-3 px-5 py-5">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-[14px] font-semibold text-ink">
                    Latency Tail Spread (p50 -&gt; p99)
                  </h2>
                  <span className="font-mono text-[10px] uppercase tracking-wider text-forecast">
                    SLO {activeMeta.sloMs} ms
                  </span>
                </div>
                <LatencyPlot
                  rows={rows}
                  domain={domain}
                  sloMs={activeMeta.sloMs}
                />
                <p className="text-[11px] text-ink-secondary">
                  Client-side k6 timing envelope (avoids server coordinated
                  omission).
                </p>
              </div>

              <div className="studio-card flex flex-col justify-between gap-4 px-5 py-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-[14px] font-semibold text-ink">
                    Arbiter Trail &amp; Cost
                  </h2>
                  <button
                    onClick={() => setShowDollars((s) => !s)}
                    aria-pressed={showDollars}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-wider transition-colors",
                      showDollars
                        ? "border-monolith-border bg-monolith text-monolith-ink"
                        : "border-edge bg-surface-sunken text-ink-secondary hover:border-ink hover:text-ink",
                    )}
                  >
                    <DollarSign size={10} aria-hidden />
                    {compact(cost, 1)} pod-s · {showDollars ? "hide $" : "show $"}
                  </button>
                </div>

                <ArbiterTrailMini decisions={decisions} />

                {showDollars ? (
                  <div className="rounded-[8px] border border-forecast/30 bg-forecast/[0.06] px-3 py-2.5">
                    <div className="flex items-center justify-between font-mono text-[11px] font-semibold text-forecast tnum">
                      <span>DERIVED ESTIMATE</span>
                      <span>
                        ≈ $
                        {((cost / 3600) * ASSUMED_USD_PER_POD_HOUR).toFixed(3)}
                      </span>
                    </div>
                    <p className="mt-1 text-[10px] text-ink-secondary">
                      Assumes ${ASSUMED_USD_PER_POD_HOUR.toFixed(4)} / pod-hour.
                      Pod-seconds remains the primary D14 metric.
                    </p>
                  </div>
                ) : (
                  <div className="flex items-center justify-between border-t border-edge pt-3 font-mono text-[10px] text-ink-tertiary tnum">
                    <span>Mean {(cost / elapsedSeconds).toFixed(2)} pods</span>
                    <span>
                      Peak{" "}
                      {visibleTelemetry.length
                        ? Math.max(...visibleTelemetry.map((r) => r.readyReplicas))
                        : 0}{" "}
                      pods
                    </span>
                    <span>
                      {
                        decisions.filter(
                          (d) => d.toReplicas !== d.fromReplicas,
                        ).length
                      }{" "}
                      actuations
                    </span>
                  </div>
                )}
              </div>
            </section>
          </>
        ) : null}

        {activeTab === "pods" ? (
          <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <div className="studio-card px-6 py-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-[16px] font-semibold text-ink">
                    Cluster Pod Topology ({activeMeta.maxPods} Slots)
                  </h2>
                  <p className="text-[12px] text-ink-secondary">
                    Authoritative Kubernetes deployment status + readiness
                    warmup progress
                  </p>
                </div>
              </div>
              <PodMatrix
                ready={latestObs?.readyReplicas ?? activeMeta.minPods}
                desired={latestObs?.desiredReplicas ?? activeMeta.minPods}
                maxPods={activeMeta.maxPods}
                capacityRps={activeMeta.capacityRps}
                expanded
              />
            </div>

            <div className="studio-card flex flex-col justify-between gap-4 px-6 py-5">
              <div>
                <h2 className="text-[16px] font-semibold text-ink">
                  Replica Actuation Timeline
                </h2>
                <p className="text-[12px] text-ink-secondary">
                  Ready vs Desired pod count bounded cleanly by [
                  {activeMeta.minPods}, {activeMeta.maxPods}]
                </p>
              </div>
              <ReplicaPlot
                rows={rows}
                domain={domain}
                maxPods={activeMeta.maxPods}
                height={220}
              />
            </div>
          </section>
        ) : null}

        {activeTab === "audit" ? (
          <section className="studio-card overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-edge px-5 py-4">
              <div>
                <h2 className="text-[16px] font-semibold text-ink">
                  Hybrid Arbiter Decision Log
                </h2>
                <p className="text-[12px] text-ink-secondary">
                  Every control tick with capacity arithmetic and actuation mode
                </p>
              </div>
              <span className="font-mono text-[11px] text-ink-tertiary">
                C = {activeMeta.capacityRps} rps/pod
              </span>
            </div>
            <DecisionLog decisions={decisions} />
          </section>
        ) : null}

        {activeTab === "table" ? (
          <section className="studio-card overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-edge px-5 py-4">
              <div>
                <h2 className="text-[16px] font-semibold text-ink">
                  Raw Telemetry &amp; Horizon Table
                </h2>
                <p className="text-[12px] text-ink-secondary">
                  Accessible numeric export with zero colour encoding (includes{" "}
                  <span className="font-mono">t + {activeMeta.horizonS}s</span>{" "}
                  lead rows)
                </p>
              </div>
              <span className="font-mono text-[11px] text-ink-tertiary">
                {rows.length} rows
              </span>
            </div>
            <div className="max-h-[540px] overflow-auto">
              <table className="w-full border-collapse text-left font-mono text-[11px] tabular-nums">
                <thead className="sticky top-0 bg-surface">
                  <tr className="border-b border-edge">
                    {[
                      "time",
                      "demand",
                      "forecast",
                      "lower",
                      "upper",
                      "capacity",
                      "ready",
                      "p50",
                      "p99",
                    ].map((h) => (
                      <th
                        key={h}
                        scope="col"
                        className="px-4 py-2.5 text-[10px] font-medium uppercase tracking-wider text-ink-tertiary"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr
                      key={r.t}
                      className="border-b border-edge/50 hover:bg-surface-sunken"
                    >
                      <td className="px-4 py-1.5 text-ink-tertiary">
                        {clockHHMMSS(r.t)}
                      </td>
                      <td className="px-4 py-1.5 font-medium text-ink">
                        {r.rps != null ? r.rps.toFixed(1) : "—"}
                      </td>
                      <td className="px-4 py-1.5 text-ink">
                        {r.fPoint != null ? r.fPoint.toFixed(1) : "—"}
                      </td>
                      <td className="px-4 py-1.5 text-ink-secondary">
                        {r.fLower != null ? r.fLower.toFixed(1) : "—"}
                      </td>
                      <td className="px-4 py-1.5 text-ink-secondary">
                        {r.fUpper != null ? r.fUpper.toFixed(1) : "—"}
                      </td>
                      <td className="px-4 py-1.5 text-ink">
                        {r.capacity != null ? r.capacity.toFixed(0) : "—"}
                      </td>
                      <td className="px-4 py-1.5 text-ink">
                        {r.replicas != null ? r.replicas : "—"}
                      </td>
                      <td className="px-4 py-1.5 text-ink-secondary">
                        {r.p50 != null ? r.p50.toFixed(1) : "—"}
                      </td>
                      <td className="px-4 py-1.5 text-ink">
                        {r.p99 != null ? r.p99.toFixed(1) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        <footer className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-edge pt-4 font-mono text-[10px] uppercase tracking-wider text-ink-tertiary">
          <span>CloudPulse · Team A8-8 · Mohan Babu University</span>
          <span>
            Latency: Client-side k6 · Cost metric: pod-seconds (D14)
          </span>
        </footer>
      </main>
    </div>
  );
}
