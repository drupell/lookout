import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ArbitrageResponse } from "@/lib/schemas";

import { UsedVsNewArbitragePill } from "./UsedVsNewArbitragePill";

function makeArbitrage(
  overrides: Partial<NonNullable<ArbitrageResponse>> = {},
): NonNullable<ArbitrageResponse> {
  return {
    spread_pct: 6.4,
    spread_usd: 2150,
    median_new_usd: 33500,
    median_used_usd: 31350,
    label_short: "Used 1-2yr",
    verdict: "tight",
    as_of: "2026-05-25T06:00:00+00:00",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

describe("UsedVsNewArbitragePill", () => {
  it("renders the tight variant with the spread amount", () => {
    render(<UsedVsNewArbitragePill data={makeArbitrage()} />);

    const pill = screen.getByRole("status");
    expect(pill).toBeInTheDocument();
    // The pill's data-verdict mirrors the backend's call so a screenshot
    // QA pass can grep for the right variant.
    expect(pill.getAttribute("data-verdict")).toBe("tight");
    // Single-line aria-label collapses the visual spans for screen readers
    // and lets us assert the full composed string without relying on
    // inter-span whitespace.
    expect(pill.getAttribute("aria-label")).toBe(
      "New within $2,150 of used — captures unusual value",
    );
    expect(screen.getByText("$2,150")).toBeInTheDocument();
    expect(screen.getByText("captures unusual value")).toBeInTheDocument();
  });

  it("renders the wide variant with the spread amount", () => {
    render(
      <UsedVsNewArbitragePill
        data={makeArbitrage({
          spread_pct: 22.5,
          spread_usd: 9200,
          median_new_usd: 41000,
          median_used_usd: 31800,
          verdict: "wide",
        })}
      />,
    );

    const pill = screen.getByRole("status");
    expect(pill.getAttribute("data-verdict")).toBe("wide");
    expect(pill.getAttribute("aria-label")).toBe(
      "Used 1-2yr is $9,200 under new — unusual gap",
    );
    expect(screen.getByText("$9,200")).toBeInTheDocument();
    expect(screen.getByText("unusual gap")).toBeInTheDocument();
  });

  it("renders nothing when data is null (suppressed normal verdict)", () => {
    const { container } = render(<UsedVsNewArbitragePill data={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("absolutes a negative spread so the dollar amount reads cleanly", () => {
    // Edge case the backend can produce in constrained-supply moments:
    // used selling *higher* than new buckets as "tight" with a negative
    // spread_usd. The pill should still render a sensible positive figure.
    render(
      <UsedVsNewArbitragePill
        data={makeArbitrage({
          spread_pct: -1.2,
          spread_usd: -400,
          median_new_usd: 33500,
          median_used_usd: 33900,
          verdict: "tight",
        })}
      />,
    );
    expect(screen.getByText("$400")).toBeInTheDocument();
  });
});
