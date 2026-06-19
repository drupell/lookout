"use client";

import { cn } from "@/components/ui";
import { relativeTime } from "@/lib/dealFormat";

import type { MarketSignalPoint } from "./MarketSignal";

export type MarketTrendWindow = "30d" | "90d" | "6m" | "12m" | "24m";

interface MarketTrendChartProps {
  /** Newest-first, as returned by the API. The chart re-orders internally. */
  points: MarketSignalPoint[];
  /** ISO timestamp for the "as of …" caption above the chart. */
  asOfTimestamp?: string;
  currentWindow: MarketTrendWindow;
  /**
   * The windows the user actually has enough history for. Anything not in
   * this list renders as a disabled pill with a "X weeks of history
   * available" tooltip — informative, never a dead end.
   */
  availableWindows: MarketTrendWindow[];
  onWindowChange: (w: MarketTrendWindow) => void;
}

/** Ordered set used both for pill rendering and the disabled-state tooltip. */
const WINDOWS: MarketTrendWindow[] = ["30d", "90d", "6m", "12m", "24m"];

/** Approximate-days lookup for the disabled-state tooltip. */
const WINDOW_DAYS: Record<MarketTrendWindow, number> = {
  "30d": 30,
  "90d": 90,
  "6m": 180,
  "12m": 365,
  "24m": 730,
};

/** Humanized chip names mirror MarketSignal's chip row so labels read the same. */
const FACTOR_LABELS: Record<string, string> = {
  calendar_pressure: "Calendar",
  auto_loan_apr_trend: "APR trend",
  discount_depth: "Discount",
  inventory_density: "Inventory",
  effective_price_trend: "Eff. price",
  incentive_prevalence: "Incentives",
};

/**
 * One short, plain-English explainer per factor — read by the expanded "Why
 * this signal?" disclosure. Warm/editorial, descriptive of *what the factor
 * measures*, not predictive of where it's headed.
 */
const FACTOR_EXPLAINERS: Record<string, string> = {
  calendar_pressure:
    "Where today sits in the buying calendar — end-of-month, end-of-quarter, and holiday windows usually carry more dealer movement.",
  auto_loan_apr_trend:
    "Today's 48-month new car APR vs the trailing 12-month average — when financing is more expensive than usual, the cost of waiting drops.",
  discount_depth:
    "How far today's typical matching listing is priced below MSRP, compared with the trailing 90-day median.",
  inventory_density:
    "How many matching listings are sitting on lots right now relative to the trailing 90-day average — thicker inventory tilts the floor lower.",
  effective_price_trend:
    "The direction of the median out-of-pocket price after netting trade-in and incentives — a slower-moving counterweight to discount depth.",
  incentive_prevalence:
    "How widely manufacturer and program incentives are stacking on matching listings today vs the baseline.",
};

function humanizeFactor(name: string): string {
  if (FACTOR_LABELS[name]) return FACTOR_LABELS[name];
  return name
    .split("_")
    .map((part) => {
      const head = part.charAt(0);
      return head.length > 0 ? head.toUpperCase() + part.slice(1) : part;
    })
    .join(" ");
}

function explainerFor(name: string): string {
  if (FACTOR_EXPLAINERS[name]) return FACTOR_EXPLAINERS[name];
  return "Contributes to today's composite signal.";
}

function signedInt(value: number): string {
  const rounded = Math.round(value);
  return rounded >= 0 ? `+${String(rounded)}` : String(rounded);
}

/** Clamp helper — keeps the line strictly inside the viewBox. */
function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

// ViewBox is fixed; the SVG scales via width=100%. Padding keeps the line
// off the edges so the stroke doesn't clip at extreme values.
const VIEW_W = 320;
const VIEW_H = 140;
const PAD_X = 8;
const PAD_Y = 14;

/** Map a composite index in [-100, +100] to a y-coordinate in the viewBox. */
function yFor(index: number): number {
  const v = clamp(index, -100, 100);
  // Higher index = closer to "Now" = visually higher on the chart.
  // Map +100 → top (PAD_Y), -100 → bottom (VIEW_H - PAD_Y).
  const frac = (100 - v) / 200;
  return PAD_Y + frac * (VIEW_H - 2 * PAD_Y);
}

/** Even x distribution across the chart width for `n` points. */
function xFor(i: number, n: number): number {
  if (n <= 1) return VIEW_W - PAD_X;
  const span = VIEW_W - 2 * PAD_X;
  return PAD_X + (i / (n - 1)) * span;
}

/** Hand-rolled SVG line chart of the composite-index timeseries. */
export function MarketTrendChart({
  points,
  asOfTimestamp,
  currentWindow,
  availableWindows,
  onWindowChange,
}: MarketTrendChartProps) {
  // API returns newest-first; the chart reads oldest→newest left→right.
  const ordered = [...points].reverse();
  const hasData = ordered.length > 0;
  // Today's point is the last (rightmost) point — what the chart calls out.
  const today = hasData ? ordered[ordered.length - 1] : null;
  const zeroY = yFor(0);

  // Build the line as straight segments; sparse data + clean stroke reads
  // honest. (Cubic beziers would over-smooth a monthly-cadence series.)
  const pathD = hasData
    ? ordered
        .map((p, i) => {
          const x = xFor(i, ordered.length);
          const y = yFor(p.composite_index);
          return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
        })
        .join(" ")
    : "";

  // For the "Why this signal?" detail panel, sort today's factors by absolute
  // magnitude so the most-influential row is on top.
  const factorRows: { name: string; value: number }[] = today
    ? Object.entries(today.factor_contribs)
        .filter(([, v]) => typeof v === "number" && Number.isFinite(v))
        .map(([name, value]) => ({ name, value }))
        .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    : [];

  return (
    <div className="space-y-3">
      {asOfTimestamp ? (
        <p className="text-2xs text-stone-500 dark:text-stone-500">
          as of {relativeTime(asOfTimestamp)}
        </p>
      ) : null}

      {/* Chart */}
      <div className="text-brand dark:text-amber-400" data-testid="trend-chart">
        <svg
          viewBox={`0 0 ${String(VIEW_W)} ${String(VIEW_H)}`}
          width="100%"
          height="auto"
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
          className="block"
        >
          {/* Zero-line — dashed warm hairline. The stroke color picks up
              our warm border palette so it sits softly in both modes. */}
          <line
            x1={PAD_X}
            x2={VIEW_W - PAD_X}
            y1={zeroY}
            y2={zeroY}
            stroke="rgb(232 224 210)"
            strokeDasharray="2 3"
            strokeWidth="1"
            className="dark:stroke-stone-700"
          />

          {/* Composite line */}
          {hasData ? (
            <path
              d={pathD}
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              fill="none"
              data-testid="trend-line"
            />
          ) : null}

          {/* Today's point — a small filled circle at the rightmost x. */}
          {hasData && today ? (
            <circle
              cx={xFor(ordered.length - 1, ordered.length).toFixed(2)}
              cy={yFor(today.composite_index).toFixed(2)}
              r="3"
              fill="currentColor"
              data-testid="today-point"
            />
          ) : null}
        </svg>

        {/* Empty-state caption — sits inside the chart card so the surface
            is never blank. */}
        {!hasData ? (
          <p className="-mt-6 text-center text-xs italic text-stone-500 dark:text-stone-500">
            Building your baseline
          </p>
        ) : null}
      </div>

      {/* Window pills */}
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Trend window">
        {WINDOWS.map((w) => {
          const active = w === currentWindow;
          const available = availableWindows.includes(w);
          const days = WINDOW_DAYS[w];
          const weeks = Math.max(1, Math.round(days / 7));
          return (
            <button
              key={w}
              type="button"
              role="tab"
              aria-selected={active}
              disabled={!available}
              title={
                available
                  ? `Show the last ${w}`
                  : `${String(weeks)} weeks of history available`
              }
              onClick={() => {
                if (available) onWindowChange(w);
              }}
              className={cn(
                "rounded-md px-2 py-0.5 font-mono text-xs transition-colors duration-fast ease-editorial",
                active
                  ? "bg-brand-subtle text-stone-900 dark:bg-amber-950/40 dark:text-stone-100"
                  : available
                    ? "text-stone-500 hover:text-stone-800 dark:text-stone-500 dark:hover:text-stone-200"
                    : "cursor-not-allowed text-stone-300 dark:text-stone-700",
              )}
            >
              {w}
            </button>
          );
        })}
      </div>

      {/* "Why this signal?" expandable card — mirrors the RunRow disclosure
          pattern: native <details> for accessibility + zero JS. */}
      {hasData && today ? (
        <details className="group rounded-md border border-stone-200 bg-stone-50/40 dark:border-stone-800 dark:bg-stone-900/30">
          <summary className="focus-ring flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-xs text-stone-600 transition-colors duration-fast hover:text-stone-900 marker:hidden [&::-webkit-details-marker]:hidden dark:text-stone-400 dark:hover:text-stone-100">
            <span>Why this signal?</span>
            <span
              aria-hidden="true"
              className="text-stone-400 transition-transform duration-fast ease-editorial group-open:rotate-90"
            >
              ▸
            </span>
          </summary>

          <div className="border-t border-stone-200 px-3 py-3 dark:border-stone-800">
            <p className="mb-3 text-xs leading-relaxed text-stone-600 dark:text-stone-400">
              Today&apos;s composite index is{" "}
              <span className="font-mono tabular-nums text-stone-800 dark:text-stone-200">
                {signedInt(today.composite_index)}
              </span>
              . The factors below show how each part of the read is leaning
              right now.
            </p>
            <dl className="space-y-2 text-xs">
              {factorRows.map((row) => (
                <div key={row.name} className="grid grid-cols-[auto_1fr] gap-x-3">
                  <dt className="flex items-baseline gap-2">
                    <span className="font-medium text-stone-800 dark:text-stone-200">
                      {humanizeFactor(row.name)}
                    </span>
                    <span className="font-mono tabular-nums text-stone-600 dark:text-stone-400">
                      {signedInt(row.value)}
                    </span>
                  </dt>
                  <dd className="text-stone-500 dark:text-stone-500">
                    {explainerFor(row.name)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </details>
      ) : null}
    </div>
  );
}
