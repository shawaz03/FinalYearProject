"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Stat tile contract: label · value · optional delta · optional sparkline.
 *
 * Values use the font's PROPORTIONAL figures. `tabular-nums` is reserved for
 * columns that align vertically (the decision log, axis ticks) — at display
 * sizes equal-width digits make a number like 121 look loose.
 */

function Spark({ points, tone }: { points: number[]; tone: string }) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const w = 72;
  const h = 20;
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((p - min) / span) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const lastX = w;
  const lastY = h - ((points[points.length - 1] - min) / span) * h;

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h + 2}`}
      className="overflow-visible"
      aria-hidden
    >
      <path
        d={d}
        fill="none"
        stroke="var(--ink-tertiary)"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.55}
      />
      {/* current period in the accent, with a 2px surface ring so it stays
          legible where it crosses the line */}
      <circle cx={lastX} cy={lastY} r={3.5} fill={tone} />
      <circle
        cx={lastX}
        cy={lastY}
        r={3.5}
        fill="none"
        stroke="var(--surface)"
        strokeWidth={2}
      />
    </svg>
  );
}

export function StatTile({
  label,
  value,
  unit,
  hint,
  tone = "var(--ink)",
  spark,
  state,
  className,
  ...rest
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  tone?: string;
  spark?: number[];
  /** status is never colour-alone: this text label is the accessible channel */
  state?: { text: string; color: string };
  className?: string;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "plate ticked flex flex-col justify-between gap-3 rounded-[var(--radius-panel)] px-4 py-3.5",
        className,
      )}
      {...rest}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="eyebrow leading-tight">{label}</span>
        {state ? (
          <span className="flex items-center gap-1.5 whitespace-nowrap">
            <span
              aria-hidden
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: state.color }}
            />
            <span className="eyebrow">{state.text}</span>
          </span>
        ) : null}
      </div>

      <div className="flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-1">
          <span className="display text-[34px]" style={{ color: tone }}>
            {value}
          </span>
          {unit ? (
            <span className="text-[12px] text-ink-tertiary">{unit}</span>
          ) : null}
        </div>
        {spark ? <Spark points={spark} tone={tone} /> : null}
      </div>

      {hint ? (
        <span className="font-mono text-[10px] leading-tight text-ink-tertiary">
          {hint}
        </span>
      ) : null}
    </div>
  );
}
