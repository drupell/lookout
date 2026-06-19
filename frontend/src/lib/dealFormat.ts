/**
 * Display helpers for the Deal record.
 *
 * Backend stores ~20 fields per deal; the dashboard composes them into a
 * handful of human-readable strings. Keeping the logic here means UI
 * components stay declarative.
 */
import type { Deal } from "./schemas";

/**
 * "2024 Tesla Model 3 Long Range" — graceful fallback when fields are missing.
 * Returns the listing_id only as a last resort.
 */
export function dealTitle(deal: Deal): string {
  const parts: string[] = [];
  if (deal.year) parts.push(String(deal.year));
  if (deal.make) parts.push(deal.make);
  if (deal.model) parts.push(deal.model);
  if (deal.trim) parts.push(deal.trim);
  return parts.length > 0 ? parts.join(" ") : deal.listing_id;
}

/**
 * Whole-percent discount off MSRP when MSRP is present and meaningful.
 * Returns null when MSRP is missing/zero/below selling price.
 */
export function discountPct(deal: Deal): number | null {
  if (deal.msrp === null || deal.msrp === undefined) return null;
  if (!deal.selling_price || deal.msrp <= deal.selling_price) return null;
  return Math.round(((deal.msrp - deal.selling_price) / deal.msrp) * 100);
}

/** "$34,500" / "—" */
export function formatPrice(value: number | undefined | null): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toLocaleString()}`;
}

/** "45,200 mi" / null */
export function formatMileage(mileage: number | undefined): string | null {
  if (!mileage) return null;
  return `${mileage.toLocaleString()} mi`;
}

/** "DealerName · 12 mi away" / "DealerName" / null */
export function formatDealer(deal: Deal): string | null {
  const parts: string[] = [];
  if (deal.dealer_name) parts.push(deal.dealer_name);
  if (deal.dealer_distance_miles && deal.dealer_distance_miles > 0) {
    parts.push(`${String(Math.round(deal.dealer_distance_miles))} mi away`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * Picks a Badge tone for an `overall_score` float on [0,1]. Aligned with the
 * default scoring threshold (0.5 in dev, 0.75 in prod) — not perfect but a
 * reasonable visual cue without plumbing per-user thresholds through.
 */
export function scoreTone(score: number | undefined): "success" | "brand" | "neutral" {
  if (score === undefined) return "neutral";
  if (score >= 0.75) return "success";
  if (score >= 0.5) return "brand";
  return "neutral";
}

/** "Score · 0.78" friendly. */
export function formatScore(score: number | undefined): string {
  if (score === undefined) return "—";
  return score.toFixed(2);
}

/** Weekday names indexed 0=Monday … 6=Sunday, matching the backend convention. */
const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

/** "Thursday" for weekday index 3. Falls back to "Thursday" for out-of-range. */
export function weekdayName(dayOfWeek: number | undefined): string {
  if (dayOfWeek === undefined || dayOfWeek < 0 || dayOfWeek > 6) return "Thursday";
  return WEEKDAYS[dayOfWeek] ?? "Thursday";
}

/**
 * Renders a `days_of_week` set (0=Mon … 6=Sun) as natural English, sorted and
 * deduped first so input order never leaks into copy:
 *   [0,1,2,3,4,5,6] → "Every day"
 *   [0,1,2,3,4]     → "Weekdays"
 *   [5,6]           → "Weekends"
 *   [3]             → "Thursdays"
 *   [0,3]           → "Mondays & Thursdays"
 *   [0,1,3]         → "Mondays, Tuesdays & Thursdays"
 * Returns "No days selected" for an empty/invalid set so the caller never
 * renders a bare, confusing string.
 */
export function formatDaysOfWeek(days: number[] | undefined): string {
  if (!Array.isArray(days)) return "No days selected";
  const clean = Array.from(new Set(days.filter((d) => d >= 0 && d <= 6))).sort((a, b) => a - b);
  if (clean.length === 0) return "No days selected";
  if (clean.length === 7) return "Every day";
  if (clean.length === 5 && clean.every((d) => d <= 4)) return "Weekdays";
  if (clean.length === 2 && clean[0] === 5 && clean[1] === 6) return "Weekends";

  const plurals = clean.map((d) => `${WEEKDAYS[d] ?? "Thursday"}s`);
  if (plurals.length === 1) return plurals[0] ?? "No days selected";
  // "Mondays & Thursdays" / "Mondays, Tuesdays & Thursdays" (Oxford-free join).
  const head = plurals.slice(0, -1).join(", ");
  const tail = plurals[plurals.length - 1];
  return `${head} & ${tail ?? ""}`;
}

/**
 * "Thu, May 29 · 1:00 PM UTC" — an unambiguous, timezone-explicit rendering of
 * a next-run timestamp. We force UTC so the displayed time always matches the
 * backend schedule regardless of the viewer's locale. Returns "Not scheduled"
 * for null/undefined and "—" for unparseable input.
 */
export function formatNextRun(iso: string | null | undefined): string {
  if (iso === null || iso === undefined || iso === "") return "Not scheduled";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const datePart = date.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
  const timePart = date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  });
  return `${datePart} · ${timePart} UTC`;
}

/**
 * Friendly relative line for a FUTURE timestamp: "in 4 days", "in 6 hours",
 * "soon". Returns null for null/unparseable input or anything in the past.
 */
export function relativeFuture(iso: string | null | undefined): string | null {
  if (iso === null || iso === undefined || iso === "") return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const diffMs = then.getTime() - Date.now();
  if (diffMs <= 0) return null;
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 60) return diffMin <= 1 ? "soon" : `in ${String(diffMin)} minutes`;
  const diffHours = Math.round(diffMs / 3600000);
  if (diffHours < 24) return `in ${String(diffHours)} hour${diffHours === 1 ? "" : "s"}`;
  const diffDays = Math.round(diffMs / 86400000);
  return `in ${String(diffDays)} day${diffDays === 1 ? "" : "s"}`;
}

/**
 * Compact relative time for a PAST timestamp: "just now", "6m ago", a
 * locale-formatted time ("6:02 AM") when same day, "2d ago" within a week,
 * or a locale-formatted time again for older. Used by both the "Last run"
 * Stat secondary line and the recent-run rows.
 */
export function relativeTime(iso: string): string {
  const then = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - then.getTime();
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${String(diffMin)}m ago`;

  const sameDay = then.toDateString() === now.toDateString();
  if (sameDay) {
    return then.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  const diffDays = Math.round(diffMs / 86400000);
  if (diffDays < 7) return `${String(diffDays)}d ago`;
  return then.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Just the UTC date — "Thu, May 29" — for inline copy like empty states. */
export function formatNextRunDate(iso: string | null | undefined): string | null {
  if (iso === null || iso === undefined || iso === "") return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
