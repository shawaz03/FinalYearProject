"use client";

import * as React from "react";
import { Activity, Cpu, Gauge } from "lucide-react";

export interface MetricItem {
  id: "demand" | "latency" | "cpu";
  label: string;
  value: string;
  unit: string;
  hint: string;
  tone: string;
  spark: number[];
}

function SmoothSpark({ points, tone }: { points: number[]; tone: string }) {
  const gradId = React.useId();
  if (points.length < 2) return null;

  const w = 84;
  const h = 26;
  const padY = 3;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;

  const coords = points.map((p, i) => ({
    x: (i / (points.length - 1)) * w,
    y: h - padY - ((p - min) / span) * (h - padY * 2),
  }));

  let lineD = `M ${coords[0].x.toFixed(1)} ${coords[0].y.toFixed(1)}`;
  for (let i = 0; i < coords.length - 1; i++) {
    const a = coords[i];
    const b = coords[i + 1];
    const cx = ((a.x + b.x) / 2).toFixed(1);
    lineD += ` C ${cx} ${a.y.toFixed(1)}, ${cx} ${b.y.toFixed(1)}, ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
  }
  const areaD = `${lineD} L ${w} ${h} L 0 ${h} Z`;

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="shrink-0 overflow-visible"
      aria-hidden
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={tone} stopOpacity={0.22} />
          <stop offset="100%" stopColor={tone} stopOpacity={0.0} />
        </linearGradient>
      </defs>
      <path d={areaD} fill={`url(#${gradId})`} />
      <path
        d={lineD}
        fill="none"
        stroke={tone}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const ICONS = {
  demand: Activity,
  latency: Gauge,
  cpu: Cpu,
};

/**
 * Architectural 3-column KPI tray separated by hairline rules rather than
 * heavy stacked boxes (Design A v2).
 */
export function MetricBar({ items }: { items: MetricItem[] }) {
  return (
    <div className="studio-card grid grid-cols-1 divide-y divide-edge md:grid-cols-3 md:divide-x md:divide-y-0">
      {items.map((m) => {
        const Icon = ICONS[m.id];
        return (
          <div
            key={m.id}
            className="flex flex-col justify-between gap-3 px-6 py-5"
          >
            <div className="flex items-center gap-2">
              <Icon size={14} style={{ color: m.tone }} aria-hidden />
              <span className="eyebrow">{m.label}</span>
            </div>

            <div className="flex items-end justify-between gap-4">
              <div className="flex items-baseline gap-1.5">
                <span className="display text-[36px] text-ink">{m.value}</span>
                <span className="text-[12px] font-medium text-ink-secondary">
                  {m.unit}
                </span>
              </div>
              <SmoothSpark points={m.spark} tone={m.tone} />
            </div>

            <span className="font-mono text-[10px] leading-tight text-ink-secondary">
              {m.hint}
            </span>
          </div>
        );
      })}
    </div>
  );
}
