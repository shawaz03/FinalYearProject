"use client";

import * as React from "react";
import { ArrowDown, ArrowRight, ArrowUp } from "lucide-react";
import type { Decision, DecisionMode } from "@/lib/telemetry";
import { Badge } from "@/components/ui/badge";
import { clockHHMMSS, cn } from "@/lib/utils";

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

function accentClass(d: Decision): string {
  if (d.mode === "reactive" || d.mode === "degraded") return "bg-critical";
  if (d.toReplicas > d.fromReplicas || d.mode === "predictive") return "bg-forecast";
  if (d.toReplicas < d.fromReplicas) return "bg-replicas";
  return "bg-ink";
}

/**
 * Compact vertical decision cards shown on the Control Plane overview (Design A v2).
 * Prioritizes scale actuations first when filtering, or latest decisions cleanly.
 */
export function ArbiterTrailMini({ decisions }: { decisions: Decision[] }) {
  const ordered = [...decisions].reverse();
  const actuations = ordered.filter((d) => d.toReplicas !== d.fromReplicas);
  const shown = (actuations.length >= 2 ? actuations : ordered).slice(0, 3);

  if (shown.length === 0) {
    return (
      <div className="rounded-[8px] bg-surface-sunken px-3 py-3 font-mono text-[11px] text-ink-tertiary">
        Awaiting first 30s arbiter decision tick...
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {shown.map((d) => {
        const isScaleUp = d.toReplicas > d.fromReplicas;
        return (
          <div
            key={d.t}
            className="flex items-center gap-3 rounded-[8px] bg-surface-sunken px-3 py-2"
          >
            <span
              className={cn("h-7 w-[3px] shrink-0 rounded-full", accentClass(d))}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <div
                className={cn(
                  "flex items-center gap-2 font-mono text-[10px] font-medium uppercase tnum",
                  d.mode === "predictive" || isScaleUp
                    ? "text-forecast"
                    : d.mode === "reactive"
                      ? "text-critical"
                      : "text-ink",
                )}
              >
                <span>{clockHHMMSS(d.t)}</span>
                <span>·</span>
                <span>
                  {d.mode} {d.fromReplicas} -&gt; {d.toReplicas}
                </span>
              </div>
              <div className="truncate text-[11px] text-ink-secondary">
                {d.reason} (ceil({d.upperBound.toFixed(0)} / {d.capacityRps}) ={" "}
                {d.predictiveReplicas})
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Full auditable decision table shown under the Audit Log tab. */
export function DecisionLog({ decisions }: { decisions: Decision[] }) {
  const [filterMode, setFilterMode] = React.useState<"all" | "actuated" | DecisionMode>("all");
  const rows = [...decisions].reverse().filter((d) => {
    if (filterMode === "all") return true;
    if (filterMode === "actuated") return d.toReplicas !== d.fromReplicas;
    return d.mode === filterMode;
  });

  const counts = decisions.reduce<Record<string, number>>((acc, d) => {
    acc[d.mode] = (acc[d.mode] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-edge px-5 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {(
            [
              ["all", `all (${decisions.length})`],
              ["actuated", `actuations (${decisions.filter((d) => d.toReplicas !== d.fromReplicas).length})`],
              ["predictive", `predictive (${counts.predictive ?? 0})`],
              ["reactive", `reactive (${counts.reactive ?? 0})`],
              ["stabilising", `stabilising (${counts.stabilising ?? 0})`],
              ["hold", `hold (${counts.hold ?? 0})`],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilterMode(key)}
              className={cn(
                "rounded-[6px] border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors",
                filterMode === key
                  ? "border-monolith-border bg-monolith text-monolith-ink"
                  : "border-edge text-ink-secondary hover:border-edge-strong hover:text-ink",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="max-h-[460px] overflow-y-auto">
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
                    className="whitespace-nowrap px-5 py-2.5 text-[10px] font-medium uppercase tracking-wider text-ink-tertiary"
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
                  className={cn(
                    "border-b border-edge/60 last:border-0 hover:bg-surface-sunken",
                    delta !== 0 && "bg-forecast/[0.04]",
                  )}
                >
                  <td className="whitespace-nowrap px-5 py-2 font-mono text-[11px] tabular-nums text-ink-tertiary">
                    {clockHHMMSS(d.t)}
                  </td>
                  <td className="px-5 py-2">
                    <Badge tone={MODE_TONE[d.mode]}>{d.mode}</Badge>
                  </td>
                  <td className="whitespace-nowrap px-5 py-2">
                    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] font-medium tabular-nums text-ink">
                      {d.fromReplicas}
                      <Icon
                        size={11}
                        aria-hidden
                        className={
                          delta > 0
                            ? "text-good"
                            : delta < 0
                              ? "text-ink-secondary"
                              : "text-ink-tertiary"
                        }
                      />
                      {d.toReplicas}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-5 py-2 font-mono text-[11px] tabular-nums text-ink-secondary">
                    ceil({d.upperBound.toFixed(0)} / {d.capacityRps}) ={" "}
                    {d.predictiveReplicas}
                  </td>
                  <td className="px-5 py-2 text-[12px] text-ink-secondary">
                    {d.reason}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
