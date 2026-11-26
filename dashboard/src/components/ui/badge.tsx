import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * Status never travels as colour alone — every variant below is rendered with
 * a text label, and callers pair it with an icon where it carries meaning.
 * On the light surface `warning` and `serious` are deliberately sub-3:1, so
 * the label IS the accessible channel, not a decoration.
 */
const badge = cva(
  "inline-flex items-center gap-1.5 rounded-[var(--radius-chip)] border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider leading-none",
  {
    variants: {
      tone: {
        neutral: "border-edge-strong text-ink-secondary",
        good: "border-good/40 text-good",
        warning: "border-warning/50 text-warning",
        serious: "border-serious/50 text-serious",
        critical: "border-critical/50 text-critical",
        demand: "border-demand/40 text-demand",
        forecast: "border-forecast/40 text-forecast",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badge> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone }), className)} {...props} />;
}
