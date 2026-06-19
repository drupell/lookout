"use client";

import { Loader2 } from "lucide-react";

import { Card, CardBody } from "@/components/ui";
import type { InFlightRun } from "@/lib/schemas";

interface Props {
  inFlight: InFlightRun | null;
  /** When true, polling has been failing — show a gentle "reconnecting" note. */
  reconnecting?: boolean;
}

// Maps backend node names to short, human-friendly labels. Falls back to a
// title-cased version if a node we haven't mapped shows up — adding a new
// node to the agent doesn't break the UI.
const NODE_LABEL: Record<string, string> = {
  starting: "Starting",
  fetch_listings: "Fetching listings",
  validate_listings: "Validating listings",
  fetch_trade_in: "Looking up trade-in",
  validate_trade_in: "Validating trade-in",
  rag_enrich: "Adding context",
  validate_rag: "Validating context",
  score_deals: "Scoring deals",
  validate_scores: "Validating scores",
  filter_new_deals: "Filtering new deals",
  draft_emails: "Drafting emails",
  validate_drafts: "Reviewing drafts",
  persist_results: "Saving results",
  persist_run_only: "Saving results",
};

function nodeLabel(name: string | undefined): string {
  if (!name) return "Working…";
  return (
    NODE_LABEL[name] ??
    name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/**
 * Presentational progress bar for an in-flight run. Returns null when no run
 * is active; the parent owns the polling via `useInFlightRun()`.
 */
export function RunProgress({ inFlight, reconnecting = false }: Props) {
  if (!inFlight?.in_flight) return null;

  const completed = inFlight.progress?.completed ?? 0;
  const total = inFlight.progress?.total ?? 1;
  const pct = Math.min(100, Math.max(0, Math.round((completed / total) * 100)));

  return (
    <Card className="mb-4 border-amber-200 bg-amber-50/60 dark:border-amber-900/50 dark:bg-amber-950/20">
      <CardBody className="space-y-2 py-3">
        <div className="flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-amber-700 dark:text-amber-400" />
          <span className="text-sm text-amber-900 dark:text-amber-100">
            {reconnecting
              ? "Reconnecting…"
              : `Run in progress · ${nodeLabel(inFlight.current_node)}`}
          </span>
          <span className="ml-auto text-xs tabular-nums text-amber-800 dark:text-amber-300">
            {completed}/{total}
          </span>
        </div>
        <div
          role="progressbar"
          aria-valuenow={completed}
          aria-valuemin={0}
          aria-valuemax={total}
          className="h-1.5 overflow-hidden rounded-full bg-amber-200/60 dark:bg-amber-900/40"
        >
          <div
            className="h-full rounded-full bg-amber-600 transition-[width] duration-500 ease-out dark:bg-amber-400"
            style={{ width: `${String(pct)}%` }}
          />
        </div>
      </CardBody>
    </Card>
  );
}
