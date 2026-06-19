import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { MacroSnapshot } from "@/lib/schemas";

import { AprSnapshotPill } from "./AprSnapshotPill";

function makeSnapshot(overrides: Partial<MacroSnapshot> = {}): MacroSnapshot {
  return {
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
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

describe("AprSnapshotPill", () => {
  it("renders the APR with its context label", () => {
    render(<AprSnapshotPill data={makeSnapshot()} />);

    const pill = screen.getByRole("status");
    expect(pill).toBeInTheDocument();
    // The accessible label collapses label/value/context into a single line,
    // which is what a screen reader hears — also a handy way to assert the
    // full composed string without depending on inter-span whitespace.
    expect(pill.getAttribute("aria-label")).toBe(
      "48-mo new car APR — 7.4% · 24-month high",
    );
    expect(screen.getByText("7.4%")).toBeInTheDocument();
    expect(screen.getByText("24-month high")).toBeInTheDocument();
  });

  it("renders without context suffix when context is null", () => {
    render(
      <AprSnapshotPill
        data={makeSnapshot({
          auto_loan_apr: {
            value_pct: 6.2,
            as_of: "2026-05-01T00:00:00+00:00",
            label_short: "48-mo new car APR",
            context: null,
          },
        })}
      />,
    );

    const pill = screen.getByRole("status");
    expect(pill.getAttribute("aria-label")).toBe("48-mo new car APR — 6.2%");
    expect(screen.queryByText("24-month high")).not.toBeInTheDocument();
  });

  it("renders nothing when data is null (no flicker)", () => {
    const { container } = render(<AprSnapshotPill data={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when auto_loan_apr is null but cpi is set (APR-only pill for now)", () => {
    const { container } = render(
      <AprSnapshotPill
        data={makeSnapshot({
          auto_loan_apr: null,
          cpi_used_cars: {
            value: 160.2,
            as_of: "2026-04-01T00:00:00+00:00",
            mom_change_pct: -0.4,
            yoy_change_pct: 2.1,
          },
        })}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
