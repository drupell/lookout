"use client";

import { Info } from "lucide-react";

import { Card, CardBody } from "@/components/ui";
import { sourceErrorLabel } from "@/lib/runSummary";
import type { RunSummary } from "@/lib/schemas";

interface Props {
  /** The most recent run for this user, or undefined if none exist. */
  run: RunSummary | undefined;
}

/**
 * Renders an info card explaining why a run produced 0 deals — but only when
 * the run actually had an upstream-data failure. Returns null otherwise so we
 * don't pester users on healthy zero-result runs.
 */
export function RunErrorNotice({ run }: Props) {
  if (!run) return null;
  const errors = run.source_errors;
  if (!errors || errors.length === 0) return null;

  return (
    <Card className="mb-4 border-amber-200 bg-amber-50/60 dark:border-amber-900/50 dark:bg-amber-950/20">
      <CardBody className="flex items-start gap-3 py-3">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400" />
        <div className="space-y-1">
          <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
            Last run had a data-source issue
          </p>
          <ul className="space-y-1 text-xs text-amber-900/90 dark:text-amber-100/90">
            {errors.map((code) => (
              <li key={code}>{sourceErrorLabel(code)}</li>
            ))}
          </ul>
        </div>
      </CardBody>
    </Card>
  );
}
