"use client";

import * as React from "react";
import { ArrowDown, ArrowRight, ArrowUp } from "lucide-react";
import type { Decision, DecisionMode } from "@/lib/telemetry";
import { Badge } from "@/components/ui/badge";
import { clockHHMMSS } from "@/lib/utils";

/**
 * Every actuation, with the arithmetic that produced it.
 *
 * This is the panel that answers "why did it do that?" — the question a viva
 * examiner will ask. Showing ceil(upper / C) alongside the reactive branch
 * makes the hybrid arbiter auditable instead of a black box.
 */

const MODE_TONE: Record<
  DecisionMode,
  "neutral" | "good" | "warning" | "serious" | "critical" | "demand" | "forecast"
> = {
  predictive: "forecast",
  reactive: "critical",
  hold: "neutral",
  stabilising: "neutral",
  clamped: "warning",
  degraded: "serious",
};

export function DecisionLog({ decisions }: { decisions: Decision[] }) {
  const rows = [...decisions].reverse();

  return (
    <div className="max-h-[320px] overflow-y-auto">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          Scaling decisions, most recent first, with the inputs behind each
        </caption>
        <thead className="sticky top-0 z-10 bg-surface">
          <tr className="border-b border-edge">
            {["Time", "Mode", "Replicas", "ceil(upper / C)", "Reason"].map(
              (h) => (
                <th
                  key={h}
                  scope="col"
                  className="px-4 py-2 text-[10px] font-medium uppercase tracking-wider text-ink-tertiary whitespace-nowrap"
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => {
            const delta = d.toReplicas - d.fromReplicas;
            const Icon =
              delta > 0 ? ArrowUp : delta < 0 ? ArrowDown : ArrowRight;
            return (
              <tr
                key={d.t}
                className="border-b border-edge/60 last:border-0 hover:bg-surface-sunken"
              >
                <td className="px-4 py-1.5 font-mono text-[11px] tabular-nums text-ink-tertiary whitespace-nowrap">
                  {clockHHMMSS(d.t)}
                </td>
                <td className="px-4 py-1.5">
                  <Badge tone={MODE_TONE[d.mode]}>{d.mode}</Badge>
                </td>
                <td className="px-4 py-1.5 whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-ink">
                    {d.fromReplicas}
                    <Icon
                      size={11}
                      aria-hidden
                      className={
                        delta > 0
                          ? "text-good"
                          : delta < 0
                            ? "text-ink-tertiary"
                            : "text-ink-tertiary"
                      }
                    />
                    {d.toReplicas}
                  </span>
                </td>
                <td className="px-4 py-1.5 font-mono text-[11px] tabular-nums text-ink-secondary whitespace-nowrap">
                  ceil({d.upperBound.toFixed(0)} / {d.capacityRps}) ={" "}
                  {d.predictiveReplicas}
                </td>
                <td className="px-4 py-1.5 text-[11px] text-ink-secondary">
                  {d.reason}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
