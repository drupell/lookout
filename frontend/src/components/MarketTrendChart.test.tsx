import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MarketSignalPoint } from "./MarketSignal";
import { MarketTrendChart, type MarketTrendWindow } from "./MarketTrendChart";

function makePoint(partial: Partial<MarketSignalPoint>): MarketSignalPoint {
  return {
    composite_index: 0,
    factor_contribs: {},
    variance_score: 0.4,
    flower_position: 0,
    label: "Quiet",
    ...partial,
  };
}

afterEach(cleanup);

describe("MarketTrendChart — window pills", () => {
  it("invokes onWindowChange when an available, non-active pill is clicked", () => {
    const onWindowChange = vi.fn();
    render(
      <MarketTrendChart
        points={[makePoint({ composite_index: 4 })]}
        currentWindow="30d"
        availableWindows={["30d", "90d"]}
        onWindowChange={onWindowChange}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "90d" }));

    expect(onWindowChange).toHaveBeenCalledWith("90d" satisfies MarketTrendWindow);
  });

  it("does not call onWindowChange for a disabled (unavailable) pill", () => {
    const onWindowChange = vi.fn();
    render(
      <MarketTrendChart
        points={[makePoint({ composite_index: 4 })]}
        currentWindow="30d"
        availableWindows={["30d"]}
        onWindowChange={onWindowChange}
      />,
    );

    const sixMonthPill = screen.getByRole("tab", { name: "6m" });
    expect(sixMonthPill).toBeDisabled();

    fireEvent.click(sixMonthPill);

    expect(onWindowChange).not.toHaveBeenCalled();
  });

  it("marks the active pill via aria-selected and shows a history hint on disabled pills", () => {
    render(
      <MarketTrendChart
        points={[makePoint({})]}
        currentWindow="90d"
        availableWindows={["30d", "90d"]}
        onWindowChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("tab", { name: "90d" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "30d" })).toHaveAttribute("aria-selected", "false");
    // Disabled pill carries the "weeks of history available" hint.
    const twelveMonthPill = screen.getByRole("tab", { name: "12m" });
    expect(twelveMonthPill).toHaveAttribute("title", expect.stringMatching(/weeks of history/));
  });
});

describe("MarketTrendChart — empty state", () => {
  it("renders the 'Building your baseline' caption and no trend line for an empty series", () => {
    render(
      <MarketTrendChart
        points={[]}
        currentWindow="30d"
        availableWindows={["30d"]}
        onWindowChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/Building your baseline/i)).toBeInTheDocument();
    expect(screen.queryByTestId("trend-line")).not.toBeInTheDocument();
    expect(screen.queryByTestId("today-point")).not.toBeInTheDocument();
  });

  it("still renders the zero-line in the empty state so the chart isn't visually blank", () => {
    const { container } = render(
      <MarketTrendChart
        points={[]}
        currentWindow="30d"
        availableWindows={["30d"]}
        onWindowChange={vi.fn()}
      />,
    );

    // The zero-line is the only <line> we draw; should be present in empty.
    expect(container.querySelectorAll("line").length).toBe(1);
  });
});

describe("MarketTrendChart — today's point", () => {
  it("renders the today circle at the rightmost x position", () => {
    // API returns newest-first; the chart should plot left→right oldest→newest
    // so today's point ends up at the right edge of the viewBox (320 - 8 = 312).
    const newest = makePoint({ composite_index: 18 });
    const older = makePoint({ composite_index: -4 });
    const oldest = makePoint({ composite_index: 2 });

    render(
      <MarketTrendChart
        points={[newest, older, oldest]}
        currentWindow="30d"
        availableWindows={["30d"]}
        onWindowChange={vi.fn()}
      />,
    );

    const point = screen.getByTestId("today-point");
    const cx = Number(point.getAttribute("cx"));
    // The right padding inside the chart viewBox is 8 → rightmost x is 312.
    expect(cx).toBeCloseTo(312, 0);
  });

  it("renders a trend line when there is at least one point", () => {
    render(
      <MarketTrendChart
        points={[makePoint({ composite_index: 12 })]}
        currentWindow="30d"
        availableWindows={["30d"]}
        onWindowChange={vi.fn()}
      />,
    );

    expect(screen.getByTestId("trend-line")).toBeInTheDocument();
    expect(screen.getByTestId("today-point")).toBeInTheDocument();
  });
});

describe("MarketTrendChart — 'Why this signal?' disclosure", () => {
  it("shows the factor breakdown when the details are opened", () => {
    render(
      <MarketTrendChart
        points={[
          makePoint({
            composite_index: 14,
            factor_contribs: {
              calendar_pressure: 8,
              discount_depth: 6,
              inventory_density: -3,
            },
          }),
        ]}
        currentWindow="30d"
        availableWindows={["30d"]}
        onWindowChange={vi.fn()}
      />,
    );

    const details = screen.getByText("Why this signal?").closest("details");
    expect(details).not.toBeNull();
    if (details) details.open = true;

    expect(screen.getByText("Calendar")).toBeInTheDocument();
    expect(screen.getByText("Discount")).toBeInTheDocument();
    expect(screen.getByText("Inventory")).toBeInTheDocument();
    // The explainer for Calendar lives in the disclosure body.
    expect(screen.getByText(/end-of-month/i)).toBeInTheDocument();
  });

  it("hides the disclosure entirely when there are no points", () => {
    render(
      <MarketTrendChart
        points={[]}
        currentWindow="30d"
        availableWindows={["30d"]}
        onWindowChange={vi.fn()}
      />,
    );

    expect(screen.queryByText("Why this signal?")).not.toBeInTheDocument();
  });
});
