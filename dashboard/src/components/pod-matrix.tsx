"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * 15-slot (or up to `maxPods`) Kubernetes Pod Allocation Matrix (Design A v2).
 * Renders Ready (monolith black), Warming (vermilion dashed), and Standby slots
 * directly instead of a plain text ratio.
 */
export function PodMatrix({
  ready,
  desired,
  maxPods,
  capacityRps,
  expanded = false,
}: {
  ready: number;
  desired: number;
  maxPods: number;
  capacityRps: number;
  expanded?: boolean;
}) {
  const count = Math.max(1, maxPods);
  const warming = Math.max(0, desired - ready);
  const standby = Math.max(0, count - Math.max(ready, desired));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-wider text-ink-tertiary">
          <span className="inline-flex items-center gap-1.5">
            <span
              className="h-2 w-2 rounded-[2px] border border-edge-strong bg-monolith"
              aria-hidden
            />
            {ready} Ready
          </span>
          {warming > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-forecast">
              <span
                className="h-2 w-2 rounded-[2px] border border-forecast bg-forecast/15"
                aria-hidden
              />
              {warming} Warming
            </span>
          ) : null}
          <span className="inline-flex items-center gap-1.5">
            <span
              className="h-2 w-2 rounded-[2px] border border-edge bg-surface-sunken"
              aria-hidden
            />
            {standby} Idle
          </span>
        </div>
        <span className="font-mono text-[11px] font-medium text-replicas tnum">
          {ready} / {count} READY
        </span>
      </div>

      <div
        className={cn(
          "grid gap-2",
          expanded ? "grid-cols-3 sm:grid-cols-5" : "grid-cols-5",
        )}
      >
        {Array.from({ length: count }, (_, idx) => {
          const num = idx + 1;
          const isReady = num <= ready;
          const isWarming = !isReady && num <= desired;
          const status = isReady ? "RDY" : isWarming ? "WRM" : "IDLE";

          return (
            <div
              key={num}
              title={`Pod p${String(num).padStart(2, "0")}: ${status}${isReady ? ` (${capacityRps} rps)` : ""}`}
              className={cn(
                "flex flex-col items-center justify-center rounded-[7px] border font-mono transition-colors",
                expanded ? "py-4 px-2 gap-1" : "py-2 px-1.5 gap-0.5",
                isReady
                  ? "border-monolith-border bg-monolith text-monolith-ink"
                  : isWarming
                    ? "border-forecast bg-forecast/10 text-forecast"
                    : "border-edge bg-surface-sunken text-ink-tertiary",
              )}
            >
              <span className="text-[10px] font-medium leading-none tnum">
                p{String(num).padStart(2, "0")}
              </span>
              <span
                className={cn(
                  "text-[8px] uppercase tracking-wider leading-none",
                  isReady
                    ? "text-replicas"
                    : isWarming
                      ? "text-forecast"
                      : "text-ink-tertiary/75",
                )}
              >
                {status}
              </span>
              {expanded ? (
                <span className="mt-1 text-[9px] opacity-75">
                  {isReady ? `${capacityRps} rps` : isWarming ? "readiness" : "standby"}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-[10px] text-ink-tertiary">
        <span>Guaranteed QoS: 250m CPU / 128Mi · 0 Pending</span>
        <span className="tnum">Capacity {(ready * capacityRps).toFixed(0)} req/s</span>
      </div>
    </div>
  );
}
