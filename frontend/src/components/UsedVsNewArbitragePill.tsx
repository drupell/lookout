import type { ArbitrageResponse } from "@/lib/schemas";

interface UsedVsNewArbitragePillProps {
  /**
   * Full arbitrage payload from `/me/used-vs-new-arbitrage`. `null` while the
   * dashboard's `Promise.allSettled` is in flight, after a soft failure, or
   * when the backend judged the spread "normal" (boring middle) and
   * suppressed the callout. In every case we render nothing — no flicker,
   * no placeholder, no perpetually-noisy pill.
   */
  data: ArbitrageResponse | null;
}

/**
 * "$32,450" — tabular-nums-friendly USD formatter. Whole dollars only;
 * the underlying medians are already rounded server-side and a cents
 * suffix would be noise at the pill's size.
 */
function formatUsd(value: number): string {
  return `$${Math.round(value).toLocaleString()}`;
}

/**
 * Used-vs-new arbitrage pill. Mirrors the visual treatment of
 * `AprSnapshotPill` (small inline rounded chip, mono tabular-nums on the
 * dollar value, muted descriptive copy) so it reads as ambient context
 * under the trend chart rather than another headline.
 *
 * The pill renders in two variants tied to the backend's `verdict`:
 *
 *   verdict="tight" — *"New within $X of used — captures unusual value"*
 *                     The dollar amount is the absolute spread (new minus
 *                     used). Brand honey-amber accent because this is the
 *                     editorially-interesting "new is the asymmetric call"
 *                     state worth surfacing.
 *
 *   verdict="wide"  — *"Used 1-2yr is $X under new — unusual gap"*
 *                     Same dollar amount, opposite sign. Quieter muted
 *                     accent because while the gap is notable it points
 *                     toward the conventional "used is cheaper" wisdom,
 *                     not an asymmetry.
 *
 * Brand voice: descriptive of the present, never predictive. The pill
 * reports what the market is doing right now; it does not say "MUST BUY"
 * or "BARGAIN ALERT" or anything else a financial-product UI would.
 */
export function UsedVsNewArbitragePill({ data }: UsedVsNewArbitragePillProps) {
  if (data == null) return null;

  const spread = Math.abs(data.spread_usd);
  const valueText = formatUsd(spread);

  // The aria-label collapses the visual into a single legible line for
  // screen readers — same idiom as AprSnapshotPill.
  const isTight = data.verdict === "tight";
  const description = isTight
    ? "captures unusual value"
    : "unusual gap";
  const leadText = isTight
    ? `New within ${valueText} of used`
    : `Used 1-2yr is ${valueText} under new`;
  const ariaLabel = `${leadText} — ${description}`;

  // Tone classes — honey-amber for tight (the editorial highlight),
  // stone-muted for wide (still worth surfacing, just not the headline).
  const valueClass = isTight
    ? "font-mono tabular-nums text-brand dark:text-amber-400"
    : "font-mono tabular-nums text-stone-700 dark:text-stone-300";

  return (
    <span
      role="status"
      aria-label={ariaLabel}
      data-verdict={data.verdict}
      className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-elevated px-2 py-1 text-xs dark:border-stone-800 dark:bg-stone-900"
    >
      <span className="text-stone-500 dark:text-stone-400">
        {isTight ? "New within" : "Used 1-2yr is"}
      </span>
      <span className={valueClass}>{valueText}</span>
      <span className="text-stone-500 dark:text-stone-400">
        {isTight ? "of used" : "under new"}
      </span>
      <span className="text-stone-400 dark:text-stone-600">·</span>
      <span className="text-stone-500 dark:text-stone-400">{description}</span>
    </span>
  );
}
