import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IncentiveItem, IncentiveStackResponse } from "@/lib/schemas";

import { IncentiveStackCard } from "./IncentiveStackCard";

// Hoisted mock for the api module — mirrors UsageQuotaPill.test.tsx so future
// tests can flip to API-driven loading if the component grows to fetch its
// own data. Today the card is a pure prop-driven view; we keep the mock in
// place anyway so the import graph matches the convention.
const { getIncentiveStack } = vi.hoisted(() => ({ getIncentiveStack: vi.fn() }));

vi.mock("@/lib/api", () => ({
  api: { getIncentiveStack },
}));

interface ItemOverrides {
  id?: string;
  title?: string;
  amount_usd?: number;
  jurisdiction?: IncentiveItem["jurisdiction"];
  expiration_date?: string | null;
  stacks?: boolean;
  notes?: string;
}

function makeItem(overrides: ItemOverrides = {}): IncentiveItem {
  return {
    id: overrides.id ?? "federal_30d",
    title: overrides.title ?? "Federal Section 30D EV Credit",
    amount_usd: overrides.amount_usd ?? 7500,
    jurisdiction: overrides.jurisdiction ?? "federal",
    expiration_date:
      overrides.expiration_date !== undefined
        ? overrides.expiration_date
        : "2032-12-31T00:00:00+00:00",
    stacks: overrides.stacks ?? true,
    ...(overrides.notes !== undefined ? { notes: overrides.notes } : {}),
  };
}

function makeResponse(overrides: {
  items?: IncentiveItem[];
  warnings?: string[];
  hidden_reason?: string;
  total_usd?: number;
}): IncentiveStackResponse {
  const items = overrides.items ?? [];
  const total_usd =
    overrides.total_usd ?? items.reduce((sum, i) => sum + i.amount_usd, 0);
  return {
    stack: { total_usd, items },
    warnings: overrides.warnings ?? [],
    ...(overrides.hidden_reason !== undefined
      ? { hidden_reason: overrides.hidden_reason }
      : {}),
  };
}

afterEach(() => {
  cleanup();
});

describe("IncentiveStackCard", () => {
  it("renders the total and every item title with mock data", () => {
    const data = makeResponse({
      items: [
        makeItem({
          id: "federal_30d",
          title: "Federal Section 30D EV Credit",
          amount_usd: 7500,
          jurisdiction: "federal",
        }),
        makeItem({
          id: "ma_morev",
          title: "MA MOR-EV",
          amount_usd: 2500,
          jurisdiction: "state",
        }),
        makeItem({
          id: "eversource_charge_now",
          title: "Eversource EV Charge Now",
          amount_usd: 1500,
          jurisdiction: "utility",
        }),
      ],
    });

    render(<IncentiveStackCard data={data} />);

    expect(screen.getByTestId("incentive-total")).toHaveTextContent("$11,500");
    expect(screen.getByText("Federal Section 30D EV Credit")).toBeInTheDocument();
    expect(screen.getByText("MA MOR-EV")).toBeInTheDocument();
    expect(screen.getByText("Eversource EV Charge Now")).toBeInTheDocument();
    // Jurisdictions summary line shows every category that appears.
    expect(
      screen.getByText(/across Federal \+ state \+ utility/i),
    ).toBeInTheDocument();
    // Federal row uses the fixed "you qualify by income" suffix; state and
    // utility lean on the "stacks" suffix.
    expect(screen.getByText(/you qualify by income/i)).toBeInTheDocument();
    expect(screen.getAllByText(/stacks/i).length).toBeGreaterThanOrEqual(2);
  });

  it("renders warnings as amber rows prefixed with a chevron", () => {
    const data = makeResponse({
      items: [makeItem({ id: "federal_30d" })],
      warnings: ["MA MOR-EV funding running low — exhaustion ~6 weeks"],
    });

    render(<IncentiveStackCard data={data} />);

    const warning = screen.getByTestId("incentive-warning");
    expect(warning).toHaveTextContent(/MA MOR-EV funding running low/i);
    // Lucide ChevronRight renders as an aria-hidden svg; the chevron is
    // present in the row by structural lookup (no role is exposed on purpose).
    expect(warning.querySelector("svg")).not.toBeNull();
    // Amber tint in both modes.
    expect(warning.className).toMatch(/text-amber-700/);
    expect(warning.className).toMatch(/dark:text-amber-400/);
  });

  it("renders nothing when hidden_reason === 'non-ev-prefs'", () => {
    const data = makeResponse({
      items: [makeItem({ id: "federal_30d" })],
      hidden_reason: "non-ev-prefs",
    });

    const { container } = render(<IncentiveStackCard data={data} />);

    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when data is null (no loading flash)", () => {
    // The dashboard fires this surface in parallel with the rest of the page;
    // a placeholder would flash for the ~ms before the promise settles. The
    // contract is the same on a hard API failure: stay quiet, never noisy.
    const { container } = render(<IncentiveStackCard data={null} />);

    expect(container.firstChild).toBeNull();
  });

  it("renders a single-item stack without a <details> wrapper", () => {
    const data = makeResponse({
      items: [makeItem({ id: "federal_30d", amount_usd: 7500 })],
    });

    render(<IncentiveStackCard data={data} />);

    expect(screen.queryByTestId("incentive-details")).not.toBeInTheDocument();
    expect(screen.getByTestId("incentive-total")).toHaveTextContent("$7,500");
  });

  it("wraps the body in <details> when the stack has more than three items", () => {
    const data = makeResponse({
      items: [
        makeItem({ id: "federal_30d", jurisdiction: "federal", amount_usd: 7500 }),
        makeItem({ id: "state_a", title: "State A", jurisdiction: "state", amount_usd: 2000 }),
        makeItem({ id: "state_b", title: "State B", jurisdiction: "state", amount_usd: 1500 }),
        makeItem({
          id: "utility_a",
          title: "Utility A",
          jurisdiction: "utility",
          amount_usd: 1000,
        }),
      ],
    });

    render(<IncentiveStackCard data={data} />);

    const details = screen.getByTestId("incentive-details");
    expect(details.tagName.toLowerCase()).toBe("details");
    // Still shows the items by default (open) — collapsibility is the
    // affordance, not a hidden-by-default state.
    expect(screen.getByText("State A")).toBeInTheDocument();
    expect(screen.getByText("Utility A")).toBeInTheDocument();
  });

  it("renders an empty-stack message when items=[] and no hidden_reason", () => {
    const data = makeResponse({ items: [], total_usd: 0 });

    render(<IncentiveStackCard data={data} />);

    expect(
      screen.getByText(/No incentives found for your area yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("incentive-total")).not.toBeInTheDocument();
  });

  describe("expiration pill", () => {
    /**
     * Inside-window dates earn the right-aligned amber "Expires Mon DD" pill;
     * dates farther out (or missing entirely) leave the pill off the row.
     * Per-tier windows mirror `_EXPIRATION_WARNING_WINDOW_BY_TIER` in
     * `src/api/incentives.py` — federal 90d, state 42d, utility 21d.
     */

    function isoFromNow(days: number): string {
      const dt = new Date();
      dt.setUTCDate(dt.getUTCDate() + days);
      return dt.toISOString().slice(0, 10);
    }

    it("renders the pill on a state row expiring inside the 42-day window", () => {
      const data = makeResponse({
        items: [
          makeItem({
            id: "ma_morev",
            title: "MA MOR-EV",
            amount_usd: 2500,
            jurisdiction: "state",
            expiration_date: isoFromNow(20),
          }),
        ],
      });
      render(<IncentiveStackCard data={data} />);
      const pill = screen.getByTestId("incentive-expiration-pill");
      expect(pill).toBeInTheDocument();
      expect(pill).toHaveTextContent(/^Expires [A-Z][a-z]{2} \d{1,2}$/);
      // Amber pill with dark-mode variant + tabular-nums for line-up.
      expect(pill.className).toMatch(/text-amber-700/);
      expect(pill.className).toMatch(/dark:text-amber-400/);
      expect(pill.className).toMatch(/text-xs/);
      expect(pill.className).toMatch(/font-mono/);
      expect(pill.className).toMatch(/tabular-nums/);
    });

    it("omits the pill on a row with no expiration_date", () => {
      const data = makeResponse({
        items: [
          makeItem({
            id: "wa_ev_exempt",
            title: "WA EV Sales Tax Exemption",
            amount_usd: 0,
            jurisdiction: "state",
            expiration_date: null,
          }),
        ],
      });
      render(<IncentiveStackCard data={data} />);
      expect(
        screen.queryByTestId("incentive-expiration-pill"),
      ).not.toBeInTheDocument();
    });

    it("omits the pill when the expiration is past the tier window", () => {
      // Federal 30D's curated YAML expiration is 2032 — way outside the
      // 90-day federal window — so no pill should render.
      const data = makeResponse({
        items: [
          makeItem({
            id: "federal_30d",
            title: "Federal Section 30D EV Credit",
            amount_usd: 7500,
            jurisdiction: "federal",
            expiration_date: "2032-12-31",
          }),
        ],
      });
      render(<IncentiveStackCard data={data} />);
      expect(
        screen.queryByTestId("incentive-expiration-pill"),
      ).not.toBeInTheDocument();
    });

    it("uses the utility 21-day window — 30 days out omits the pill", () => {
      const data = makeResponse({
        items: [
          makeItem({
            id: "utility_far",
            title: "Utility Far",
            amount_usd: 500,
            jurisdiction: "utility",
            expiration_date: isoFromNow(30),
          }),
        ],
      });
      render(<IncentiveStackCard data={data} />);
      expect(
        screen.queryByTestId("incentive-expiration-pill"),
      ).not.toBeInTheDocument();
    });
  });
});
