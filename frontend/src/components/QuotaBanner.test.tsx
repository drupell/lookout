import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { UsageResponse } from "@/lib/schemas";

import { QuotaBanner } from "./QuotaBanner";

interface UsageOverrides {
  tier?: "default" | "byok";
  api_key_id?: string;
  calls?: number;
  limit?: number | null;
  pct_used?: number | null;
  approaching_limit?: boolean;
  yyyy_mm?: string;
}

function makeUsage(overrides: UsageOverrides = {}): UsageResponse {
  const calls = overrides.calls ?? 412;
  const limit: number | null = overrides.limit !== undefined ? overrides.limit : 500;
  return {
    marketcheck: {
      tier: overrides.tier ?? "default",
      api_key_id: overrides.api_key_id ?? "shared",
      calls,
      limit,
      yyyy_mm: overrides.yyyy_mm ?? "2026-06",
      pct_used:
        overrides.pct_used !== undefined
          ? overrides.pct_used
          : limit !== null
            ? (calls / limit) * 100
            : null,
      approaching_limit: overrides.approaching_limit ?? true,
    },
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("QuotaBanner", () => {
  it("renders when default-tier usage is approaching the limit", () => {
    render(<QuotaBanner usage={makeUsage()} />);

    expect(screen.getByText(/412 \/ 500/)).toBeInTheDocument();
    expect(
      screen.getByText(/before the shared default-tier limit/i),
    ).toBeInTheDocument();
  });

  it("stays hidden when approaching_limit is false", () => {
    const { container } = render(
      <QuotaBanner
        usage={makeUsage({ calls: 100, approaching_limit: false })}
      />,
    );

    expect(container.firstChild).toBeNull();
  });

  it("stays hidden for BYOK users regardless of approaching_limit", () => {
    const { container } = render(
      <QuotaBanner
        usage={makeUsage({
          tier: "byok",
          api_key_id: "byok:preview-user",
          calls: 1480,
          limit: 1500,
          approaching_limit: true,
        })}
      />,
    );

    expect(container.firstChild).toBeNull();
  });

  it("stays hidden when usage is null (API failed)", () => {
    const { container } = render(<QuotaBanner usage={null} />);

    expect(container.firstChild).toBeNull();
  });

  it("persists dismissal in localStorage keyed by yyyy_mm", () => {
    const { unmount } = render(<QuotaBanner usage={makeUsage({ yyyy_mm: "2026-06" })} />);

    fireEvent.click(screen.getByLabelText("Dismiss"));

    // Same month -> still dismissed after remount.
    expect(
      window.localStorage.getItem("quota-banner-dismissed-2026-06"),
    ).toBe("1");

    unmount();
    const { container } = render(<QuotaBanner usage={makeUsage({ yyyy_mm: "2026-06" })} />);
    expect(container.firstChild).toBeNull();
  });

  it("re-shows when a new month rolls in (different localStorage key)", () => {
    // Simulate having dismissed last month's banner.
    window.localStorage.setItem("quota-banner-dismissed-2026-05", "1");

    render(<QuotaBanner usage={makeUsage({ yyyy_mm: "2026-06" })} />);

    // The current-month banner should still render — last month's dismissal
    // doesn't carry over.
    expect(screen.getByText(/before the shared default-tier limit/i)).toBeInTheDocument();
  });

  it("computes a sensible 'about N more runs' estimate from remaining calls", () => {
    // 88 remaining @ 50 calls/run ~= 1 more run.
    render(<QuotaBanner usage={makeUsage({ calls: 412, limit: 500 })} />);

    expect(screen.getByText(/about/i).textContent).toMatch(/1 more run/);
  });
});
