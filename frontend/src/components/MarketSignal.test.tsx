import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarketSignal, type MarketSignalPoint } from "./MarketSignal";

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

describe("MarketSignal — endpoints and education line", () => {
  it("renders both endpoint labels and the descriptive (never predictive) one-liner", () => {
    render(<MarketSignal latest={makePoint({})} />);

    expect(screen.getByText("Now")).toBeInTheDocument();
    expect(screen.getByText("Not yet")).toBeInTheDocument();
    expect(
      screen.getByText(/This signal describes today's market conditions/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/It is not a prediction\./i)).toBeInTheDocument();
  });

  it("keeps the education one-liner visible even in the cold-start state", () => {
    render(<MarketSignal latest={null} isColdStart />);

    expect(
      screen.getByText(/This signal describes today's market conditions/i),
    ).toBeInTheDocument();
  });
});

describe("MarketSignal — marker position", () => {
  it("places the marker on the left half for a negative flower_position", () => {
    render(<MarketSignal latest={makePoint({ flower_position: -0.8, label: "Now" })} />);

    const marker = screen.getByTestId("signal-marker");
    const left = Number(marker.getAttribute("data-marker-left"));
    expect(left).toBeLessThan(50);
  });

  it("places the marker on the right half for a positive flower_position", () => {
    render(<MarketSignal latest={makePoint({ flower_position: 0.8, label: "Not yet" })} />);

    const marker = screen.getByTestId("signal-marker");
    const left = Number(marker.getAttribute("data-marker-left"));
    expect(left).toBeGreaterThan(50);
  });

  it("snaps the marker exactly to center for flower_position = 0", () => {
    render(<MarketSignal latest={makePoint({ flower_position: 0 })} />);

    const marker = screen.getByTestId("signal-marker");
    expect(Number(marker.getAttribute("data-marker-left"))).toBeCloseTo(50, 1);
  });
});

describe("MarketSignal — flower morph thresholds", () => {
  it("renders the bloom state for flower_position > 0.15", () => {
    const { container } = render(
      <MarketSignal latest={makePoint({ flower_position: 0.6 })} />,
    );

    // Bloom uses 5 rotated petal ellipses + a single center circle. The bud
    // state has no ellipses, withered has 3 ellipses + no center circle.
    // Five ellipses is the cleanest invariant for bloom.
    expect(container.querySelectorAll("ellipse").length).toBe(5);
    expect(container.querySelector("circle[r='1.9']")).not.toBeNull();
  });

  it("renders the bud state inside the central [-0.15, 0.15] zone", () => {
    const { container } = render(
      <MarketSignal latest={makePoint({ flower_position: 0.05 })} />,
    );

    // Bud has no ellipses; it's a teardrop path + a stem.
    expect(container.querySelectorAll("ellipse").length).toBe(0);
    // And the "Quiet" middle label sits above the marker only in this zone.
    expect(screen.getByTestId("quiet-label")).toHaveTextContent(/Quiet/i);
  });

  it("renders the withered state for flower_position < -0.15", () => {
    const { container } = render(
      <MarketSignal latest={makePoint({ flower_position: -0.6 })} />,
    );

    // Withered draws 3 drooped petal ellipses, no honey center circle.
    expect(container.querySelectorAll("ellipse").length).toBe(3);
    expect(container.querySelector("circle[r='1.9']")).toBeNull();
  });
});

describe("MarketSignal — variance halo", () => {
  it("draws a tight halo when factors agree (variance ~ 0)", () => {
    render(<MarketSignal latest={makePoint({ variance_score: 0 })} />);

    const halo = screen.getByTestId("variance-halo");
    expect(Number(halo.getAttribute("data-halo-radius"))).toBeCloseTo(20, 1);
  });

  it("draws a wider halo when factors disagree (variance ~ 1)", () => {
    render(<MarketSignal latest={makePoint({ variance_score: 1 })} />);

    const halo = screen.getByTestId("variance-halo");
    expect(Number(halo.getAttribute("data-halo-radius"))).toBeCloseTo(36, 1);
  });

  it("clamps an out-of-range variance to the [0, 1] range", () => {
    render(<MarketSignal latest={makePoint({ variance_score: 5 })} />);

    const halo = screen.getByTestId("variance-halo");
    expect(Number(halo.getAttribute("data-halo-radius"))).toBeCloseTo(36, 1);
  });
});

describe("MarketSignal — factor chips", () => {
  it("shows the top three factors sorted by absolute magnitude", () => {
    render(
      <MarketSignal
        latest={makePoint({
          flower_position: -0.2,
          label: "Now",
          factor_contribs: {
            calendar_pressure: 8,
            discount_depth: 6,
            inventory_density: -3,
            effective_price_trend: 1,
            incentive_prevalence: -2,
          },
        })}
      />,
    );

    const chips = screen.getByTestId("factor-chips").textContent;
    // Top three by |value|: calendar (8), discount (6), inventory (-3).
    expect(chips).toContain("Calendar +8");
    expect(chips).toContain("Discount +6");
    expect(chips).toContain("Inventory -3");
    // The two smaller ones are suppressed.
    expect(chips).not.toContain("Eff. price");
    expect(chips).not.toContain("Incentives");
  });

  it("orders chips with the largest absolute contribution first", () => {
    render(
      <MarketSignal
        latest={makePoint({
          flower_position: 0.2,
          label: "Not yet",
          factor_contribs: {
            calendar_pressure: 2,
            discount_depth: -10,
            inventory_density: 5,
          },
        })}
      />,
    );

    const chips = screen.getByTestId("factor-chips").textContent;
    const discountIdx = chips.indexOf("Discount");
    const inventoryIdx = chips.indexOf("Inventory");
    const calendarIdx = chips.indexOf("Calendar");
    expect(discountIdx).toBeLessThan(inventoryIdx);
    expect(inventoryIdx).toBeLessThan(calendarIdx);
  });
});

describe("MarketSignal — cold-start", () => {
  it("renders the 'Building your baseline' message in place of the chips when latest is null", () => {
    render(<MarketSignal latest={null} />);

    expect(
      screen.getByText(/Building your baseline — full signal kicks in after 4 weeks of runs\./i),
    ).toBeInTheDocument();
    // And the chip row is suppressed.
    expect(screen.queryByTestId("factor-chips")).not.toBeInTheDocument();
  });

  it("renders the same baseline message when isColdStart is explicitly true", () => {
    render(
      <MarketSignal
        latest={makePoint({
          flower_position: 0.5,
          label: "Not yet",
          factor_contribs: { calendar_pressure: 10 },
        })}
        isColdStart
      />,
    );

    expect(screen.getByText(/Building your baseline/i)).toBeInTheDocument();
    expect(screen.queryByTestId("factor-chips")).not.toBeInTheDocument();
  });

  it("snaps the marker to center and uses the bud flower in cold-start", () => {
    const { container } = render(<MarketSignal latest={null} />);

    const marker = screen.getByTestId("signal-marker");
    expect(Number(marker.getAttribute("data-marker-left"))).toBeCloseTo(50, 1);
    // Bud state: no petal ellipses.
    expect(container.querySelectorAll("ellipse").length).toBe(0);
  });
});
