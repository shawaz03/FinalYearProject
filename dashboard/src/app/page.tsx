"use client";

import * as React from "react";
import gsap from "gsap";
import {
  Activity,
  Moon,
  Pause,
  Play,
  RotateCcw,
  Sun,
  Table2,
  TriangleAlert,
} from "lucide-react";

import { Panel, PanelHeader } from "@/components/ui/panel";
import { Badge } from "@/components/ui/badge";
import { StatTile } from "@/components/stat-tile";
import { DecisionLog } from "@/components/decision-log";
import {
  DemandPlot,
  LatencyPlot,
  Legend,
  ReplicaPlot,
} from "@/components/charts/timeline";
import { mergeSeries } from "@/lib/series";
import { RUN, podSeconds, simulateRun } from "@/lib/telemetry";
import { clockHHMMSS, cn, compact } from "@/lib/utils";

/* ----------------------------- replay playhead ---------------------------- */

/**
 * The run replays rather than appearing all at once.
 *
 * This is the only animation in the interface that touches data, and it earns
 * its place: watching the forecast band advance ahead of the measured line is
 * the single clearest demonstration of what the system does. GSAP drives a
 * plain numeric proxy, so the tween is pausable and scrubbable.
 */
function useReplay(total: number) {
  const [head, setHead] = React.useState(total);
  const [playing, setPlaying] = React.useState(false);
  const proxy = React.useRef({ v: total });
  const tween = React.useRef<gsap.core.Tween | null>(null);

  const stop = React.useCallback(() => {
    tween.current?.kill();
    tween.current = null;
    setPlaying(false);
  }, []);

  const play = React.useCallback(
    (from = 0) => {
      tween.current?.kill();
      proxy.current.v = from;
      setHead(from);
      setPlaying(true);
      tween.current = gsap.to(proxy.current, {
        v: total,
        duration: Math.max(6, total * 0.06),
        ease: "none",
        onUpdate: () => setHead(Math.round(proxy.current.v)),
        onComplete: () => setPlaying(false),
      });
    },
    [total],
  );

  React.useEffect(() => () => void tween.current?.kill(), []);

  return { head, playing, play, stop, setHead };
}

/* --------------------------------- page ----------------------------------- */

export default function Page() {
  const run = React.useMemo(() => simulateRun(), []);
  const allRows = React.useMemo(
    () => mergeSeries(run.telemetry, run.forecasts, RUN.capacityRps),
    [run],
  );

  const { head, playing, play, stop, setHead } = useReplay(allRows.length);
  const [showTable, setShowTable] = React.useState(false);
  const [theme, setTheme] = React.useState<"light" | "dark">("dark");

  React.useEffect(() => {
    const stored = localStorage.getItem("cp-theme") as "light" | "dark" | null;
    const initial =
      stored ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light");
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("cp-theme", next);
  };

  const rows = allRows.slice(0, Math.max(2, head));
  const domain: [number, number] = [allRows[0].t, allRows[allRows.length - 1].t];
  const latest = rows[rows.length - 1];
  const decisions = run.decisions.filter((d) => d.t <= latest.t);

  // Computed from the authoritative telemetry frames, not the merged chart
  // rows, so the cost metric never depends on display-side interpolation.
  const cost = podSeconds(run.telemetry.slice(0, rows.length));
  const violation = run.telemetry[rows.length - 1]?.slaViolationRate ?? 0;
  const breaching = latest.p99 != null && latest.p99 > RUN.sloMs;
  const headroom =
    latest.capacity && latest.rps ? latest.capacity / latest.rps : 0;

  const sparkOf = (pick: (i: number) => number) => {
    const n = Math.min(12, rows.length);
    return Array.from({ length: n }, (_, k) =>
      pick(rows.length - n + k),
    );
  };

  return (
    <div className="min-h-screen bg-plane">
      {/* ------------------------------ top bar ------------------------------ */}
      <header className="sticky top-0 z-30 border-b border-edge bg-plane/85 backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-5 gap-y-2 px-6 py-3">
          <div className="flex items-center gap-2.5">
            <Activity size={16} className="text-demand" aria-hidden />
            <span className="text-[13px] font-semibold tracking-[-0.01em]">
              CloudPulse
            </span>
            <span className="text-[11px] text-ink-tertiary">control plane</span>
          </div>

          <div className="hidden h-4 w-px bg-edge-strong sm:block" />

          <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[10px] uppercase tracking-wider text-ink-tertiary">
            {[
              ["run", RUN.runId],
              ["scaler", RUN.scaler],
              ["profile", RUN.profile],
              ["H", `${RUN.horizonS}s`],
              ["warmup", `${RUN.warmupSeconds}s`],
            ].map(([k, v]) => (
              <div key={k} className="flex items-center gap-1.5">
                <dt>{k}</dt>
                <dd className="text-ink-secondary">{v}</dd>
              </div>
            ))}
          </dl>

          <div className="ml-auto flex items-center gap-2">
            <Badge tone="warning" title="No arbiter is connected yet">
              <TriangleAlert size={10} aria-hidden />
              simulated data
            </Badge>
            <button
              onClick={() => setShowTable((s) => !s)}
              aria-pressed={showTable}
              className={cn(
                "flex items-center gap-1.5 rounded-[var(--radius-chip)] border px-2 py-1 text-[10px] uppercase tracking-wider transition-colors",
                showTable
                  ? "border-edge-strong bg-surface text-ink"
                  : "border-edge text-ink-secondary hover:text-ink",
              )}
            >
              <Table2 size={11} aria-hidden /> table
            </button>
            <button
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              className="rounded-[var(--radius-chip)] border border-edge p-1.5 text-ink-secondary transition-colors hover:text-ink"
            >
              {theme === "dark" ? <Sun size={12} /> : <Moon size={12} />}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-6 py-6">
        {/* ------------------------- hero + KPI row ------------------------- */}
        <section className="mb-5 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1.15fr)_repeat(3,minmax(0,1fr))]">
          {/* exactly one hero figure per view */}
          <div className="flex flex-col justify-between rounded-[var(--radius-panel)] border border-edge bg-surface px-5 py-4">
            <div className="flex items-start justify-between gap-3">
              <span className="text-[11px] text-ink-secondary">
                SLA violations
                <span className="block text-ink-tertiary">
                  requests slower than {RUN.sloMs} ms
                </span>
              </span>
              <span className="flex items-center gap-1.5 whitespace-nowrap">
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 rounded-full"
                  style={{
                    background:
                      violation > 0.02
                        ? "var(--color-critical)"
                        : "var(--color-good)",
                  }}
                />
                <span className="font-mono text-[9px] uppercase tracking-wider text-ink-secondary">
                  {violation > 0.02 ? "breaching" : "within target"}
                </span>
              </span>
            </div>
            <div className="mt-4 flex items-baseline gap-1.5">
              <span className="text-[52px] font-semibold leading-none tracking-[-0.03em]">
                {(violation * 100).toFixed(2)}
              </span>
              <span className="text-[18px] text-ink-tertiary">%</span>
            </div>
            <p className="mt-3 font-mono text-[10px] leading-relaxed text-ink-tertiary">
              target &lt; 2.00% · cumulative over the run
            </p>
          </div>

          <StatTile
            label="Demand"
            value={latest.rps?.toFixed(0) ?? "—"}
            unit="req/s"
            tone="var(--color-demand)"
            spark={sparkOf((i) => rows[i].rps ?? 0)}
            hint={`capacity ${latest.capacity?.toFixed(0)} req/s · ${headroom.toFixed(2)}× headroom`}
          />
          <StatTile
            label="Replicas ready"
            value={String(latest.replicas ?? "—")}
            unit={`of ${RUN.maxPods}`}
            tone="var(--color-replicas)"
            spark={sparkOf((i) => rows[i].replicas ?? 0)}
            hint={`requested ${latest.desired} · C = ${RUN.capacityRps} req/s per pod`}
            state={
              latest.desired !== latest.replicas
                ? { text: "warming", color: "var(--color-warning)" }
                : undefined
            }
          />
          <StatTile
            label="p99 latency"
            value={latest.p99?.toFixed(0) ?? "—"}
            unit="ms"
            tone={breaching ? "var(--color-critical)" : "var(--ink)"}
            spark={sparkOf((i) => rows[i].p99 ?? 0)}
            hint={`p50 ${run.telemetry[rows.length - 1]?.p50.toFixed(0)} ms · client-side`}
            state={
              breaching
                ? { text: "over SLO", color: "var(--color-critical)" }
                : { text: "nominal", color: "var(--color-good)" }
            }
          />
        </section>

        {/* ---------------------------- timeline ---------------------------- */}
        <Panel className="mb-5">
          <PanelHeader title="Timeline" unit="shared time axis">
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => (playing ? stop() : play(playing ? head : 0))}
                className="flex items-center gap-1.5 rounded-[var(--radius-chip)] border border-edge px-2 py-1 text-[10px] uppercase tracking-wider text-ink-secondary transition-colors hover:text-ink"
              >
                {playing ? <Pause size={11} /> : <Play size={11} />}
                {playing ? "pause" : "replay"}
              </button>
              <button
                onClick={() => {
                  stop();
                  setHead(allRows.length);
                }}
                aria-label="Show the whole run"
                className="rounded-[var(--radius-chip)] border border-edge p-1.5 text-ink-secondary transition-colors hover:text-ink"
              >
                <RotateCcw size={11} />
              </button>
            </div>
          </PanelHeader>

          <div className="px-4 pb-2 pt-3">
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[11px] font-medium">
                Demand{" "}
                <span className="text-ink-tertiary">
                  — measured vs forecast at t+{RUN.horizonS}s
                </span>
              </span>
              <Legend
                items={[
                  { label: "Measured", color: "var(--color-demand)" },
                  { label: "Forecast", color: "var(--color-forecast)" },
                  { label: "90% interval", color: "var(--color-forecast)" },
                  {
                    label: "Capacity",
                    color: "var(--color-replicas)",
                    dashed: true,
                  },
                ]}
              />
            </div>
            <DemandPlot rows={rows} domain={domain} />
          </div>

          <div className="border-t border-edge px-4 pb-2 pt-3">
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[11px] font-medium">
                Replicas <span className="text-ink-tertiary">— pods</span>
              </span>
              <Legend
                items={[
                  { label: "Ready", color: "var(--color-replicas)" },
                  {
                    label: "Requested",
                    color: "var(--color-replicas)",
                    dashed: true,
                  },
                ]}
              />
            </div>
            <ReplicaPlot rows={rows} domain={domain} maxPods={RUN.maxPods} />
          </div>

          <div className="border-t border-edge px-4 pb-3 pt-3">
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[11px] font-medium">
                Latency{" "}
                <span className="text-ink-tertiary">— p99, client-side, ms</span>
              </span>
            </div>
            <LatencyPlot rows={rows} domain={domain} sloMs={RUN.sloMs} />
          </div>
        </Panel>

        {/* ------------------------ decisions + cost ------------------------ */}
        <section className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel>
            <PanelHeader title="Decisions" unit="hybrid arbiter" />
            <DecisionLog decisions={decisions} />
          </Panel>

          <Panel>
            <PanelHeader title="Cost" unit="pod-seconds" />
            <div className="flex flex-col gap-4 px-4 py-4">
              <div>
                <div className="flex items-baseline gap-1.5">
                  <span className="text-[32px] font-semibold leading-none tracking-[-0.02em]">
                    {compact(cost, 1)}
                  </span>
                  <span className="text-[12px] text-ink-tertiary">
                    pod-seconds
                  </span>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-ink-secondary">
                  The integral of replicas over time. This is the primary cost
                  metric — a local cluster has no dollar cost, so a currency
                  figure here would be fiction.
                </p>
              </div>

              <div className="rounded-[var(--radius-chip)] border border-edge bg-surface-sunken px-3 py-2.5">
                <div className="mb-1 flex items-center gap-1.5">
                  <TriangleAlert
                    size={11}
                    className="text-warning"
                    aria-hidden
                  />
                  <span className="font-mono text-[9px] uppercase tracking-wider text-ink-secondary">
                    if priced
                  </span>
                </div>
                <p className="font-mono text-[11px] tabular-nums text-ink">
                  ≈ ${((cost / 3600) * 0.0425).toFixed(3)}
                </p>
                <p className="mt-1 text-[10px] leading-relaxed text-ink-tertiary">
                  Assumes $0.0425 / pod-hour (one 0.5 vCPU / 128 Mi pod at a
                  published on-demand rate). The assumption is printed because
                  an unlabelled cost figure is not a measurement.
                </p>
              </div>

              <dl className="flex flex-col gap-1.5 border-t border-edge pt-3 font-mono text-[11px] tabular-nums">
                {[
                  ["mean replicas", (cost / (rows.length * 5)).toFixed(2)],
                  ["peak replicas", String(Math.max(...rows.map((r) => r.replicas ?? 0)))],
                  ["actuations", String(decisions.filter((d) => d.toReplicas !== d.fromReplicas).length)],
                  ["elapsed", clockHHMMSS(latest.t)],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3">
                    <dt className="text-ink-tertiary">{k}</dt>
                    <dd className="text-ink-secondary">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </Panel>
        </section>

        {/* --------------------------- table view --------------------------- */}
        {showTable ? (
          <Panel className="mt-5">
            <PanelHeader title="Table view" unit="every value, no colour" />
            <div className="max-h-[420px] overflow-auto">
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
                      "p99",
                    ].map((h) => (
                      <th
                        key={h}
                        scope="col"
                        className="px-3 py-2 text-[10px] uppercase tracking-wider text-ink-tertiary"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.t} className="border-b border-edge/50">
                      <td className="px-3 py-1 text-ink-tertiary">
                        {clockHHMMSS(r.t)}
                      </td>
                      <td className="px-3 py-1">{r.rps?.toFixed(1)}</td>
                      <td className="px-3 py-1">{r.fPoint?.toFixed(1) ?? "—"}</td>
                      <td className="px-3 py-1 text-ink-secondary">
                        {r.fLower?.toFixed(1) ?? "—"}
                      </td>
                      <td className="px-3 py-1 text-ink-secondary">
                        {r.fUpper?.toFixed(1) ?? "—"}
                      </td>
                      <td className="px-3 py-1 text-ink-secondary">
                        {r.capacity?.toFixed(0)}
                      </td>
                      <td className="px-3 py-1">{r.replicas}</td>
                      <td className="px-3 py-1">{r.p99?.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        ) : null}

        <footer className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-edge pt-4 font-mono text-[10px] text-ink-tertiary">
          <span>Team A8-8 · Mohan Babu University</span>
          <span className="text-ink-tertiary/60">·</span>
          <span>
            C = {RUN.capacityRps} req/s per pod (provisional — measured in Phase 2)
          </span>
          <span className="text-ink-tertiary/60">·</span>
          <span>latency is client-side</span>
        </footer>
      </main>
    </div>
  );
}
