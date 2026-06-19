"use client";

import { AlertTriangle, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { MeResponse } from "@/lib/schemas";

import { dismiss, isDismissed, isStale } from "./staleSettings";

interface Props {
  me: MeResponse;
  onRefreshed?: () => void;
}

/**
 * Warns the user when their preferences were updated after the last completed
 * agent run — which means the deals on screen were scored against the *old*
 * criteria. Offers a "Run now" button that triggers a fresh run.
 *
 * Dismissal is per-(user_id, updated_at), so a future settings change will
 * re-arm the banner instead of being silently swallowed.
 */
export function StaleSettingsBanner({ me, onRefreshed }: Props) {
  const stale = isStale(me.updated_at, me.last_run_at);
  // Only consult localStorage on mount; updated_at + user_id are stable
  // enough that we don't need to recompute on every render.
  const [hidden, setHidden] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!stale || !me.updated_at) return;
    setHidden(isDismissed(me.user_id, me.updated_at));
  }, [stale, me.user_id, me.updated_at]);

  if (!stale || hidden || !me.updated_at) return null;

  const handleDismiss = () => {
    if (me.updated_at) dismiss(me.user_id, me.updated_at);
    setHidden(true);
  };

  const handleRun = async () => {
    setTriggering(true);
    setError(null);
    try {
      await api.triggerRun();
      // Hide the banner — the run is queued, deals will refresh shortly.
      handleDismiss();
      onRefreshed?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.toUserMessage() : "Failed to queue run");
    } finally {
      setTriggering(false);
    }
  };

  const changedAt = new Date(me.updated_at).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div
      role="status"
      className="mb-4 flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-900/50 dark:bg-amber-950/30"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400" />
      <div className="flex-1 space-y-2">
        <p className="text-sm text-amber-900 dark:text-amber-100">
          Your settings changed on{" "}
          <span className="font-medium">{changedAt}</span>. Current deals were scored
          against your previous criteria — they&apos;ll refresh on the next scheduled run.
        </p>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={handleRun} disabled={triggering}>
            {triggering ? "Queueing…" : "Run now"}
          </Button>
          {error ? (
            <span className="text-xs text-red-700 dark:text-red-400">{error}</span>
          ) : null}
        </div>
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
