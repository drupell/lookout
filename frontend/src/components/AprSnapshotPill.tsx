import type { MacroSnapshot } from "@/lib/schemas";

interface AprSnapshotPillProps {
  /**
   * Full macro snapshot from `/me/macro`. Null while the dashboard's
   * `Promise.allSettled` is in flight or after a soft failure — in either
   * case we render nothing rather than a flicker / placeholder.
   */
  data: MacroSnapshot | null;
}

/**
 * Quiet inline pill that surfaces the latest FRED 48-mo new-car APR alongside
 * an optional editorial context label ("24-month high", "near 12-mo low", …).
 * Lives next to the MarketTrendChart's factor chips — it is a *supporting*
 * metric, not a headline, so the visual stays small and never dominates.
 *
 *   "48-mo new car APR — 7.4% · 24-month high"
 *
 * The honey-amber tabular-nums value is the load-bearing element; the label
 * and context drop to muted small text so the pill reads as one quick glance.
 * If the macro cache hasn't been populated yet (cold-start, FRED-down), the
 * backend returns `auto_loan_apr: null` and this component renders nothing —
 * no flicker, no placeholder. The dashboard layout collapses gracefully.
 */
export function AprSnapshotPill({ data }: AprSnapshotPillProps) {
  if (data?.auto_loan_apr == null) return null;

  const apr = data.auto_loan_apr;
  // One decimal place reads cleanly at this scale ("7.4%") — FRED publishes
  // monthly to two decimals but the trailing digit is noise in this surface.
  const valueText = `${apr.value_pct.toFixed(1)}%`;
  const label = apr.context
    ? `${apr.label_short} — ${valueText} · ${apr.context}`
    : `${apr.label_short} — ${valueText}`;

  return (
    <span
      role="status"
      aria-label={label}
      className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-elevated px-2 py-1 text-xs dark:border-stone-800 dark:bg-stone-900"
    >
      <span className="text-stone-500 dark:text-stone-400">{apr.label_short}</span>
      <span className="text-stone-400 dark:text-stone-600">—</span>
      <span className="font-mono tabular-nums text-brand dark:text-amber-400">
        {valueText}
      </span>
      {apr.context ? (
        <>
          <span className="text-stone-400 dark:text-stone-600">·</span>
          <span className="text-stone-500 dark:text-stone-400">{apr.context}</span>
        </>
      ) : null}
    </span>
  );
}
