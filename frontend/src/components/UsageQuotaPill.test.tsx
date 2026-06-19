import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UsageQuotaPill } from "./UsageQuotaPill";

const { getUsage } = vi.hoisted(() => ({ getUsage: vi.fn() }));

vi.mock("@/lib/api", () => ({
  api: { getUsage },
}));

interface UsageOverrides {
  tier?: "default" | "byok";
  api_key_id?: string;
  calls?: number;
  limit?: number | null;
  pct_used?: number | null;
  approaching_limit?: boolean;
  yyyy_mm?: string;
}

function makeUsage(overrides: UsageOverrides = {}) {
  const calls = overrides.calls ?? 312;
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
      approaching_limit: overrides.approaching_limit ?? false,
    },
  };
}

beforeEach(() => {
  getUsage.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("UsageQuotaPill", () => {
  it("renders the default-tier copy with shared quota counts", async () => {
    getUsage.mockResolvedValue(makeUsage({ calls: 312, limit: 500 }));
    render(<UsageQuotaPill />);

    expect(
      await screen.findByText(/MarketCheck: 312 \/ 500 calls this month/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/shared default tier/i)).toBeInTheDocument();
  });

  it("renders the BYOK copy without 'shared default tier' framing", async () => {
    getUsage.mockResolvedValue(
      makeUsage({
        tier: "byok",
        api_key_id: "byok:preview-user",
        calls: 18,
        limit: 1500,
        pct_used: 1.2,
      }),
    );
    render(<UsageQuotaPill />);

    expect(
      await screen.findByText(/MarketCheck: 18 \/ 1,500 \(your key\)/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/shared default tier/i)).not.toBeInTheDocument();
  });

  it("applies the amber tint above 70% utilization", async () => {
    getUsage.mockResolvedValue(makeUsage({ calls: 400, limit: 500, pct_used: 80 }));
    render(<UsageQuotaPill />);

    const pill = await screen.findByRole("status");
    expect(pill.className).toMatch(/bg-amber/);
  });

  it("applies the red tint above 90% utilization", async () => {
    getUsage.mockResolvedValue(makeUsage({ calls: 470, limit: 500, pct_used: 94 }));
    render(<UsageQuotaPill />);

    const pill = await screen.findByRole("status");
    expect(pill.className).toMatch(/bg-red/);
  });

  it("stays neutral for BYOK even at high utilization (their key, their problem to model)", async () => {
    getUsage.mockResolvedValue(
      makeUsage({
        tier: "byok",
        api_key_id: "byok:preview-user",
        calls: 1450,
        limit: 1500,
        pct_used: 96.6,
      }),
    );
    render(<UsageQuotaPill />);

    const pill = await screen.findByRole("status");
    expect(pill.className).not.toMatch(/bg-amber/);
    expect(pill.className).not.toMatch(/bg-red/);
  });

  it("renders nothing while loading (no flicker)", () => {
    // Never-resolving promise — simulates an in-flight request. The executor
    // is intentionally a no-op; nothing resolves the promise during the test.
    getUsage.mockReturnValue(
      new Promise(() => {
        /* never resolves */
      }),
    );
    const { container } = render(<UsageQuotaPill />);

    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the API errors (ambient surface, no red error)", async () => {
    getUsage.mockRejectedValue(new Error("boom"));
    const { container } = render(<UsageQuotaPill />);

    // Give the rejected promise a turn to settle.
    await waitFor(() => {
      expect(getUsage).toHaveBeenCalled();
    });
    expect(container.firstChild).toBeNull();
  });
});
