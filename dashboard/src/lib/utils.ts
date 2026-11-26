import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Compact figures for stat tiles: 1,284 / 12.9K / 1.4M. */
export function compact(n: number, dp = 1): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(dp) + "M";
  if (abs >= 10_000) return (n / 1_000).toFixed(dp) + "K";
  return n.toLocaleString(undefined, { maximumFractionDigits: dp });
}

export function clockHHMMSS(t: number): string {
  const d = new Date(t);
  return [d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()]
    .map((v) => String(v).padStart(2, "0"))
    .join(":");
}

export function clockHHMM(t: number): string {
  const d = new Date(t);
  return (
    String(d.getUTCHours()).padStart(2, "0") +
    ":" +
    String(d.getUTCMinutes()).padStart(2, "0")
  );
}
