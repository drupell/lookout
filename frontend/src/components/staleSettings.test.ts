import { describe, expect, it } from "vitest";

import { isStale } from "./staleSettings";

describe("isStale", () => {
  it("returns true when updated_at is after last_run_at", () => {
    expect(isStale("2026-05-01T12:00:00Z", "2026-04-30T12:00:00Z")).toBe(true);
  });

  it("returns false when updated_at is before last_run_at", () => {
    expect(isStale("2026-04-30T12:00:00Z", "2026-05-01T12:00:00Z")).toBe(false);
  });

  it("returns false when timestamps are equal", () => {
    expect(isStale("2026-05-01T12:00:00Z", "2026-05-01T12:00:00Z")).toBe(false);
  });

  it("returns false when last_run_at is missing (user never ran)", () => {
    expect(isStale("2026-05-01T12:00:00Z", null)).toBe(false);
    expect(isStale("2026-05-01T12:00:00Z", undefined)).toBe(false);
  });

  it("returns false when updated_at is missing", () => {
    expect(isStale(null, "2026-05-01T12:00:00Z")).toBe(false);
  });

  it("returns false on unparseable timestamps", () => {
    expect(isStale("not a date", "2026-05-01T12:00:00Z")).toBe(false);
    expect(isStale("2026-05-01T12:00:00Z", "garbage")).toBe(false);
  });
});
