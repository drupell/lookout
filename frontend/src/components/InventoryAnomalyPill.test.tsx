import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { InventoryAnomalyResponse } from "@/lib/schemas";

import { InventoryAnomalyPill } from "./InventoryAnomalyPill";

function makeAnomaly(
  overrides: Partial<NonNullable<InventoryAnomalyResponse>> = {},
): NonNullable<InventoryAnomalyResponse> {
  return {
    current_count: 42,
    baseline_count: 20,
    ratio: 2.1,
    verdict: "thick",
    as_of: "2026-05-25T06:00:00+00:00",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

describe("InventoryAnomalyPill", () => {
  it("renders the thick variant with the matching count and ratio blurb", () => {
    render(<InventoryAnomalyPill data={makeAnomaly()} />);

    const pill = screen.getByRole("status");
    expect(pill).toBeInTheDocument();
    // data-verdict mirrors the backend so a visual-regression QA pass can
    // grep for which variant a screenshot is exercising.
    expect(pill.getAttribute("data-verdict")).toBe("thick");
    expect(pill.getAttribute("aria-label")).toBe(
      "Inventory — 42 matching listings nearby — ≈2× usual",
    );
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("≈2× usual")).toBeInTheDocument();
  });

  it("renders the thin variant with the muted-tone framing", () => {
    render(
      <InventoryAnomalyPill
        data={makeAnomaly({
          current_count: 6,
          baseline_count: 18,
          ratio: 0.33,
          verdict: "thin",
        })}
      />,
    );

    const pill = screen.getByRole("status");
    expect(pill.getAttribute("data-verdict")).toBe("thin");
    expect(pill.getAttribute("aria-label")).toBe(
      "Inventory — 6 matching listings nearby — ≈third usual",
    );
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("≈third usual")).toBeInTheDocument();
  });

  it("renders nothing when data is null (backend suppressed normal verdict)", () => {
    const { container } = render(<InventoryAnomalyPill data={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("formats the thousands-separator on large nearby counts", () => {
    render(
      <InventoryAnomalyPill
        data={makeAnomaly({
          current_count: 1340,
          baseline_count: 600,
          ratio: 2.23,
          verdict: "thick",
        })}
      />,
    );
    expect(screen.getByText("1,340")).toBeInTheDocument();
    // ratio 2.23 rounds to 2× in the pill blurb.
    expect(screen.getByText("≈2× usual")).toBeInTheDocument();
  });

  it("clamps very-thick ratios at 5× so the blurb stays defensible", () => {
    render(
      <InventoryAnomalyPill
        data={makeAnomaly({
          current_count: 200,
          baseline_count: 15,
          ratio: 13.3,
          verdict: "thick",
        })}
      />,
    );
    expect(screen.getByText("≈5× usual")).toBeInTheDocument();
  });

  it("uses 'almost none' when current_count is zero against a real baseline", () => {
    render(
      <InventoryAnomalyPill
        data={makeAnomaly({
          current_count: 0,
          baseline_count: 22,
          ratio: 0,
          verdict: "thin",
        })}
      />,
    );
    expect(screen.getByText("almost none")).toBeInTheDocument();
  });
});
