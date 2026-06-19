"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { UsageResponse } from "@/lib/schemas";

/**
 * Quiet inline pill that surfaces the user's current MarketCheck call usage.
 *
 *   default tier: "MarketCheck: 312 / 500 calls this month (shared default tier)"
 *   byok:         "MarketCheck: 18 / 1,500 (your key)"
 *
 * Visual leans neutral until utilization climbs — faint amber at >70%, faint
 * red at >90%, so the user gets a calibrated heads-up *before* a run failure.
 * BYOK always renders neutral (we don't know the user's own quota ceiling).
 *
 * Errors (incl. zod parse failures) are intentionally swallowed: this is an
 * ambient surface and a transient `/me/usage` blip should not stamp a red
 * error on the Settings page. A loading flicker is similarly avoided by
 * rendering null until the first response lands.
 */
export function UsageQuotaPill({ className }: { className?: string }) {
  const [usage, setUsage] = useState<UsageResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getUsage()
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch((err: unknown) => {
        // Ambient surface — log for ops, render nothing.
        // eslint-disable-next-line no-console
        console.warn("UsageQuotaPill: failed to load usage", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!usage) return null;

  const mc = usage.marketcheck;
  const isByok = mc.tier === "byok";

  // Tier-aware tint. BYOK stays neutral (no shared-quota anxiety to model).
  const pctUsed = mc.pct_used ?? (mc.limit ? (mc.calls / mc.limit) * 100 : 0);
  const tone: "neutral" | "amber" | "red" = isByok
    ? "neutral"
    : pctUsed > 90
      ? "red"
      : pctUsed > 70
        ? "amber"
        : "neutral";

  const toneClasses: Record<typeof tone, string> = {
    neutral: "border-border bg-surface-elevated text-stone-700 dark:text-stone-300",
    amber:
      "border-amber-200 bg-amber-50/60 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-200",
    red: "border-red-200 bg-red-50/60 text-red-900 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-200",
  };

  const formatNum = (n: number) => n.toLocaleString();
  const label = isByok
    ? `MarketCheck: ${formatNum(mc.calls)}${
        mc.limit !== null ? ` / ${formatNum(mc.limit)}` : ""
      } (your key)`
    : `MarketCheck: ${formatNum(mc.calls)} / ${formatNum(mc.limit ?? 0)} calls this month (shared default tier)`;

  return (
    <span
      role="status"
      aria-label={label}
      className={`inline-flex items-center gap-2 rounded-md border px-2 py-1 text-xs ${toneClasses[tone]} ${
        className ?? ""
      }`}
    >
      {label}
    </span>
  );
}
