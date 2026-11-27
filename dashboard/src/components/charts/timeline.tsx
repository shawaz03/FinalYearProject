"use client";

import * as React from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Row } from "@/lib/series";
import { niceTicks } from "@/lib/series";
import { clockHHMM, clockHHMMSS } from "@/lib/utils";

/**
 * Three aligned plots sharing one time axis.
 *
 * Demand (req/s), replicas (count) and latency (ms) are three different
 * scales. Putting any two of them on one plot would need a second y-axis,
 * which invents a correlation that is not in the data — the single most
 * common and most misleading chart mistake. Stacked small multiples on a
 * shared x domain give the same visual comparison honestly.
 *
 * `syncId` ties the crosshair together, so hovering anywhere reads all three
 * plots at the same instant.
 */

const SYNC = "cloudpulse-timeline";
const AXIS_FONT = 10;

/* -------------------------------------------------------------------------- */

interface TipRow {
  label: string;
  value: string;
  swatch?: string;
  dashed?: boolean;
}

function Tip({
  active,
  payload,
  label,
  rows,
}: {
  active?: boolean;
  payload?: unknown;
  label?: number;
  rows: (r: Row) => TipRow[];
}) {
  const p = payload as Array<{ payload: Row }> | undefined;
  if (!active || !p?.length) return null;
  const row = p[0].payload;
  return (
    <div className="pointer-events-none rounded-[var(--radius-chip)] border border-edge-strong bg-surface px-2.5 py-2 shadow-sm">
      <div className="mb-1.5 font-mono text-[10px] tabular-nums text-ink-tertiary">
        {clockHHMMSS(label ?? row.t)}
      </div>
      <div className="flex flex-col gap-1">
        {rows(row).map((r) => (
          <div key={r.label} className="flex items-center gap-2 text-[11px]">
            {r.swatch ? (
              <span
                aria-hidden
                className="h-0.5 w-3 shrink-0 rounded-full"
                style={{
                  background: r.dashed
                    ? `repeating-linear-gradient(90deg, ${r.swatch} 0 3px, transparent 3px 6px)`
                    : r.swatch,
                }}
              />
            ) : (
              <span className="w-3" />
            )}
            <span className="text-ink-secondary">{r.label}</span>
            <span className="ml-auto font-mono tabular-nums text-ink">
              {r.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function axisProps(domain: [number, number]) {
  return {
    type: "number" as const,
    dataKey: "t",
    domain,
    scale: "time" as const,
    tickFormatter: clockHHMM,
    stroke: "var(--axis)",
    strokeWidth: 1,
    tick: { fill: "var(--ink-tertiary)", fontSize: AXIS_FONT },
    tickLine: false,
    minTickGap: 44,
  };
}

const yAxisBase = {
  stroke: "var(--axis)",
  strokeWidth: 1,
  tick: { fill: "var(--ink-tertiary)", fontSize: AXIS_FONT },
  tickLine: false,
  axisLine: false,
  width: 38,
};

const grid = (
  <CartesianGrid
    vertical={false}
    stroke="var(--grid)"
    strokeWidth={1}
    /* solid hairlines — dashed grid reads as "threshold" when it is just a grid */
  />
);

/* ---------------------------------- demand -------------------------------- */

export function DemandPlot({
  rows,
  domain,
  height = 210,
}: {
  rows: Row[];
  domain: [number, number];
  height?: number;
}) {
  const max = Math.max(
    10,
    ...rows.map((r) => Math.max(r.rps ?? 0, r.fUpper ?? 0, r.capacity ?? 0)),
  );
  const ticks = niceTicks(max * 1.08);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={rows}
        syncId={SYNC}
        margin={{ top: 8, right: 56, bottom: 4, left: 0 }}
      >
        {grid}
        <XAxis {...axisProps(domain)} />
        <YAxis {...yAxisBase} domain={[0, ticks[ticks.length - 1]]} ticks={ticks} />

        {/* conformal interval: a 10% wash, never a saturated block */}
        <Area
          dataKey="fRange"
          stroke="none"
          fill="var(--color-forecast)"
          fillOpacity={0.1}
          isAnimationActive={false}
          connectNulls={false}
        />
        {/* capacity the running pods can actually serve */}
        <Line
          dataKey="capacity"
          stroke="var(--color-replicas)"
          strokeWidth={1.5}
          strokeDasharray="3 3"
          dot={false}
          isAnimationActive={false}
        />
        <Line
          dataKey="fPoint"
          stroke="var(--color-forecast)"
          strokeWidth={2}
          strokeLinecap="round"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          dataKey="rps"
          stroke="var(--color-demand)"
          strokeWidth={2}
          strokeLinecap="round"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Tooltip
          cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
          content={
            <Tip
              rows={(r) => [
                {
                  label: "Measured",
                  value: `${r.rps?.toFixed(1) ?? "—"} req/s`,
                  swatch: "var(--color-demand)",
                },
                {
                  label: "Forecast",
                  value: r.fPoint != null ? `${r.fPoint.toFixed(1)} req/s` : "—",
                  swatch: "var(--color-forecast)",
                },
                {
                  label: "90% interval",
                  value:
                    r.fLower != null && r.fUpper != null
                      ? `${r.fLower.toFixed(0)} – ${r.fUpper.toFixed(0)}`
                      : "—",
                },
                {
                  label: "Capacity",
                  value: `${r.capacity?.toFixed(0) ?? "—"} req/s`,
                  swatch: "var(--color-replicas)",
                  dashed: true,
                },
              ]}
            />
          }
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* --------------------------------- replicas ------------------------------- */

export function ReplicaPlot({
  rows,
  domain,
  maxPods,
  height = 120,
}: {
  rows: Row[];
  domain: [number, number];
  maxPods: number;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={rows}
        syncId={SYNC}
        margin={{ top: 8, right: 56, bottom: 4, left: 0 }}
      >
        {grid}
        <XAxis {...axisProps(domain)} />
        <YAxis
          {...yAxisBase}
          domain={[0, maxPods]}
          ticks={niceTicks(maxPods, 3)}
          allowDecimals={false}
        />
        <ReferenceLine
          y={maxPods}
          stroke="var(--color-critical)"
          strokeWidth={1}
          strokeDasharray="2 3"
          label={{
            value: "MAX_PODS",
            position: "right",
            fill: "var(--ink-tertiary)",
            fontSize: 9,
          }}
        />
        <Area
          dataKey="replicas"
          type="stepAfter"
          stroke="var(--color-replicas)"
          strokeWidth={2}
          fill="var(--color-replicas)"
          fillOpacity={0.1}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          dataKey="desired"
          type="stepAfter"
          stroke="var(--color-replicas)"
          strokeWidth={1}
          strokeDasharray="2 2"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Tooltip
          cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
          content={
            <Tip
              rows={(r) => [
                {
                  label: "Ready",
                  value: String(r.replicas ?? "—"),
                  swatch: "var(--color-replicas)",
                },
                {
                  label: "Requested",
                  value: String(r.desired ?? "—"),
                  swatch: "var(--color-replicas)",
                  dashed: true,
                },
              ]}
            />
          }
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* --------------------------------- latency -------------------------------- */

export function LatencyPlot({
  rows,
  domain,
  sloMs,
  height = 130,
}: {
  rows: Row[];
  domain: [number, number];
  sloMs: number;
  height?: number;
}) {
  const max = Math.max(sloMs * 1.6, ...rows.map((r) => r.p99 ?? 0));
  const ticks = niceTicks(max);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={rows}
        syncId={SYNC}
        margin={{ top: 8, right: 56, bottom: 4, left: 0 }}
      >
        {grid}
        <XAxis {...axisProps(domain)} />
        <YAxis {...yAxisBase} domain={[0, ticks[ticks.length - 1]]} ticks={ticks} />
        <ReferenceLine
          y={sloMs}
          stroke="var(--color-critical)"
          strokeWidth={1.5}
          label={{
            value: `SLO ${sloMs}ms`,
            position: "right",
            fill: "var(--color-critical)",
            fontSize: 9,
          }}
        />
        <Line
          dataKey="p99"
          stroke="var(--color-demand)"
          strokeWidth={2}
          strokeLinecap="round"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Tooltip
          cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
          content={
            <Tip
              rows={(r) => [
                {
                  label: "p99",
                  value: `${r.p99?.toFixed(1) ?? "—"} ms`,
                  swatch: "var(--color-demand)",
                },
                {
                  label: "SLO",
                  value: `${sloMs} ms`,
                  swatch: "var(--color-critical)",
                },
              ]}
            />
          }
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* --------------------------------- legend --------------------------------- */

export function Legend({
  items,
}: {
  items: { label: string; color: string; dashed?: boolean; band?: boolean }[];
}) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          {i.band ? (
            <span
              aria-hidden
              className="h-2.5 w-3.5 shrink-0 rounded-[1px] border"
              style={{
                borderColor: `color-mix(in srgb, ${i.color} 45%, transparent)`,
                background: `color-mix(in srgb, ${i.color} 18%, transparent)`,
              }}
            />
          ) : (
            <span
              aria-hidden
              className="h-0.5 w-3.5 shrink-0 rounded-full"
              style={{
                background: i.dashed
                  ? `repeating-linear-gradient(90deg, ${i.color} 0 3px, transparent 3px 6px)`
                  : i.color,
              }}
            />
          )}
          {/* text never wears the data colour — the mark beside it carries identity */}
          <span className="text-[11px] text-ink-secondary">{i.label}</span>
        </li>
      ))}
    </ul>
  );
}
