import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * A panel is a flat surface with a hairline edge. No shadow, no gradient, no
 * blur: depth would compete with the data, which is the only thing in this
 * interface allowed to be loud.
 */
export function Panel({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-[var(--radius-panel)] border border-edge bg-surface",
        className,
      )}
      {...props}
    />
  );
}

export function PanelHeader({
  title,
  unit,
  children,
  className,
}: {
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
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-secondary whitespace-nowrap">
        {title}
      </h2>
      {/* the measurement rule: the one ornament in the interface */}
      <div className="rule-ticks h-2 flex-1 opacity-40" aria-hidden />
      {unit ? (
        <span className="font-mono text-[10px] uppercase tracking-wider text-ink-tertiary whitespace-nowrap">
          {unit}
        </span>
      ) : null}
      {children}
    </div>
  );
}
