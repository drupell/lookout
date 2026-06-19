import type { InventoryAnomalyResponse } from "@/lib/schemas";

interface InventoryAnomalyPillProps {
  /**
   * Full payload from `/me/inventory-anomaly`. `null` while the dashboard's
   * `Promise.allSettled` is in flight, after a soft failure, or when the
   * backend judged the listing count "normal" (boring middle) and
   * suppressed the callout. In every case we render nothing — no flicker,
   * no placeholder, no perpetually-noisy pill.
   */
  data: InventoryAnomalyResponse | null;
}

/**
 * Format the ratio into the editorial "≈Nx usual" / "≈half usual" / "≈Mx
 * fewer than usual" blurb the pill reads.
 *
 * The backend already classifies; this function is purely cosmetic — it
 * picks the human-readable framing for the ratio the backend handed us so
 * the verdict and the number agree at a glance. We avoid printing literal
 * "0.5x" / "1.7x" — those are accurate but read like a spreadsheet.
 */
function ratioBlurb(ratio: number, verdict: "thick" | "thin"): string {
  if (verdict === "thin") {
    // ratio < 1 here by construction. Frame as "≈half usual" / "≈third usual"
    // / "≈quarter usual" by reciprocal — reads warmer than "0.4× usual."
    if (ratio === 0) return "almost none";
    const reciprocal = 1 / ratio;
    if (reciprocal >= 3.5) return "≈quarter usual";
    if (reciprocal >= 2.5) return "≈third usual";
    return "≈half usual";
  }
  // verdict === "thick", ratio > 1.8 by construction. "2× usual" reads
  // cleaner than "1.83× usual" — round to integers above 1.5 and clamp
  // the long tail at 5× since anything beyond that is more "rare event"
  // than "calibrated metric."
  const rounded = Math.min(5, Math.round(ratio));
  return `≈${String(rounded)}× usual`;
}

/**
 * Inventory anomaly pill. Mirrors the visual treatment of `AprSnapshotPill`
 * and `UsedVsNewArbitragePill` (small inline rounded chip, mono tabular-nums
 * on the integer count, muted descriptive copy) so it reads as ambient
 * context under the trend chart rather than another headline.
 *
 * Two variants tied to the backend's `verdict`:
 *
 *   verdict="thick" — *"Inventory — 42 nearby · ≈2× usual"*
 *                     Brand honey-amber accent because thicker inventory
 *                     usually means a calendar-driven push by dealers; this
 *                     is the editorially-interesting buyer-leverage state.
 *
 *   verdict="thin"  — *"Inventory — 8 nearby · ≈half usual"*
 *                     Muted accent because the framing is more
 *                     "the market is tightening" — worth knowing, but the
 *                     visual shouldn't dominate the row.
 *
 * Brand voice: descriptive of the present, never predictive. The pill
 * reports what the user's matching inventory looks like right now; it
 * does not say "BUY NOW" or "ACT FAST" or anything else a high-pressure
 * financial UI would.
 */
export function InventoryAnomalyPill({ data }: InventoryAnomalyPillProps) {
  if (data == null) return null;

  const countText = data.current_count.toLocaleString();
  const blurb = ratioBlurb(data.ratio, data.verdict);
  const isThick = data.verdict === "thick";

  // The aria-label collapses the pill into one legible line for screen
  // readers — same idiom as AprSnapshotPill / UsedVsNewArbitragePill.
  const ariaLabel = `Inventory — ${countText} matching listings nearby — ${blurb}`;

  // Tone classes — honey-amber for thick (the editorial highlight),
  // stone-muted for thin (still worth surfacing, just not the headline).
  const valueClass = isThick
    ? "font-mono tabular-nums text-brand dark:text-amber-400"
    : "font-mono tabular-nums text-stone-700 dark:text-stone-300";

  return (
    <span
      role="status"
      aria-label={ariaLabel}
      data-verdict={data.verdict}
      className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-elevated px-2 py-1 text-xs dark:border-stone-800 dark:bg-stone-900"
    >
      <span className="text-stone-500 dark:text-stone-400">Inventory</span>
      <span className="text-stone-400 dark:text-stone-600">—</span>
      <span className={valueClass}>{countText}</span>
      <span className="text-stone-500 dark:text-stone-400">nearby</span>
      <span className="text-stone-400 dark:text-stone-600">·</span>
      <span className="text-stone-500 dark:text-stone-400">{blurb}</span>
    </span>
  );
}
