import { describe, expect, it } from "vitest";

import { PreferencesSchema, ScheduleConfigSchema } from "./schemas";

describe("ScheduleConfigSchema", () => {
  it("accepts valid BYOK schedule shape", () => {
    expect(
      ScheduleConfigSchema.parse({
        days_of_week: [3],
        time_of_day_utc: "08:30",
      }),
    ).toBeTruthy();
  });

  it("accepts multiple days (Mondays & Thursdays)", () => {
    expect(
      ScheduleConfigSchema.parse({
        days_of_week: [0, 3],
        time_of_day_utc: "00:00",
      }),
    ).toBeTruthy();
  });

  it("rejects an out-of-range day in days_of_week", () => {
    expect(() =>
      ScheduleConfigSchema.parse({
        days_of_week: [7],
        time_of_day_utc: "08:00",
      }),
    ).toThrow();
  });

  it("rejects an empty days_of_week", () => {
    expect(() =>
      ScheduleConfigSchema.parse({
        days_of_week: [],
        time_of_day_utc: "08:00",
      }),
    ).toThrow();
  });

  it("rejects malformed time_of_day_utc", () => {
    expect(() =>
      ScheduleConfigSchema.parse({
        days_of_week: [3],
        time_of_day_utc: "8:00",
      }),
    ).toThrow();
  });
});

describe("PreferencesSchema", () => {
  it("requires the schedule block", () => {
    const minimal = {
      vehicle: {},
      search: {
        location_zip: "10001",
        radius_miles: 50,
        max_vehicle_age_years: 3,
        fuel_types: ["EV"],
        min_price_usd: 0,
        max_price_usd: 60000,
        target_listings: 50,
        max_pages: 1,
      },
      included_brands: [],
      excluded_brands: [],
      excluded_models: [],
      deal_criteria: {
        max_effective_monthly_usd: 500,
        min_discount_off_msrp_pct: 10,
        acceptable_apr_max: 7,
        lease_to_own_preferred: false,
        zero_percent_financing_preferred: true,
      },
      scoring: {
        threshold_notify: 0.7,
        threshold_draft_email: 0.85,
      },
    };

    expect(() => PreferencesSchema.parse(minimal)).toThrow();
  });
});
