/**
 * Zod schemas mirroring the backend Pydantic models.
 *
 * Source of truth is `src/config/loader.py` and `src/api/_user_repo.py`.
 * Keep these in sync — when a backend field changes, mirror it here so the
 * dashboard fails loudly at parse time instead of silently rendering stale UI.
 */
import { z } from "zod";

// ---- Preferences (mirror src/config/loader.py) ----

export const VehicleConfigSchema = z.object({
  vin: z.string().optional(),
  mileage: z.number().int().nonnegative().optional(),
  condition: z.string().optional(),
  trade_in_floor_usd: z.number().nonnegative().optional(),
});

export const SearchConfigSchema = z.object({
  location_zip: z.string(),
  radius_miles: z.number().int().positive(),
  max_vehicle_age_years: z.number().int().positive(),
  body_styles: z.array(z.string()),
  fuel_types: z.array(z.string()),
  min_price_usd: z.number().nonnegative(),
  // Backend allows 0 as "no upper bound" — match that here so prefs from a
  // user who hasn't set a max don't fail zod parse.
  max_price_usd: z.number().nonnegative(),
  // Same 0 = no cap convention as prices.
  max_mileage_miles: z.number().int().nonnegative(),
  target_listings: z.number().int().positive(),
  max_pages: z.number().int().positive(),
});

export const DealCriteriaSchema = z.object({
  max_effective_monthly_usd: z.number().positive(),
  min_discount_off_msrp_pct: z.number().min(0).max(100),
  acceptable_apr_max: z.number().min(0).max(100),
  lease_to_own_preferred: z.boolean(),
  zero_percent_financing_preferred: z.boolean(),
});

export const ScoringConfigSchema = z.object({
  threshold_notify: z.number().min(0).max(1),
  threshold_draft_email: z.number().min(0).max(1),
});

export const ScheduleConfigSchema = z.object({
  // Which weekdays the agent runs on: a non-empty subset of 0–6 where
  // 0=Monday … 3=Thursday (default) … 6=Sunday. [3]=weekly Thursday,
  // [0,3]=Mondays & Thursdays, [0,1,2,3,4,5,6]=every day, [5,6]=weekends.
  days_of_week: z.array(z.number().int().min(0).max(6)).min(1),
  // The UTC time the run fires on each selected day, "HH:MM" 24h.
  time_of_day_utc: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
});

export const PreferencesSchema = z.object({
  vehicle: VehicleConfigSchema,
  search: SearchConfigSchema,
  included_brands: z.array(z.string()),
  excluded_brands: z.array(z.string()),
  excluded_models: z.array(z.string()),
  deal_criteria: DealCriteriaSchema,
  scoring: ScoringConfigSchema,
  schedule: ScheduleConfigSchema,
});

export type Preferences = z.infer<typeof PreferencesSchema>;

// ---- API responses ----

export const TierSchema = z.enum(["default", "byok"]);
export type Tier = z.infer<typeof TierSchema>;

export const TierCapsSchema = z.object({
  schedule_editable: z.boolean(),
  pagination_editable: z.boolean(),
  scoring_thresholds_editable: z.boolean(),
  max_pages: z.number().optional(),
  target_listings: z.number().optional(),
  fixed_schedule_note: z.string().optional(),
});

export const MeResponseSchema = z.object({
  user_id: z.string(),
  email: z.string(),
  tier: TierSchema,
  tier_caps: TierCapsSchema,
  marketcheck_secret_configured: z.boolean(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  // ISO-8601, or null if the user has never run. Compared against updated_at
  // by the dashboard to detect "settings changed since last run".
  last_run_at: z.string().nullable().optional(),
  // ISO-8601 (UTC), or null if no run is scheduled. The user's next scheduled
  // run; surfaced on the dashboard and settings so they know when fresh
  // listings will land.
  next_run_at: z.string().nullable().optional(),
});
export type MeResponse = z.infer<typeof MeResponseSchema>;

export const ByokKeyResponseSchema = z.object({
  tier: TierSchema,
  marketcheck_secret_configured: z.boolean(),
});
export type ByokKeyResponse = z.infer<typeof ByokKeyResponseSchema>;

export const PrefsResponseSchema = z.object({
  tier: TierSchema,
  effective: PreferencesSchema,
  user_overrides: z.record(z.unknown()),
  writable_paths: z.array(z.string()),
});
export type PrefsResponse = z.infer<typeof PrefsResponseSchema>;

// Permissive: the backend emits IN_PROGRESS, RUNNING, SUCCESS, ERROR,
// GUARDRAIL_BLOCKED, and PARTIAL. Keep this a plain string so a new status
// added on the backend doesn't break /me/runs parsing and blank the dashboard.
export const RunStatusSchema = z.string();

export const RunSummarySchema = z
  .object({
    run_id: z.string(),
    user_id: z.string().optional(),
    status: RunStatusSchema.optional(),
    started_at: z.string().optional(),
    finished_at: z.string().optional(),
    listings_seen: z.number().int().optional(),
    deals_kept: z.number().int().optional(),
    // Per-run counts the backend already returns (via passthrough); declared
    // here for type safety so RunRow can use them without `as` casts.
    deals_found: z.number().int().nonnegative().optional(),
    deals_above_threshold: z.number().int().nonnegative().optional(),
    drafts_produced: z.number().int().nonnegative().optional(),
    // Names of nodes whose guardrails blocked the run, e.g. ["score_deals"].
    // The backend projects from a richer persisted forensic shape; if a row
    // predates that projection it'll fail this declaration and get dropped by
    // RunsResponseSchema rather than blanking the whole list.
    guardrail_triggers: z.array(z.string()).optional(),
    // Upstream-data failures, e.g. ["marketcheck:rate_limited"]. Surfaced
    // by the dashboard so users know "0 deals" wasn't caused by criteria.
    source_errors: z.array(z.string()).optional(),
  })
  .passthrough();
export type RunSummary = z.infer<typeof RunSummarySchema>;

// Per-row tolerance: parse each run individually and drop ones that fail
// declared-field types. A single row with a stale shape (e.g. legacy
// `guardrail_triggers: [{node, errors, ...}]`) should not blank the whole
// "Recent runs" list — the rest of the rows are still useful.
export const RunsResponseSchema = z.object({
  runs: z.array(z.unknown()).transform((items): RunSummary[] => {
    const out: RunSummary[] = [];
    for (const item of items) {
      const parsed = RunSummarySchema.safeParse(item);
      if (parsed.success) out.push(parsed.data);
    }
    return out;
  }),
});

export const InFlightRunSchema = z.discriminatedUnion("in_flight", [
  z.object({ in_flight: z.literal(false) }),
  z.object({
    in_flight: z.literal(true),
    run_id: z.string().optional(),
    started_at: z.string().optional(),
    current_node: z.string().optional(),
    progress: z
      .object({
        completed: z.number().int().nonnegative(),
        total: z.number().int().positive(),
      })
      .optional(),
  }),
]);
export type InFlightRun = z.infer<typeof InFlightRunSchema>;

export const DealStatusSchema = z.enum(["NEW", "NOTIFIED", "ACTED", "EXPIRED"]);

/** Sub-scores + reasoning produced by the LLM scoring node. */
export const ScoreBreakdownSchema = z
  .object({
    price_score: z.number().min(0).max(1).optional(),
    financing_score: z.number().min(0).max(1).optional(),
    incentive_score: z.number().min(0).max(1).optional(),
    preference_match_score: z.number().min(0).max(1).optional(),
    reasoning: z.string().optional(),
  })
  .passthrough();
export type ScoreBreakdown = z.infer<typeof ScoreBreakdownSchema>;

/**
 * Mirrors the shape persisted by `src/memory/deal_store.py`. Keep in sync —
 * passthrough() lets new fields land without breaking parsing, but anything
 * the UI renders should be declared here so we get type safety.
 */
export const DealSchema = z
  .object({
    listing_id: z.string(),
    first_seen: z.string(),
    user_id: z.string().optional(),
    status: DealStatusSchema.optional(),
    // Scoring
    overall_score: z.number().min(0).max(1).optional(),
    above_threshold: z.boolean().optional(),
    score_breakdown: ScoreBreakdownSchema.optional(),
    effective_out_of_pocket_usd: z.number().optional(),
    trade_in_value_at_scoring: z.number().optional(),
    applicable_incentives: z.array(z.string()).optional(),
    // Vehicle
    make: z.string().optional(),
    model: z.string().optional(),
    trim: z.string().optional(),
    year: z.number().int().optional(),
    mileage: z.number().int().optional(),
    msrp: z.number().nullable().optional(),
    selling_price: z.number().optional(),
    fuel_type: z.string().optional(),
    powertrain_type: z.string().optional(),
    body_type: z.string().optional(),
    // Dealer
    dealer_name: z.string().optional(),
    dealer_distance_miles: z.number().optional(),
    url: z.string().optional(),
    // Set by the deals API from the user's favorites — drives the heart icon.
    is_favorite: z.boolean().optional(),
  })
  .passthrough();
export type Deal = z.infer<typeof DealSchema>;

export const DealsResponseSchema = z.object({
  deals: z.array(DealSchema),
});

// ---- Favorites (mirror src/api/favorites.py) ----

/** signal is "FAVORITE" today; the store is dislike-ready (DISLIKE later). */
export const FavoriteSchema = z
  .object({
    user_id: z.string(),
    listing_id: z.string(),
    signal: z.literal("FAVORITE"),
    created_at: z.string(),
    note: z.string().optional(),
    deal: DealSchema.optional(),
  })
  .passthrough();
export type Favorite = z.infer<typeof FavoriteSchema>;

export const FavoritesResponseSchema = z.object({
  favorites: z.array(FavoriteSchema),
});

export const AddFavoriteResponseSchema = z.object({
  listing_id: z.string(),
  signal: z.literal("FAVORITE"),
});

export const RemoveFavoriteResponseSchema = z.object({
  listing_id: z.string(),
  removed: z.boolean(),
});

export const TriggerRunResponseSchema = z.object({
  status: z.literal("ACCEPTED"),
  message_id: z.string(),
  user_id: z.string(),
});
export type TriggerRunResponse = z.infer<typeof TriggerRunResponseSchema>;

export const ActDealResponseSchema = z.object({
  listing_id: z.string(),
  status: DealStatusSchema,
});

// ---- Market signal (mirror src/api/market_signal.py) ----

/**
 * Per-factor contributions to the composite index. Open-ended on purpose:
 * the backend can add a new factor (e.g. "incentive_prevalence") and the
 * dashboard's chip list picks it up without a schema bump.
 */
export const FactorContribsSchema = z.record(z.string(), z.number());

export const MarketSignalPointSchema = z
  .object({
    timestamp: z.string().optional(),
    composite_index: z.number(),
    factor_contribs: FactorContribsSchema,
    variance_score: z.number(),
    flower_position: z.number(),
    label: z.enum(["Now", "Quiet", "Not yet"]),
  })
  .passthrough();
export type MarketSignalPoint = z.infer<typeof MarketSignalPointSchema>;

/**
 * Per-row safeParse-and-drop, same pattern as RunsResponseSchema. One bad
 * snapshot row (legacy shape, partial guardrail-blocked row) shouldn't blank
 * the whole chart — the surviving points still tell a useful story.
 */
export const MarketSignalResponseSchema = z.object({
  window: z.string(),
  view: z.string(),
  points: z.array(z.unknown()).transform((items): MarketSignalPoint[] => {
    const out: MarketSignalPoint[] = [];
    for (const item of items) {
      const parsed = MarketSignalPointSchema.safeParse(item);
      if (parsed.success) out.push(parsed.data);
    }
    return out;
  }),
  latest: MarketSignalPointSchema.nullable().optional(),
});
export type MarketSignalResponse = z.infer<typeof MarketSignalResponseSchema>;

// ---- Incentive stack (mirror src/api/incentives.py) ----

/**
 * One program in the user's qualified incentive stack: federal credit, state
 * rebate, or utility program. `stacks=true` means it adds on top of the others
 * in the response (per our hand-curated stackability YAML); `expiration_date`
 * may be null for evergreen programs and is used by the card to surface
 * urgency copy when funding is running thin.
 */
export const IncentiveItemSchema = z
  .object({
    id: z.string(),
    title: z.string(),
    amount_usd: z.number(),
    jurisdiction: z.enum(["federal", "state", "utility"]),
    expiration_date: z.string().nullable().optional(),
    stacks: z.boolean(),
    notes: z.string().optional(),
  })
  .passthrough();
export type IncentiveItem = z.infer<typeof IncentiveItemSchema>;

/**
 * Per-row safeParse-and-drop, same pattern as RunsResponseSchema /
 * MarketSignalResponseSchema. One malformed program shouldn't blank the whole
 * card — the surviving items still produce a useful stack.
 *
 * `hidden_reason` is set by the backend when the surface should suppress
 * itself entirely (e.g. `"non-ev-prefs"` when the user's `fuel_types`
 * excludes EV — the EV credit reminder is hidden by default).
 */
export const IncentiveStackResponseSchema = z.object({
  stack: z.object({
    total_usd: z.number(),
    items: z.array(z.unknown()).transform((items): IncentiveItem[] => {
      const out: IncentiveItem[] = [];
      for (const item of items) {
        const parsed = IncentiveItemSchema.safeParse(item);
        if (parsed.success) out.push(parsed.data);
      }
      return out;
    }),
  }),
  warnings: z.array(z.string()).default([]),
  hidden_reason: z.string().optional(),
});
export type IncentiveStackResponse = z.infer<typeof IncentiveStackResponseSchema>;

// ---- Macro snapshot (mirror src/api/macro_snapshot.py) ----

/**
 * One FRED-cached macro series surfaced as a small dashboard pill. Today only
 * `auto_loan_apr` is rendered (via `AprSnapshotPill`); `cpi_used_cars` ships
 * in the same response so a future "CPI used-cars" pill can land without a
 * second round-trip.
 *
 * Both fields are independently nullable: if the nightly FRED refresh hasn't
 * populated the cache yet (cold-start) we omit the pill rather than render a
 * placeholder. `passthrough()` keeps the schema tolerant to additive backend
 * fields landing without a frontend bump.
 */
export const AutoLoanAprSchema = z.object({
  value_pct: z.number(),
  as_of: z.string(),
  label_short: z.string(),
  context: z.string().nullable(),
}).passthrough();
export type AutoLoanApr = z.infer<typeof AutoLoanAprSchema>;

export const CpiUsedCarsSchema = z.object({
  value: z.number(),
  as_of: z.string(),
  mom_change_pct: z.number().nullable(),
  yoy_change_pct: z.number().nullable(),
}).passthrough();
export type CpiUsedCars = z.infer<typeof CpiUsedCarsSchema>;

export const MacroSnapshotSchema = z.object({
  auto_loan_apr: AutoLoanAprSchema.nullable(),
  cpi_used_cars: CpiUsedCarsSchema.nullable(),
});
export type MacroSnapshot = z.infer<typeof MacroSnapshotSchema>;

// ---- Used-vs-new arbitrage (mirror src/api/used_vs_new.py) ----

/**
 * One-shot supporting-metric payload for the dashboard's used-vs-new arbitrage
 * pill. Surfaced only in two editorially-interesting states — `tight` (new
 * captures unusual value relative to used) and `wide` (used 1-2yr is sharply
 * cheaper than new). The boring middle ("normal") is returned as `null` by
 * the backend so the pill suppresses itself entirely.
 *
 * Dollar amounts are whole-USD integers; `spread_pct` carries one decimal.
 */
export const ArbitrageResponseSchema = z
  .object({
    spread_pct: z.number(),
    spread_usd: z.number(),
    median_new_usd: z.number(),
    median_used_usd: z.number(),
    label_short: z.string(),
    verdict: z.enum(["tight", "wide"]),
    as_of: z.string(),
  })
  .nullable();
export type ArbitrageResponse = z.infer<typeof ArbitrageResponseSchema>;

// ---- Inventory anomaly (mirror src/api/inventory_anomaly.py) ----

/**
 * One-shot supporting-metric payload for the dashboard's inventory anomaly
 * pill. Surfaced only in two editorially-interesting states — `thick` (the
 * user's matching-listing count is materially above its trailing baseline,
 * usually a calendar-driven dealer stock-up) and `thin` (sharply below
 * baseline, usually a segment-wide supply pinch). The boring middle is
 * returned as `null` by the backend so the pill suppresses itself entirely.
 *
 * `ratio` is `current_count / baseline_count`, rounded to two decimals
 * server-side; the frontend rounds again for the human-readable "≈2× usual"
 * blurb. Both counts are whole-listing integers.
 */
export const InventoryAnomalyResponseSchema = z
  .object({
    current_count: z.number().int().nonnegative(),
    baseline_count: z.number().int().nonnegative(),
    ratio: z.number().nonnegative(),
    verdict: z.enum(["thick", "thin"]),
    as_of: z.string(),
  })
  .nullable();
export type InventoryAnomalyResponse = z.infer<typeof InventoryAnomalyResponseSchema>;

// ---- API usage (mirror src/api/usage.py) ----

export const UsageSchema = z
  .object({
    marketcheck: z
      .object({
        tier: z.enum(["default", "byok"]),
        api_key_id: z.string(),
        calls: z.number().int().nonnegative(),
        // null on BYOK — we don't track the user's own quota ceiling.
        limit: z.number().int().positive().nullable(),
        yyyy_mm: z.string(),
        // null on BYOK for the same reason.
        pct_used: z.number().nullable(),
        approaching_limit: z.boolean(),
      })
      .passthrough(),
  })
  .passthrough();
export type UsageResponse = z.infer<typeof UsageSchema>;
