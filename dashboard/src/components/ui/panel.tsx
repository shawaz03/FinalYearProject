import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * A panel is a physical plate: tinted surface, 1px lit top edge, registration
 * ticks at opposing corners. Depth comes from material, never from a drop
 * shadow or a blurred gradient — those would compete with the data.
 */
export function Panel({
  className,
  ticks = true,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { ticks?: boolean }) {
  return (
    <div
      className={cn(
        "plate rounded-[var(--radius-panel)]",
        ticks && "ticked",
        className,
      )}
      {...props}
    />
  );
}

/**
 * Header carries a section index (01, 02 …) like a plate on an instrument
 * rack. The index is the cheapest way to make a layout read as *designed*
 * rather than as a stack of cards.
 */
export function PanelHeader({
  index,
  title,
  unit,
  children,
  className,
}: {
  index?: string;
  title: string;
  unit?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 border-b border-edge px-4 py-2.5",
        className,
      )}
    >
      {index ? (
        <span className="font-mono text-[10px] tabular-nums text-ink-tertiary">
          {index}
        </span>
      ) : null}
      <h2 className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-[0.16em]">
        {title}
      </h2>
      <div className="rule-ticks h-2 flex-1 opacity-35" aria-hidden />
      {unit ? (
        <span className="whitespace-nowrap font-mono text-[10px] uppercase tracking-wider text-ink-tertiary">
          {unit}
        </span>
      ) : null}
      {children}
    </div>
  );
}

/** A small key/value readout, as used along instrument rails. */
export function Readout({
  k,
  v,
  tone,
}: {
  k: string;
  v: string;
  tone?: string;
}) {
  return (
    <div className="flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="eyebrow">{k}</span>
      <span
        className="font-mono text-[11px] tabular-nums"
        style={{ color: tone ?? "var(--ink-secondary)" }}
      >
        {v}
      </span>
    </div>
  );
}
