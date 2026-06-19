import { describe, expect, it } from "vitest";

import {
  outcomeBlurb,
  runDurationLabel,
  sourceErrorLabel,
  sourceErrorShort,
} from "./runSummary";
import type { RunSummary } from "./schemas";

function run(partial: Partial<RunSummary>): RunSummary {
  return { run_id: "00000000-0000-0000-0000-000000000000", ...partial };
}

describe("outcomeBlurb — SUCCESS", () => {
  it("renders the full count line when all counts are non-zero", () => {
    expect(
      outcomeBlurb(
        run({
          status: "SUCCESS",
          deals_found: 41,
          deals_above_threshold: 8,
          drafts_produced: 5,
        }),
      ),
    ).toBe("41 listings scored, 8 in view, 5 emails drafted");
  });

  it("collapses to 'nothing matched' when listings were scored but none cleared the threshold", () => {
    expect(
      outcomeBlurb(
        run({
          status: "SUCCESS",
          deals_found: 41,
          deals_above_threshold: 0,
          drafts_produced: 0,
        }),
      ),
    ).toBe("41 listings scored, nothing matched");
  });

  it("renders 'Nothing came back' when no listings were scored at all", () => {
    expect(
      outcomeBlurb(
        run({
          status: "SUCCESS",
          deals_found: 0,
          deals_above_threshold: 0,
          drafts_produced: 0,
        }),
      ),
    ).toBe("Nothing came back from MarketCheck");
  });

  it("treats undefined counts the same as zero", () => {
    expect(outcomeBlurb(run({ status: "SUCCESS" }))).toBe(
      "Nothing came back from MarketCheck",
    );
  });

  it("singularizes 1 listing / 1 email", () => {
    expect(
      outcomeBlurb(
        run({
          status: "SUCCESS",
          deals_found: 1,
          deals_above_threshold: 1,
          drafts_produced: 1,
        }),
      ),
    ).toBe("1 listing scored, 1 in view, 1 email drafted");
  });

  it("appends a source-error suffix when present", () => {
    expect(
      outcomeBlurb(
        run({
          status: "SUCCESS",
          deals_found: 0,
          source_errors: ["marketcheck:rate_limited"],
        }),
      ),
    ).toBe("Nothing came back from MarketCheck · MarketCheck rate-limited");
  });
});

describe("outcomeBlurb — GUARDRAIL_BLOCKED", () => {
  it("names the single failing node", () => {
    expect(
      outcomeBlurb(run({ status: "GUARDRAIL_BLOCKED", guardrail_triggers: ["score_deals"] })),
    ).toBe("Stopped at score deals");
  });

  it("comma-joins 2–3 triggers", () => {
    expect(
      outcomeBlurb(
        run({
          status: "GUARDRAIL_BLOCKED",
          guardrail_triggers: ["validate_listings", "score_deals"],
        }),
      ),
    ).toBe("Stopped at validate listings, score deals");
  });

  it("summarizes 'and N more' for >3 triggers", () => {
    expect(
      outcomeBlurb(
        run({
          status: "GUARDRAIL_BLOCKED",
          guardrail_triggers: ["a", "b", "c", "d"],
        }),
      ),
    ).toBe("Stopped at a and 3 more");
  });

  it("falls back to a generic note when triggers are missing", () => {
    expect(outcomeBlurb(run({ status: "GUARDRAIL_BLOCKED" }))).toBe(
      "Stopped by a safety check",
    );
  });
});

describe("outcomeBlurb — other statuses", () => {
  it("shows 'Running…' for RUNNING and IN_PROGRESS", () => {
    expect(outcomeBlurb(run({ status: "RUNNING" }))).toBe("Running…");
    expect(outcomeBlurb(run({ status: "IN_PROGRESS" }))).toBe("Running…");
  });

  it("renders ERROR with the source-error detail when present", () => {
    expect(
      outcomeBlurb(run({ status: "ERROR", source_errors: ["marketcheck:auth_failed"] })),
    ).toBe("Something went wrong — MarketCheck auth failed");
  });

  it("renders bare ERROR when no source error", () => {
    expect(outcomeBlurb(run({ status: "ERROR" }))).toBe("Something went wrong");
  });

  it("prefixes PARTIAL onto the SUCCESS-shaped sentence", () => {
    expect(
      outcomeBlurb(
        run({
          status: "PARTIAL",
          deals_found: 12,
          deals_above_threshold: 3,
        }),
      ),
    ).toBe("Partial — 12 listings scored, 3 in view");
  });

  it("returns null for unknown status", () => {
    expect(outcomeBlurb(run({ status: "WHATEVER" }))).toBeNull();
    expect(outcomeBlurb(run({}))).toBeNull();
  });
});

describe("runDurationLabel", () => {
  it("returns null when finished_at is missing", () => {
    expect(runDurationLabel(run({ started_at: "2026-05-30T06:00:00Z" }))).toBeNull();
  });

  it("returns null when started_at is missing", () => {
    expect(runDurationLabel(run({ finished_at: "2026-05-30T06:00:45Z" }))).toBeNull();
  });

  it("returns null when end is before start", () => {
    expect(
      runDurationLabel(
        run({
          started_at: "2026-05-30T06:00:45Z",
          finished_at: "2026-05-30T06:00:00Z",
        }),
      ),
    ).toBeNull();
  });

  it("formats seconds under a minute", () => {
    expect(
      runDurationLabel(
        run({
          started_at: "2026-05-30T06:00:00Z",
          finished_at: "2026-05-30T06:00:45Z",
        }),
      ),
    ).toBe("45s");
  });

  it("formats minutes + seconds", () => {
    expect(
      runDurationLabel(
        run({
          started_at: "2026-05-30T06:00:00Z",
          finished_at: "2026-05-30T06:02:11Z",
        }),
      ),
    ).toBe("2m 11s");
  });

  it("drops trailing zero seconds", () => {
    expect(
      runDurationLabel(
        run({
          started_at: "2026-05-30T06:00:00Z",
          finished_at: "2026-05-30T06:02:00Z",
        }),
      ),
    ).toBe("2m");
  });

  it("formats hours + minutes", () => {
    expect(
      runDurationLabel(
        run({
          started_at: "2026-05-30T06:00:00Z",
          finished_at: "2026-05-30T07:03:00Z",
        }),
      ),
    ).toBe("1h 3m");
  });
});

describe("sourceErrorLabel / sourceErrorShort", () => {
  it("maps known codes to long form", () => {
    expect(sourceErrorLabel("marketcheck:rate_limited")).toMatch(/rate-limited/i);
  });
  it("falls back to a generic long-form for unknown codes", () => {
    expect(sourceErrorLabel("strange:thing")).toBe("Source error: strange:thing");
  });
  it("maps known codes to short form", () => {
    expect(sourceErrorShort("marketcheck:auth_failed")).toBe("MarketCheck auth failed");
  });
  it("falls back to a generic short-form for unknown codes", () => {
    expect(sourceErrorShort("strange:thing")).toBe("source error: strange:thing");
  });
});
