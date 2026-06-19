"use client";

import { AlertTriangle, X } from "lucide-react";
import { useEffect, useState } from "react";

import type { UsageResponse } from "@/lib/schemas";

const STORAGE_KEY_PREFIX = "quota-banner-dismissed-";

/**
 * Warns default-tier users that their share of the MarketCheck quota is
 * almost spent. Shown when the backend reports `approaching_limit === true`
 * (currently >=80% utilization) — never for BYOK users, who have their own
 * quota and are calmer to reason about.
 *
 * Dismissal is keyed by `yyyy_mm` so a fresh month resurfaces the warning —
 * which is the correct behavior since the quota itself resets monthly. We
 * don't need cross-user keying because localStorage already partitions per
 * browser profile / user.
 */
interface Props {
  usage: UsageResponse | null;
}

function storageKey(yyyyMm: string): string {
  return `${STORAGE_KEY_PREFIX}${yyyyMm}`;
}

function isDismissed(yyyyMm: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(storageKey(yyyyMm)) === "1";
  } catch {
    return false;
  }
}

function dismiss(yyyyMm: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(yyyyMm), "1");
  } catch {
    // localStorage might be disabled (private mode); fail silently — the
    // banner just won't stick across reloads, which is acceptable.
  }
}

export function QuotaBanner({ usage }: Props) {
  const mc = usage?.marketcheck;
  // We only ever show this for default-tier users hitting the approaching
  // threshold. BYOK users opted into their own quota and don't need nagging,
  // and a null `limit` (shouldn't happen for default, but we narrow anyway)
  // makes "N runs left" math meaningless.
  const shouldShow =
    mc?.tier === "default" && mc.approaching_limit && mc.limit !== null;

  const yyyyMm = mc?.yyyy_mm;
  const [hidden, setHidden] = useState(true);

  useEffect(() => {
    if (!shouldShow || yyyyMm === undefined) return;
    setHidden(isDismissed(yyyyMm));
  }, [shouldShow, yyyyMm]);

  if (!shouldShow || hidden || mc.limit === null) return null;

  const remaining = Math.max(0, mc.limit - mc.calls);
  // Heuristic: typical default-tier run consumes ~50 MarketCheck calls
  // (target_listings cap). Floor at 0 so the copy never reads "-1 more runs".
  const runsLeft = Math.max(0, Math.floor(remaining / 50));

  const handleDismiss = () => {
    dismiss(mc.yyyy_mm);
    setHidden(true);
  };

  return (
    <div
      role="status"
      className="mb-4 flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-900/50 dark:bg-amber-950/30"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400" />
      <div className="flex-1 space-y-1">
        <p className="text-sm text-amber-900 dark:text-amber-100">
          You&apos;re at{" "}
          <span className="font-medium tabular-nums">
            {mc.calls.toLocaleString()} / {mc.limit.toLocaleString()}
          </span>{" "}
          MarketCheck calls this month — about{" "}
          <span className="font-medium">{runsLeft}</span> more{" "}
          {runsLeft === 1 ? "run" : "runs"} before the shared default-tier limit. Consider
          BYOK for higher quota.
        </p>
      </div>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss"
        className="focus-ring rounded p-1 text-amber-700 hover:bg-amber-100 dark:text-amber-400 dark:hover:bg-amber-900/40"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
