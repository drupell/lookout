import { cn } from "@/components/ui";
import { relativeTime } from "@/lib/dealFormat";

/**
 * One sample of the market signal. Mirrors the schema Agent E is generating
 * on the API side: `composite_index` is the score in [-100, +100],
 * `factor_contribs` is a sparse map of factor-name → ±points contribution,
 * `variance_score` is the [0, 1] disagreement-between-factors width that
 * drives the halo, `flower_position` is the [-1, +1] derived slider position,
 * and `label` is the deterministic, never-predictive headline word.
 */
export interface MarketSignalPoint {
  composite_index: number;
  factor_contribs: Record<string, number>;
  variance_score: number;
  flower_position: number;
  label: "Now" | "Quiet" | "Not yet";
}

interface MarketSignalProps {
  /** Today's signal point. Null when no snapshots exist yet (true cold start). */
  latest: MarketSignalPoint | null;
  /** True when the user has <30d of personal history (still building baseline). */
  isColdStart?: boolean;
  /** ISO timestamp for the "as of …" caption. Omit to hide the caption. */
  asOfTimestamp?: string;
}

/**
 * Humanized factor labels. Backend names are stable snake_case; the chip row
 * needs the calmer editorial display form. Unknown names fall back to a
 * title-cased version of the raw key so a new factor never blanks the row.
 */
const FACTOR_LABELS: Record<string, string> = {
  calendar_pressure: "Calendar",
  auto_loan_apr_trend: "APR trend",
  discount_depth: "Discount",
  inventory_density: "Inventory",
  effective_price_trend: "Eff. price",
  incentive_prevalence: "Incentives",
};

function humanizeFactor(name: string): string {
  if (FACTOR_LABELS[name]) return FACTOR_LABELS[name];
  // Title-case the raw key as a graceful fallback so new factors still read.
  return name
    .split("_")
    .map((part) => {
      const head = part.charAt(0);
      return head.length > 0 ? head.toUpperCase() + part.slice(1) : part;
    })
    .join(" ");
}

/** "+8" / "-3" — the signed integer formatter used in the chip row. */
function signedInt(value: number): string {
  const rounded = Math.round(value);
  return rounded >= 0 ? `+${String(rounded)}` : String(rounded);
}

/** Clamp helper — we never let stray data push the marker outside the track. */
function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

/**
 * Derives the three flower morph states from the [-1, +1] position. The
 * thresholds match the central "Quiet" zone exactly so the headline word, the
 * flower state, and the slider position all switch in lock-step.
 */
type FlowerState = "bloom" | "bud" | "withered";
function flowerStateFor(position: number): FlowerState {
  if (position > 0.15) return "bloom";
  if (position < -0.15) return "withered";
  return "bud";
}

/**
 * The morphing flower — a single SVG that swaps `<path>` content based on
 * the current state so the transition reads as a morph rather than a swap.
 * Color tweens from `text-brand` (full bloom honey-amber) → `text-stone-400`
 * (withered, dimmed) via a Tailwind class change on the wrapping element.
 *
 * Decorative; the parent carries the accessible name.
 */
function Flower({ state, size = 28 }: { state: FlowerState; size?: number }) {
  const color =
    state === "bloom"
      ? "text-brand"
      : state === "bud"
        ? "text-amber-600 dark:text-amber-400"
        : "text-stone-400 dark:text-stone-500";

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={cn(
        "transition-colors duration-base ease-editorial",
        color,
      )}
    >
      {state === "bloom" ? (
        <g className="transition-all duration-base ease-editorial">
          {/* Five petals around a small honey center. The petal ellipses are
              evenly rotated; centered on (12, 12). */}
          <ellipse cx="12" cy="6" rx="2.4" ry="3.8" fill="currentColor" opacity="0.85" />
          <ellipse
            cx="12"
            cy="6"
            rx="2.4"
            ry="3.8"
            fill="currentColor"
            opacity="0.85"
            transform="rotate(72 12 12)"
          />
          <ellipse
            cx="12"
            cy="6"
            rx="2.4"
            ry="3.8"
            fill="currentColor"
            opacity="0.85"
            transform="rotate(144 12 12)"
          />
          <ellipse
            cx="12"
            cy="6"
            rx="2.4"
            ry="3.8"
            fill="currentColor"
            opacity="0.85"
            transform="rotate(216 12 12)"
          />
          <ellipse
            cx="12"
            cy="6"
            rx="2.4"
            ry="3.8"
            fill="currentColor"
            opacity="0.85"
            transform="rotate(288 12 12)"
          />
          {/* Honey center */}
          <circle cx="12" cy="12" r="1.9" fill="currentColor" />
        </g>
      ) : null}

      {state === "bud" ? (
        <g className="transition-all duration-base ease-editorial">
          {/* Closed teardrop bud — a single rounded shape sitting on a stem stub. */}
          <path
            d="M12 5 C 14.5 7 14.5 13 12 15 C 9.5 13 9.5 7 12 5 Z"
            fill="currentColor"
            opacity="0.78"
          />
          <path
            d="M12 15 V 19"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity="0.55"
          />
        </g>
      ) : null}

      {state === "withered" ? (
        <g className="transition-all duration-base ease-editorial">
          {/* Drooped petals — three closed ellipses tilted down. */}
          <ellipse
            cx="12"
            cy="12"
            rx="2"
            ry="3.4"
            fill="currentColor"
            opacity="0.55"
            transform="rotate(-40 12 12)"
          />
          <ellipse
            cx="12"
            cy="12"
            rx="2"
            ry="3.4"
            fill="currentColor"
            opacity="0.55"
            transform="rotate(20 12 12)"
          />
          <ellipse
            cx="12"
            cy="12"
            rx="2"
            ry="3.4"
            fill="currentColor"
            opacity="0.55"
            transform="rotate(60 12 12)"
          />
          {/* Drooped stem */}
          <path
            d="M12 14 C 13 17 14 18 16 18.5"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity="0.5"
            fill="none"
          />
        </g>
      ) : null}
    </svg>
  );
}

/**
 * Faint amber halo behind the flower. Radius scales linearly with the
 * [0, 1] variance: tight (~20px) when the factors agree, wide (~36px) when
 * they disagree — a pure visual confidence cue with no label.
 */
function VarianceHalo({ variance, size = 64 }: { variance: number; size?: number }) {
  const v = clamp(variance, 0, 1);
  const radius = 20 + v * 16; // 20 → 36
  const cx = size / 2;
  const cy = size / 2;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${String(size)} ${String(size)}`}
      aria-hidden="true"
      focusable="false"
      data-testid="variance-halo"
      data-halo-radius={radius.toFixed(2)}
      className="absolute inset-0 transition-all duration-base ease-editorial"
    >
      <circle cx={cx} cy={cy} r={radius} fill="rgb(176 110 26 / 0.12)" />
    </svg>
  );
}

/** Top-3 factor chips by |contribution|, formatted "Name +8 · Name -3". */
function topFactorChips(contribs: Record<string, number>): string[] {
  const entries = Object.entries(contribs).filter(
    ([, v]) => typeof v === "number" && Number.isFinite(v) && Math.round(v) !== 0,
  );
  entries.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return entries.slice(0, 3).map(([k, v]) => `${humanizeFactor(k)} ${signedInt(v)}`);
}

/**
 * The headline market signal: warm slider + morphing flower + variance halo
 * + factor chips. Never predictive — every word of copy is descriptive of
 * today's market. Cold-start (no snapshots, or `isColdStart`) renders the
 * marker at center with a bud and a "building your baseline" line in place
 * of the chip row, so the surface is never blank.
 */
export function MarketSignal({ latest, isColdStart = false, asOfTimestamp }: MarketSignalProps) {
  // Cold-start collapses everything to a neutral, mid-track presentation.
  const isCold = isColdStart || latest === null;
  const point: MarketSignalPoint = isCold
    ? {
        composite_index: 0,
        factor_contribs: {},
        variance_score: 0.35,
        flower_position: 0,
        label: "Quiet",
      }
    : latest;

  const position = clamp(point.flower_position, -1, 1);
  // [-1, +1] → [0%, 100%] across the track.
  const markerLeftPct = ((position + 1) / 2) * 100;
  const state = isCold ? "bud" : flowerStateFor(position);
  const showQuiet = Math.abs(position) < 0.15;
  const chips = isCold ? [] : topFactorChips(point.factor_contribs);

  // Accessible name describes today's state in one short sentence — never
  // predictive, mirrors the on-screen language.
  const ariaLabel = isCold
    ? "Market signal: building your baseline. The signal will become more specific after four weeks of runs."
    : `Market signal: ${point.label}. Composite index ${String(Math.round(point.composite_index))}.`;

  return (
    <div
      role="img"
      aria-label={ariaLabel}
      className="space-y-4"
    >
      {/* Endpoint labels + slider track */}
      <div className="space-y-2">
        <div className="flex items-baseline justify-between">
          <span className="font-serif text-xs uppercase tracking-wider text-stone-600 dark:text-stone-400">
            Now
          </span>
          {asOfTimestamp ? (
            <span className="text-2xs text-stone-500 dark:text-stone-500">
              as of {relativeTime(asOfTimestamp)}
            </span>
          ) : null}
          <span className="font-serif text-xs uppercase tracking-wider text-stone-600 dark:text-stone-400">
            Not yet
          </span>
        </div>

        {/* Track + marker + flower + halo */}
        <div className="relative">
          {/* Faint warm-gradient track. The light-mode gradient runs
              honey-amber → muted warm-gray; the dark-mode variant keeps the
              same "amber means favorable" semantics by inverting the muted
              end toward the warm-near-black end of our stone scale. */}
          <div
            className="h-1.5 w-full rounded-full bg-[linear-gradient(to_right,rgb(176_110_26_/_0.18),rgb(160_147_130_/_0.10))] dark:bg-[linear-gradient(to_right,rgb(214_142_43_/_0.22),rgb(51_41_31_/_0.40))]"
          />

          {/* "Quiet" middle label — only in the central zone, positioned
              above the marker. */}
          {showQuiet ? (
            <span
              className="pointer-events-none absolute -top-5 left-1/2 -translate-x-1/2 font-serif text-2xs uppercase italic tracking-widest text-stone-500 dark:text-stone-500"
              data-testid="quiet-label"
            >
              Quiet
            </span>
          ) : null}

          {/* Marker stack: halo + marker dot + flower below. We use absolute
              positioning + translate-x so the marker sits over its track
              percentage exactly, regardless of width. */}
          <div
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 transition-all duration-base ease-editorial"
            style={{ left: `${String(markerLeftPct)}%` }}
            data-testid="signal-marker"
            data-marker-left={markerLeftPct.toFixed(2)}
          >
            {/* Halo first so flower paints on top. */}
            <div className="relative h-16 w-16">
              <VarianceHalo variance={point.variance_score} size={64} />
              {/* Solid marker dot anchored to the track center. */}
              <span
                aria-hidden="true"
                className="absolute left-1/2 top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand shadow-xs dark:bg-amber-400"
              />
              {/* Flower sits below the dot in the vertical stack. */}
              <span className="absolute left-1/2 top-full -translate-x-1/2 translate-y-1">
                <Flower state={state} size={28} />
              </span>
            </div>
          </div>

          {/* Spacer to reserve vertical room for the marker stack below the
              track without disturbing the parent's flow. */}
          <div className="h-14" aria-hidden="true" />
        </div>
      </div>

      {/* Factor chips or cold-start hint */}
      {isCold ? (
        <p className="text-xs text-stone-500 dark:text-stone-400">
          Building your baseline — full signal kicks in after 4 weeks of runs.
        </p>
      ) : chips.length > 0 ? (
        <p
          className="text-xs text-stone-600 dark:text-stone-400"
          data-testid="factor-chips"
        >
          {chips.join(" · ")}
        </p>
      ) : null}

      {/* Education one-liner — always present, always the same words. */}
      <p className="text-xs text-stone-500 dark:text-stone-500">
        This signal describes today&apos;s market conditions. It is not a prediction.
      </p>
    </div>
  );
}
