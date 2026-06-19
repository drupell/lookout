"use client";

import { ChevronRight, Copy } from "lucide-react";

import { Badge, Button, useToast } from "@/components/ui";
import { relativeTime } from "@/lib/dealFormat";
import { outcomeBlurb, runDurationLabel, sourceErrorLabel } from "@/lib/runSummary";
import type { RunSummary } from "@/lib/schemas";

interface RunRowProps {
  run: RunSummary;
}

function statusTone(status: string | undefined): "success" | "danger" | "warning" | "neutral" {
  switch (status?.toUpperCase()) {
    case "SUCCESS":
      return "success";
    case "GUARDRAIL_BLOCKED":
    case "PARTIAL":
      return "warning";
    case "ERROR":
    case "FAILED":
      return "danger";
    default:
      return "neutral";
  }
}

function humanize(name: string): string {
  return name.toLowerCase().replaceAll("_", " ");
}

/**
 * Browser-tolerant clipboard copy. The modern API works in any secure context
 * (incl. localhost); the `<textarea>` fallback covers the rare insecure case
 * — `execCommand` is deprecated but still the only no-permissions option for
 * an http:// origin, so we keep it behind the feature check.
 */
async function copyText(text: string): Promise<void> {
  // lib.dom types `navigator.clipboard` as non-nullable, but on insecure
  // origins or older Safari it can be undefined. Cast through `unknown` so
  // the runtime guard is meaningful and the lint check doesn't flatten it.
  const clipboard = (navigator as unknown as { clipboard?: Clipboard }).clipboard;
  if (clipboard) {
    await clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  // eslint-disable-next-line @typescript-eslint/no-deprecated
  document.execCommand("copy");
  document.body.removeChild(ta);
}

/**
 * One row in the "Recent runs" list. Closed state is a clean two-line
 * summary (status badge + outcome blurb + relative time). Open state reveals
 * the full run_id (with a copy button) and any guardrail / source-error
 * context — the admin/troubleshooting surface tucked out of the way.
 */
export function RunRow({ run }: RunRowProps) {
  const toast = useToast();
  const blurb = outcomeBlurb(run);
  const duration = runDurationLabel(run);
  const triggers = run.guardrail_triggers ?? [];
  const errors = run.source_errors ?? [];

  async function handleCopy() {
    try {
      await copyText(run.run_id);
      toast.success("Run ID copied");
    } catch {
      toast.error("Couldn't copy run ID");
    }
  }

  return (
    <li>
      <details className="group">
        <summary
          className="focus-ring flex cursor-pointer list-none items-start justify-between gap-3 px-5 py-3 transition-colors duration-fast hover:bg-stone-50/70 marker:hidden [&::-webkit-details-marker]:hidden dark:hover:bg-stone-800/30"
        >
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <Badge tone={statusTone(run.status)} className="mt-0.5 shrink-0">
              {run.status ?? "unknown"}
            </Badge>
            <div className="min-w-0 flex-1 space-y-0.5">
              {blurb ? (
                <p className="truncate text-sm text-stone-800 dark:text-stone-200">{blurb}</p>
              ) : null}
              {run.started_at ? (
                <p className="text-xs text-stone-500">{relativeTime(run.started_at)}</p>
              ) : null}
            </div>
          </div>
          <ChevronRight
            aria-hidden="true"
            className="mt-1 h-3.5 w-3.5 shrink-0 text-stone-400 transition-transform duration-fast ease-editorial group-open:rotate-90"
          />
        </summary>

        <div className="border-t border-stone-200 bg-stone-50/40 px-5 py-3 text-xs dark:border-stone-800 dark:bg-stone-900/30">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2">
            <dt className="text-stone-500">Run ID</dt>
            <dd className="flex flex-wrap items-center gap-2">
              <span className="font-mono break-all text-stone-700 dark:text-stone-300">
                {run.run_id}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="Copy run ID"
                onClick={handleCopy}
                leadingIcon={<Copy className="h-3.5 w-3.5" aria-hidden="true" />}
              >
                Copy
              </Button>
            </dd>

            {run.started_at ? (
              <>
                <dt className="text-stone-500">Started</dt>
                <dd className="text-stone-700 dark:text-stone-300">
                  {new Date(run.started_at).toLocaleString()}
                  {duration ? <span className="text-stone-500"> (took {duration})</span> : null}
                </dd>
              </>
            ) : null}

            {triggers.length > 0 ? (
              <>
                <dt className="text-stone-500">Guardrails</dt>
                <dd className="text-stone-700 dark:text-stone-300">
                  {triggers.map(humanize).join(", ")}
                </dd>
              </>
            ) : null}

            {errors.length > 0 ? (
              <>
                <dt className="text-stone-500">Source errors</dt>
                <dd className="text-stone-700 dark:text-stone-300">
                  <ul className="space-y-1">
                    {errors.map((code) => (
                      <li key={code}>{sourceErrorLabel(code)}</li>
                    ))}
                  </ul>
                </dd>
              </>
            ) : null}
          </dl>
        </div>
      </details>
    </li>
  );
}
