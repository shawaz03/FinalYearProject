"use client";

import * as React from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Row } from "@/lib/series";
import { niceTicks } from "@/lib/series";
import { clockHHMM, clockHHMMSS } from "@/lib/utils";

const SYNC = "cloudpulse-timeline";
const AXIS_FONT = 10;

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
    <div className="pointer-events-none rounded-[8px] border border-monolith-border bg-monolith px-3.5 py-2.5 text-monolith-ink shadow-xl">
      <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-monolith-muted tnum">
        {clockHHMMSS(label ?? row.t)} UTC
      </div>
      <div className="flex flex-col gap-1">
        {rows(row).map((r) => (
          <div key={r.label} className="flex items-center gap-3 text-[11px]">
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
            <span className="text-monolith-muted">{r.label}</span>
            <span className="ml-auto font-mono font-medium tabular-nums text-monolith-ink">
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
    minTickGap: 48,
  };
}

const yAxisBase = {
  stroke: "var(--axis)",
  strokeWidth: 1,
  tick: { fill: "var(--ink-tertiary)", fontSize: AXIS_FONT },
  tickLine: false,
  axisLine: false,
  width: 40,
};

const grid = (
  <CartesianGrid vertical={false} stroke="var(--grid)" strokeWidth={1} />
);

/* ---------------------------------- demand -------------------------------- */

export function DemandPlot({
  rows,
  domain,
  nowT,
  horizonS = 45,
  height = 240,
}: {
  rows: Row[];
  domain: [number, number];
  nowT?: number;
  horizonS?: number;
  height?: number;
}) {
  const fadeId = React.useId();
  const max = Math.max(
    10,
    ...rows.map((r) => Math.max(r.rps ?? 0, r.fUpper ?? 0, r.capacity ?? 0)),
  );
  const ticks = niceTicks(max * 1.1);
  const showLookahead = nowT != null && domain[1] > nowT;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={rows}
        syncId={SYNC}
        margin={{ top: 10, right: 16, bottom: 4, left: 0 }}
      >
        <defs>
          <linearGradient id={fadeId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-demand)" stopOpacity={0.12} />
            <stop offset="100%" stopColor="var(--color-demand)" stopOpacity={0.0} />
          </linearGradient>
        </defs>
        {grid}
        <XAxis {...axisProps(domain)} />
        <YAxis
          {...yAxisBase}
          domain={[0, ticks[ticks.length - 1]]}
          ticks={ticks}
        />

        {showLookahead ? (
          <ReferenceArea
            x1={nowT}
            x2={domain[1]}
            fill="var(--color-forecast)"
            fillOpacity={0.06}
            ifOverflow="extendDomain"
          />
        ) : null}

        {nowT != null ? (
          <ReferenceLine
            x={nowT}
            stroke="var(--ink)"
            strokeWidth={1.2}
            strokeDasharray="4 4"
            label={{
              value: `NOW -> +${horizonS}S`,
              position: "insideTopRight",
              fill: "var(--color-forecast)",
              fontSize: 10,
            }}
          />
        ) : null}

        {/* Conformal 90% prediction cone */}
        <Area
          type="monotone"
          dataKey="fRange"
          stroke="none"
          fill="var(--color-forecast)"
          fillOpacity={0.14}
          isAnimationActive={false}
          connectNulls={false}
        />

        {/* Provisioned capacity step line */}
        <Line
          dataKey="capacity"
          type="stepAfter"
          stroke="var(--color-replicas)"
          strokeWidth={1.75}
          strokeDasharray="5 4"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />

        {/* Forecast median */}
        <Line
          type="monotone"
          dataKey="fPoint"
          stroke="var(--color-forecast)"
          strokeWidth={2}
          strokeLinecap="round"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />

        {/* Measured demand smooth curve + subtle vertical gradient */}
        <Area
          type="monotone"
          dataKey="rps"
          stroke="var(--color-demand)"
          strokeWidth={2.5}
          strokeLinecap="round"
          fill={`url(#${fadeId})`}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />

        <Tooltip
          cursor={{ stroke: "var(--ink-tertiary)", strokeWidth: 1 }}
          content={
            <Tip
              rows={(r) => [
                {
                  label: "Measured",
                  value: `${r.rps?.toFixed(1) ?? "—"} req/s`,
                  swatch: "#ffffff",
                },
                {
                  label: "Forecast",
                  value:
                    r.fPoint != null ? `${r.fPoint.toFixed(1)} req/s` : "—",
                  swatch: "var(--color-forecast)",
                },
                {
                  label: "90% cone",
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
  height = 140,
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
        margin={{ top: 10, right: 16, bottom: 4, left: 0 }}
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
          strokeDasharray="3 3"
          label={{
            value: "MAX_PODS",
            position: "insideBottomRight",
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
          fillOpacity={0.12}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          dataKey="desired"
          type="stepAfter"
          stroke="var(--color-demand)"
          strokeWidth={1.2}
          strokeDasharray="3 3"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Tooltip
          cursor={{ stroke: "var(--ink-tertiary)", strokeWidth: 1 }}
          content={
            <Tip
              rows={(r) => [
                {
                  label: "Ready",
                  value: String(r.replicas ?? "—"),
                  swatch: "var(--color-replicas)",
                },
                {
                  label: "Desired",
                  value: String(r.desired ?? "—"),
                  swatch: "#ffffff",
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
  height = 152,
}: {
  rows: Row[];
  domain: [number, number];
  sloMs: number;
  height?: number;
}) {
  const max = Math.max(sloMs * 1.4, ...rows.map((r) => r.p99 ?? 0));
  const ticks = niceTicks(max, 3);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={rows}
        syncId={SYNC}
        margin={{ top: 8, right: 14, bottom: 4, left: 0 }}
      >
        {grid}
        <XAxis {...axisProps(domain)} />
        <YAxis
          {...yAxisBase}
          domain={[0, ticks[ticks.length - 1]]}
          ticks={ticks}
        />
        <ReferenceLine
          y={sloMs}
          stroke="var(--color-forecast)"
          strokeWidth={1.5}
          strokeDasharray="4 4"
        />

        {/* p50 -> p99 shaded envelope */}
        <Area
          type="monotone"
          dataKey="latRange"
          stroke="none"
          fill="var(--color-demand)"
          fillOpacity={0.1}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="p50"
          stroke="var(--ink-tertiary)"
          strokeWidth={1.2}
          strokeDasharray="3 3"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="p99"
          stroke="var(--color-demand)"
          strokeWidth={2}
          strokeLinecap="round"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Tooltip
          cursor={{ stroke: "var(--ink-tertiary)", strokeWidth: 1 }}
          content={
            <Tip
              rows={(r) => [
                {
                  label: "p99",
                  value: `${r.p99?.toFixed(1) ?? "—"} ms`,
                  swatch: "#ffffff",
                },
                {
                  label: "p50",
                  value: `${r.p50?.toFixed(1) ?? "—"} ms`,
                  swatch: "#a1a1aa",
                  dashed: true,
                },
                {
                  label: "SLO",
                  value: `${sloMs} ms`,
                  swatch: "var(--color-forecast)",
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
              className="h-2.5 w-3.5 shrink-0 rounded-[2px] border"
              style={{
                borderColor: `color-mix(in srgb, ${i.color} 55%, transparent)`,
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
          <span className="text-[11px] font-medium text-ink-secondary">
            {i.label}
          </span>
        </li>
      ))}
    </ul>
  );
}
