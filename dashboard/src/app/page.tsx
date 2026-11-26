"use client";

import * as React from "react";
import gsap from "gsap";
import {
  Moon,
  Pause,
  Play,
  RotateCcw,
  Sun,
  Table2,
  TriangleAlert,
} from "lucide-react";

import { Panel, PanelHeader, Readout } from "@/components/ui/panel";
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
        duration: Math.max(6, total * 0.055),
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

/** Rolls a figure to its new value. Only used on the hero — a dashboard where
 *  every number animates is unreadable. */
function useRoll(value: number) {
  const [shown, setShown] = React.useState(value);
  const proxy = React.useRef({ v: value });
  React.useEffect(() => {
    const t = gsap.to(proxy.current, {
      v: value,
      duration: 0.5,
      ease: "power2.out",
      onUpdate: () => setShown(proxy.current.v),
    });
    return () => void t.kill();
  }, [value]);
  return shown;
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
  const [theme, setTheme] = React.useState<"light" | "dark">("light");
  const stage = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const stored = localStorage.getItem("cp-theme") as "light" | "dark" | null;
    const initial = stored ?? "light";
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);
  }, []);

  /* Entrance: plates settle in sequence, like a rack powering up. Runs once,
     is short, and is skipped entirely under prefers-reduced-motion. */
  React.useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = gsap.context(() => {
      gsap.from("[data-stagger]", {
        opacity: 0,
        y: 10,
        duration: 0.5,
        ease: "power2.out",
        stagger: 0.06,
      });
    }, stage);
    return () => ctx.revert();
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

  const cost = podSeconds(run.telemetry.slice(0, rows.length));
  const violation = run.telemetry[rows.length - 1]?.slaViolationRate ?? 0;
  const rolled = useRoll(violation * 100);
  const breaching = latest.p99 != null && latest.p99 > RUN.sloMs;
  const headroom =
    latest.capacity && latest.rps ? latest.capacity / latest.rps : 0;
  const over = violation > 0.02;

  const sparkOf = (pick: (i: number) => number) => {
    const n = Math.min(14, rows.length);
    return Array.from({ length: n }, (_, k) => pick(rows.length - n + k));
  };

  return (
    <div ref={stage} className="relative min-h-screen">
      {/* material layers */}
      <div className="plane-grid pointer-events-none fixed inset-0 z-0" aria-hidden />
      <div className="grain pointer-events-none fixed inset-0 z-0" aria-hidden />

      {/* ------------------------------ masthead ----------------------------- */}
      <header className="relative z-20 border-b border-edge-strong bg-plane/80 backdrop-blur-md">
        <div className="mx-auto max-w-[1560px] px-6">
          <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 py-4">
            <div className="flex items-end gap-4">
              <div>
                <div className="eyebrow mb-1.5">Team A8-8 · Capstone</div>
                <h1 className="display text-[30px]">
                  Cloud<span className="text-demand">Pulse</span>
                </h1>
              </div>
              <div className="mb-1 hidden h-7 w-px bg-edge-strong sm:block" />
              <p className="mb-0.5 hidden max-w-[22ch] text-[11px] leading-snug text-ink-secondary sm:block">
                Load-aware predictive auto-scaling.
                <br />
                Conformal forecasts, hybrid arbitration.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <Badge tone="warning" title="No arbiter is connected yet">
                <TriangleAlert size={10} aria-hidden />
                simulated
              </Badge>
              <button
                onClick={() => setShowTable((s) => !s)}
                aria-pressed={showTable}
                className={cn(
                  "flex items-center gap-1.5 rounded-[var(--radius-chip)] border px-2 py-1 text-[10px] uppercase tracking-wider transition-colors",
                  showTable
                    ? "border-edge-strong bg-surface-raised text-ink"
                    : "border-edge text-ink-secondary hover:border-edge-strong hover:text-ink",
                )}
              >
                <Table2 size={11} aria-hidden /> table
              </button>
              <button
                onClick={toggleTheme}
                aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
                className="rounded-[var(--radius-chip)] border border-edge p-1.5 text-ink-secondary transition-colors hover:border-edge-strong hover:text-ink"
              >
                {theme === "dark" ? <Sun size={12} /> : <Moon size={12} />}
              </button>
            </div>
          </div>

          {/* instrument rail */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-edge py-2">
            <span className="flex items-center gap-1.5">
              <span
                className="live-dot h-1.5 w-1.5 rounded-full bg-demand"
                aria-hidden
              />
              <span className="eyebrow">replaying</span>
            </span>
            <Readout k="run" v={RUN.runId} />
            <Readout k="scaler" v={RUN.scaler} />
            <Readout k="profile" v={RUN.profile} />
            <Readout k="horizon" v={`${RUN.horizonS}s`} />
            <Readout k="warmup" v={`${RUN.warmupSeconds}s`} />
            <Readout k="C" v={`${RUN.capacityRps} rps/pod`} />
            <Readout k="clock" v={clockHHMMSS(latest.t)} />
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto max-w-[1560px] px-6 py-7">
        {/* --------------------------- hero + tiles -------------------------- */}
        <section className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.45fr)_repeat(3,minmax(0,1fr))]">
          <Panel
            data-stagger
            className="relative flex flex-col justify-between overflow-hidden px-6 py-5"
          >
            {/* a single hairline accent on the live edge of the plate */}
            <span
              aria-hidden
              className="absolute inset-y-0 left-0 w-[2px]"
              style={{
                background: over
                  ? "var(--color-critical)"
                  : "var(--color-replicas)",
              }}
            />
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="eyebrow mb-1">Headline metric</div>
                <div className="text-[12px] text-ink-secondary">
                  SLA violations — requests slower than {RUN.sloMs} ms
                </div>
              </div>
              <span className="flex shrink-0 items-center gap-1.5">
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 rounded-full"
                  style={{
                    background: over
                      ? "var(--color-critical)"
                      : "var(--color-good)",
                  }}
                />
                <span className="eyebrow">
                  {over ? "breaching" : "within target"}
                </span>
              </span>
            </div>

            <div className="my-5 flex items-end gap-2">
              <span
                className="display text-[clamp(56px,8vw,96px)] tnum"
                style={{ color: over ? "var(--color-critical)" : undefined }}
              >
                {rolled.toFixed(2)}
              </span>
              <span className="mb-2 text-[20px] text-ink-tertiary">%</span>
            </div>

            <div className="flex items-center gap-4 border-t border-edge pt-3">
              <Readout k="target" v="< 2.00%" />
              <Readout k="window" v="cumulative" />
              <Readout
                k="headroom"
                v={`${headroom.toFixed(2)}×`}
                tone={
                  headroom < 1
                    ? "var(--color-critical)"
                    : "var(--ink-secondary)"
                }
              />
            </div>
          </Panel>

          <StatTile
            data-stagger
            label="Demand"
            value={latest.rps?.toFixed(0) ?? "—"}
            unit="req/s"
            tone="var(--color-demand)"
            spark={sparkOf((i) => rows[i].rps ?? 0)}
            hint={`capacity ${latest.capacity?.toFixed(0)} req/s`}
          />
          <StatTile
            data-stagger
            label="Replicas ready"
            value={String(latest.replicas ?? "—")}
            unit={`/ ${RUN.maxPods}`}
            tone="var(--color-replicas)"
            spark={sparkOf((i) => rows[i].replicas ?? 0)}
            hint={`requested ${latest.desired} · C = ${RUN.capacityRps}`}
            state={
              latest.desired !== latest.replicas
                ? { text: "warming", color: "var(--color-warning)" }
                : undefined
            }
          />
          <StatTile
            data-stagger
            label="p99 latency"
            value={latest.p99?.toFixed(0) ?? "—"}
            unit="ms"
            tone={breaching ? "var(--color-critical)" : "var(--ink)"}
            spark={sparkOf((i) => rows[i].p99 ?? 0)}
            hint={`p50 ${run.telemetry[rows.length - 1]?.p50.toFixed(0)} ms · client-side`}
            state={
              breaching
                ? { text: "over slo", color: "var(--color-critical)" }
                : { text: "nominal", color: "var(--color-good)" }
            }
          />
        </section>

        {/* ---------------------------- timeline ---------------------------- */}
        <Panel data-stagger className="mb-6">
          <PanelHeader index="01" title="Timeline" unit="shared time axis">
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => (playing ? stop() : play(0))}
                className="flex items-center gap-1.5 rounded-[var(--radius-chip)] border border-edge px-2 py-1 text-[10px] uppercase tracking-wider text-ink-secondary transition-colors hover:border-edge-strong hover:text-ink"
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
                className="rounded-[var(--radius-chip)] border border-edge p-1.5 text-ink-secondary transition-colors hover:border-edge-strong hover:text-ink"
              >
                <RotateCcw size={11} />
              </button>
            </div>
          </PanelHeader>

          {[
            {
              title: "Demand",
              sub: `measured vs forecast at t+${RUN.horizonS}s`,
              legend: [
                { label: "Measured", color: "var(--color-demand)" },
                { label: "Forecast", color: "var(--color-forecast)" },
                { label: "90% interval", color: "var(--color-forecast)" },
                {
                  label: "Capacity",
                  color: "var(--color-replicas)",
                  dashed: true,
                },
              ],
              plot: <DemandPlot rows={rows} domain={domain} />,
            },
            {
              title: "Replicas",
              sub: "pods",
              legend: [
                { label: "Ready", color: "var(--color-replicas)" },
                {
                  label: "Requested",
                  color: "var(--color-replicas)",
                  dashed: true,
                },
              ],
              plot: (
                <ReplicaPlot
                  rows={rows}
                  domain={domain}
                  maxPods={RUN.maxPods}
                />
              ),
            },
            {
              title: "Latency",
              sub: "p99, client-side, ms",
              legend: [],
              plot: (
                <LatencyPlot rows={rows} domain={domain} sloMs={RUN.sloMs} />
              ),
            },
          ].map((band, i) => (
            <div
              key={band.title}
              className={cn("px-4 pb-2 pt-3", i > 0 && "border-t border-edge")}
            >
              <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[11px] font-medium">
                  {band.title}{" "}
                  <span className="text-ink-tertiary">— {band.sub}</span>
                </span>
                {band.legend.length ? <Legend items={band.legend} /> : null}
              </div>
              {band.plot}
            </div>
          ))}
        </Panel>

        {/* ------------------------ decisions + cost ------------------------ */}
        <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel data-stagger>
            <PanelHeader index="02" title="Decisions" unit="hybrid arbiter" />
            <DecisionLog decisions={decisions} />
          </Panel>

          <Panel data-stagger>
            <PanelHeader index="03" title="Cost" unit="pod-seconds" />
            <div className="flex flex-col gap-4 px-4 py-4">
              <div>
                <div className="flex items-baseline gap-1.5">
                  <span className="display text-[38px]">
                    {compact(cost, 1)}
                  </span>
                  <span className="text-[12px] text-ink-tertiary">
                    pod-seconds
                  </span>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-ink-secondary">
                  The integral of replicas over time. A local cluster has no
                  dollar cost, so a currency figure here would be fiction.
                </p>
              </div>

              <div className="rounded-[var(--radius-chip)] border border-edge bg-surface-sunken px-3 py-2.5">
                <div className="mb-1 flex items-center gap-1.5">
                  <TriangleAlert size={11} className="text-warning" aria-hidden />
                  <span className="eyebrow">if priced</span>
                </div>
                <p className="font-mono text-[12px] tabular-nums">
                  ≈ ${((cost / 3600) * 0.0425).toFixed(3)}
                </p>
                <p className="mt-1 text-[10px] leading-relaxed text-ink-tertiary">
                  Assumes $0.0425 / pod-hour. The assumption is printed because
                  an unlabelled cost figure is not a measurement.
                </p>
              </div>

              <dl className="flex flex-col gap-1.5 border-t border-edge pt-3">
                {[
                  ["mean replicas", (cost / (rows.length * 5)).toFixed(2)],
                  [
                    "peak replicas",
                    String(Math.max(...rows.map((r) => r.replicas ?? 0))),
                  ],
                  [
                    "actuations",
                    String(
                      decisions.filter((d) => d.toReplicas !== d.fromReplicas)
                        .length,
                    ),
                  ],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3">
                    <dt className="eyebrow">{k}</dt>
                    <dd className="font-mono text-[11px] tabular-nums text-ink-secondary">
                      {v}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </Panel>
        </section>

        {/* --------------------------- table view --------------------------- */}
        {showTable ? (
          <Panel className="mt-6">
            <PanelHeader index="04" title="Table view" unit="no colour encoding" />
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

        <footer className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-edge pt-4">
          <span className="eyebrow">Mohan Babu University</span>
          <Readout k="C" v={`${RUN.capacityRps} req/s per pod — provisional`} />
          <Readout k="latency" v="client-side" />
        </footer>
      </main>
    </div>
  );
}
