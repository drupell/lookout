/**
 * Local preview mode — dev-only, gated by NEXT_PUBLIC_PREVIEW=1 (set in
 * .env.local, never in a real build). When on, auth is bypassed with a mock
 * user and the API client returns the fixtures below instead of calling the
 * backend, so the whole UI can be reviewed locally without deploying anything.
 *
 * Fixtures are validated against the real Zod schemas at the call site
 * (api.ts runs `schema.parse` on them), so a drifting fixture fails loudly.
 */
import type { CurrentUser } from "./auth";

// Hard-gated to non-production: `next build` sets NODE_ENV=production, so a
// real/deployed build ALWAYS ignores this flag even if .env.local leaks in.
// Only `next dev` (NODE_ENV=development) can turn preview on.
export const isPreview =
  process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_PREVIEW === "1";

export const PREVIEW_USER: CurrentUser = {
  userId: "preview-user",
  email: "you@preview.local",
};

type Method = "GET" | "POST" | "PUT" | "DELETE";

const me = {
  user_id: "preview-user",
  email: "you@preview.local",
  tier: "default",
  tier_caps: {
    schedule_editable: false,
    pagination_editable: false,
    scoring_thresholds_editable: false,
    max_pages: 1,
    target_listings: 50,
    fixed_schedule_note: "Default tier runs weekly. Upgrade to BYOK to customize.",
  },
  marketcheck_secret_configured: false,
  created_at: "2026-04-01T12:00:00Z",
  updated_at: "2026-05-18T09:30:00Z",
  last_run_at: "2026-05-20T06:00:00Z",
  // A future Thursday at 13:00 UTC — populates the "Next run" UI in dev.
  next_run_at: "2026-05-28T13:00:00Z",
};

const preferences = {
  vehicle: { mileage: 14000, condition: "used", trade_in_floor_usd: 18000 },
  search: {
    location_zip: "02139",
    radius_miles: 75,
    max_vehicle_age_years: 3,
    body_styles: ["SUV", "Sedan"],
    fuel_types: ["EV"],
    min_price_usd: 0,
    max_price_usd: 48000,
    max_mileage_miles: 40000,
    target_listings: 50,
    max_pages: 1,
  },
  included_brands: [],
  excluded_brands: ["Tesla"],
  excluded_models: [],
  deal_criteria: {
    max_effective_monthly_usd: 520,
    min_discount_off_msrp_pct: 8,
    acceptable_apr_max: 4.9,
    lease_to_own_preferred: true,
    zero_percent_financing_preferred: true,
  },
  scoring: { threshold_notify: 0.7, threshold_draft_email: 0.82 },
  schedule: { days_of_week: [3], time_of_day_utc: "13:00" },
};

function deal(
  i: number,
  make: string,
  model: string,
  trim: string,
  year: number,
  msrp: number,
  selling: number,
  score: number,
  reasoning: string,
  extra: Record<string, unknown> = {},
) {
  return {
    listing_id: `preview-${String(i)}`,
    first_seen: "2026-05-20T06:00:00Z",
    run_id: "preview-run-2",
    status: "NEW",
    overall_score: score,
    above_threshold: score >= 0.7,
    score_breakdown: {
      price_score: Math.min(1, score + 0.05),
      financing_score: Math.max(0, score - 0.1),
      incentive_score: Math.max(0, score - 0.05),
      preference_match_score: score,
      reasoning,
    },
    effective_out_of_pocket_usd: selling - 7500,
    trade_in_value_at_scoring: 18000,
    applicable_incentives: ["$7,500 federal credit"],
    make,
    model,
    trim,
    year,
    mileage: 9000 + i * 1500,
    msrp,
    selling_price: selling,
    fuel_type: "EV",
    powertrain_type: "BEV",
    body_type: "SUV",
    dealer_name: "Greenline Motors",
    dealer_distance_miles: 12 + i * 4,
    url: "https://example.com/listing",
    ...extra,
  };
}

const deals = [
  deal(1, "Hyundai", "Ioniq 5", "SEL AWD", 2024, 52000, 43200, 0.91,
    "Strong discount, 0% APR available, and the federal credit applies at point of sale — a standout on effective monthly cost.",
    { is_favorite: true }),
  deal(2, "Kia", "EV6", "Wind", 2024, 51500, 44100, 0.84,
    "Comfortably under your monthly target with a healthy discount; financing is good but not the headline 0%."),
  deal(3, "Ford", "Mustang Mach-E", "Premium", 2023, 49900, 41800, 0.78,
    "Great price-to-MSRP and within range; slightly higher mileage than ideal."),
  deal(4, "Chevrolet", "Blazer EV", "2LT", 2024, 50300, 45600, 0.72,
    "Meets your criteria but the discount is modest and APR is at the edge of acceptable."),
  deal(5, "Volkswagen", "ID.4", "Pro S", 2023, 46100, 39900, 0.68,
    "Just under the notify threshold — decent value but preference match is weaker on body style."),
];

// Distinguishable full UUIDs (not just "preview-run-N") so the disclosure
// body shows something meaningful when expanded — and so the Copy button
// has a realistic payload to paste during local QA.
const runs = [
  {
    run_id: "3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e",
    user_id: "preview-user",
    status: "SUCCESS",
    started_at: "2026-05-20T06:00:00Z",
    finished_at: "2026-05-20T06:02:11Z",
    listings_seen: 48,
    deals_kept: 5,
    deals_found: 41,
    deals_above_threshold: 8,
    drafts_produced: 5,
  },
  {
    run_id: "9c12d4e6-5a7b-4f3c-8b21-1c2f0e6a7d9b",
    user_id: "preview-user",
    status: "GUARDRAIL_BLOCKED",
    started_at: "2026-05-13T06:00:00Z",
    finished_at: "2026-05-13T06:00:34Z",
    listings_seen: 0,
    deals_kept: 0,
    guardrail_triggers: ["score_deals"],
  },
  {
    run_id: "ab38e019-6d52-4c8a-9f04-7e89bb1c3a55",
    user_id: "preview-user",
    status: "SUCCESS",
    started_at: "2026-05-06T06:00:00Z",
    finished_at: "2026-05-06T06:00:12Z",
    listings_seen: 0,
    deals_kept: 0,
    deals_found: 0,
    deals_above_threshold: 0,
    drafts_produced: 0,
    source_errors: ["marketcheck:rate_limited"],
  },
];

const favorites = [
  {
    user_id: "preview-user",
    listing_id: "preview-1",
    signal: "FAVORITE",
    created_at: "2026-05-20T07:15:00Z",
    deal: deals[0],
  },
];

// --- Market signal fixture ---------------------------------------------------
//
// 12 weeks of synthetic snapshots, newest-first. Hand-tuned (not random) so
// the dashboard preview tells a readable story:
//   - a soft "Not yet" stretch in the deeper past,
//   - a quiet middle,
//   - a visible end-of-month spike where calendar_pressure dominates,
//   - landing on a current "Now" reading.
//
// Each row sums its `factor_contribs` to roughly equal `composite_index` so the
// chart's factor chips don't lie about where the headline came from.

function buildMarketSignalPoint(
  weeksAgo: number,
  composite: number,
  variance: number,
  factors: Record<string, number>,
): {
  timestamp: string;
  composite_index: number;
  factor_contribs: Record<string, number>;
  variance_score: number;
  flower_position: number;
  label: "Now" | "Quiet" | "Not yet";
} {
  // Newest-first: weeksAgo=0 is "now-ish". Anchor on a fixed date so the
  // preview is deterministic across local runs.
  const anchor = new Date("2026-05-25T06:00:00Z").getTime();
  const ts = new Date(anchor - weeksAgo * 7 * 24 * 60 * 60 * 1000).toISOString();
  const label: "Now" | "Quiet" | "Not yet" =
    composite >= 15 ? "Now" : composite <= -15 ? "Not yet" : "Quiet";
  return {
    timestamp: ts,
    composite_index: composite,
    factor_contribs: factors,
    variance_score: variance,
    // Position in [-1, 1]; the flower visual maps this to a slider x-coord.
    flower_position: composite / 100,
    label,
  };
}

const marketSignalPoints = [
  // newest -> oldest
  buildMarketSignalPoint(0, 22, 0.18, {
    calendar_pressure: 14,
    discount_depth: 6,
    inventory_density: 3,
    effective_price_trend: -2,
    incentive_prevalence: 1,
  }),
  buildMarketSignalPoint(1, 12, 0.22, {
    calendar_pressure: 5,
    discount_depth: 5,
    inventory_density: 2,
    effective_price_trend: 0,
    incentive_prevalence: 0,
  }),
  buildMarketSignalPoint(2, 4, 0.28, {
    calendar_pressure: 1,
    discount_depth: 3,
    inventory_density: 1,
    effective_price_trend: 0,
    incentive_prevalence: -1,
  }),
  buildMarketSignalPoint(3, -2, 0.31, {
    calendar_pressure: -1,
    discount_depth: 1,
    inventory_density: -1,
    effective_price_trend: -1,
    incentive_prevalence: 0,
  }),
  buildMarketSignalPoint(4, 18, 0.35, {
    calendar_pressure: 10,
    discount_depth: 4,
    inventory_density: 2,
    effective_price_trend: 1,
    incentive_prevalence: 1,
  }),
  buildMarketSignalPoint(5, 6, 0.42, {
    calendar_pressure: 0,
    discount_depth: 4,
    inventory_density: 1,
    effective_price_trend: 0,
    incentive_prevalence: 1,
  }),
  buildMarketSignalPoint(6, -8, 0.39, {
    calendar_pressure: -2,
    discount_depth: -3,
    inventory_density: -2,
    effective_price_trend: -1,
    incentive_prevalence: 0,
  }),
  buildMarketSignalPoint(7, -14, 0.46, {
    calendar_pressure: -3,
    discount_depth: -5,
    inventory_density: -3,
    effective_price_trend: -3,
    incentive_prevalence: 0,
  }),
  buildMarketSignalPoint(8, -18, 0.52, {
    calendar_pressure: -4,
    discount_depth: -6,
    inventory_density: -4,
    effective_price_trend: -4,
    incentive_prevalence: 0,
  }),
  buildMarketSignalPoint(9, -10, 0.48, {
    calendar_pressure: -3,
    discount_depth: -4,
    inventory_density: -2,
    effective_price_trend: -1,
    incentive_prevalence: 0,
  }),
  buildMarketSignalPoint(10, 2, 0.41, {
    calendar_pressure: 1,
    discount_depth: 2,
    inventory_density: 0,
    effective_price_trend: -1,
    incentive_prevalence: 0,
  }),
  buildMarketSignalPoint(11, 9, 0.36, {
    calendar_pressure: 3,
    discount_depth: 4,
    inventory_density: 1,
    effective_price_trend: 0,
    incentive_prevalence: 1,
  }),
];

const marketSignal = {
  window: "90d",
  view: "personalized",
  points: marketSignalPoints,
  latest: marketSignalPoints[0] ?? null,
};

// --- API usage fixture -------------------------------------------------------
//
// Default tier, mid-month, comfortably under the warn threshold so the
// QuotaBanner stays hidden in the standard preview. Set ?approaching=1 to
// flip approaching_limit=true (412/500) for QA of the banner.
function currentYyyyMm(): string {
  const d = new Date();
  return `${String(d.getUTCFullYear())}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function buildUsage(approaching: boolean): {
  marketcheck: {
    tier: "default" | "byok";
    api_key_id: string;
    calls: number;
    limit: number | null;
    yyyy_mm: string;
    pct_used: number | null;
    approaching_limit: boolean;
  };
} {
  const calls = approaching ? 412 : 312;
  return {
    marketcheck: {
      tier: "default",
      api_key_id: "shared",
      calls,
      limit: 500,
      yyyy_mm: currentYyyyMm(),
      pct_used: (calls / 500) * 100,
      approaching_limit: approaching,
    },
  };
}

// --- Macro snapshot fixture --------------------------------------------------
//
// Hand-tuned to mirror a believable mid-2026 macro read: 48-mo new car APR at
// 7.36% (near the multi-year peak — surfaces the "24-month high" context
// label) and used-car CPI softening modestly month-over-month with a small
// year-over-year gain. The shape exercises every branch of `AprSnapshotPill`
// and leaves room for a future CPI pill to land without a fixture bump.

const macroSnapshot = {
  auto_loan_apr: {
    value_pct: 7.36,
    as_of: "2026-05-01T00:00:00+00:00",
    label_short: "48-mo new car APR",
    context: "24-month high",
  },
  cpi_used_cars: {
    value: 160.2,
    as_of: "2026-04-01T00:00:00+00:00",
    mom_change_pct: -0.4,
    yoy_change_pct: 2.1,
  },
};

// --- Used-vs-new arbitrage fixture -------------------------------------------
//
// "Tight" verdict — used 1-2yr Ioniq 5 examples are selling within $2,150 of
// new (~6.4% by ratio). Exercises the brand-amber tight branch of the pill.
// Flip `verdict` to "wide" or set the whole export to `null` for the other
// two states the component handles.

const arbitrage = {
  spread_pct: 6.4,
  spread_usd: 2150,
  median_new_usd: 33500,
  median_used_usd: 31350,
  label_short: "Used 1-2yr",
  verdict: "tight" as const,
  as_of: "2026-05-25T06:00:00+00:00",
};

// --- Incentive stack fixture -------------------------------------------------
//
// A representative MA-flavored stack: federal $7,500 Section 30D + MA MOR-EV
// $2,500 + Eversource utility $1,500, with a single funding-low warning for
// the state program. Hand-tuned to exercise every visual branch of the card
// (federal "qualify by income" suffix, state/utility "stacks" suffix, the
// amber warning row) so a local preview round-trips through the full design.

const incentiveStack = {
  stack: {
    total_usd: 11500,
    items: [
      {
        id: "federal_30d",
        title: "Federal Section 30D EV Credit",
        amount_usd: 7500,
        jurisdiction: "federal",
        expiration_date: "2032-12-31T00:00:00+00:00",
        stacks: true,
        notes: "Point-of-sale credit at participating dealers.",
      },
      {
        id: "ma_morev",
        title: "MA MOR-EV",
        amount_usd: 2500,
        jurisdiction: "state",
        expiration_date: null,
        stacks: true,
        notes: "Massachusetts state rebate for new BEVs.",
      },
      {
        id: "eversource_charge_now",
        title: "Eversource EV Charge Now",
        amount_usd: 1500,
        jurisdiction: "utility",
        expiration_date: null,
        stacks: true,
        notes: "Utility rebate for home charger install.",
      },
    ],
  },
  warnings: ["MA MOR-EV funding running low — expected exhaustion in ~6 weeks"],
};

// --- Inventory anomaly fixture -----------------------------------------------
//
// "Thick" verdict — 42 matching listings nearby vs a trailing baseline of 18
// (≈2.3×). Exercises the brand-amber thick branch of the pill. Flip
// `verdict` to "thin" with `current_count: 5, baseline_count: 22` for the
// other variant; set the whole export to `null` to preview the suppressed
// (normal) state.

const inventoryAnomaly = {
  current_count: 42,
  baseline_count: 18,
  ratio: 2.33,
  verdict: "thick" as const,
  as_of: "2026-05-25T06:00:00+00:00",
};

/**
 * Returns the canned response for a (path, method) pair. Throws on an
 * unmapped route so a missing fixture surfaces as a clear error in the UI
 * rather than a confusing blank.
 */
export function previewFixture(path: string, method: Method): unknown {
  if (path === "/me") return me;
  if (path === "/me/prefs") {
    return { tier: "default", effective: preferences, user_overrides: {}, writable_paths: [] };
  }
  if (path === "/me/runs") {
    if (method === "POST") {
      return { status: "ACCEPTED", message_id: "preview-msg", user_id: "preview-user" };
    }
    return { runs };
  }
  if (path === "/me/runs/in_flight") return { in_flight: false };
  if (path === "/me/deals") return { deals };
  if (path === "/me/favorites") return { favorites };
  if (path === "/me/byok-key") {
    return { tier: "default", marketcheck_secret_configured: method !== "DELETE" };
  }
  if (path === "/me/signal" || path.startsWith("/me/signal?")) {
    return marketSignal;
  }
  if (path === "/me/incentives" || path.startsWith("/me/incentives?")) {
    return incentiveStack;
  }
  if (path === "/me/macro" || path.startsWith("/me/macro?")) {
    return macroSnapshot;
  }
  if (
    path === "/me/used-vs-new-arbitrage" ||
    path.startsWith("/me/used-vs-new-arbitrage?")
  ) {
    return arbitrage;
  }
  if (
    path === "/me/inventory-anomaly" ||
    path.startsWith("/me/inventory-anomaly?")
  ) {
    return inventoryAnomaly;
  }
  if (path === "/me/usage" || path.startsWith("/me/usage?")) {
    // ?approaching=1 in the URL flips the banner-trigger fixture on for QA.
    const approaching =
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("approaching") === "1";
    return buildUsage(approaching);
  }
  if (path.startsWith("/me/deals/")) {
    const id = path.split("/")[3] ?? "preview-1";
    if (path.endsWith("/favorite")) {
      return method === "DELETE"
        ? { listing_id: id, removed: true }
        : { listing_id: id, signal: "FAVORITE" };
    }
    if (path.endsWith("/act")) return { listing_id: id, status: "ACTED" };
  }
  throw new Error(`Preview: no fixture for ${method} ${path}`);
}
