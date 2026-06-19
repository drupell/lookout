import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";
import type { RunSummary } from "@/lib/schemas";

import { RunRow } from "./RunRow";

const { writeText } = vi.hoisted(() => ({ writeText: vi.fn() }));

// Wrap every render in ToastProvider — RunRow calls useToast for the copy
// feedback path. Mirrors DealCard.test.tsx's pattern so tests don't fight
// the provider plumbing.
function renderRow(ui: ReactElement) {
  return render(<ToastProvider>{ui}</ToastProvider>);
}

function makeRun(partial: Partial<RunSummary>): RunSummary {
  return {
    run_id: "3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e",
    ...partial,
  };
}

function openDetailsFor(label: string): void {
  const details = screen.getByText(label).closest("details");
  if (!details) throw new Error(`No <details> ancestor for "${label}"`);
  details.open = true;
}

beforeEach(() => {
  // jsdom doesn't expose navigator.clipboard by default — wire a stub so the
  // primary copy path (writeText) is exercisable and assertable.
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
});

afterEach(() => {
  cleanup();
  writeText.mockReset();
});

describe("RunRow — closed summary", () => {
  it("renders the status badge and the SUCCESS outcome blurb", () => {
    renderRow(
      <RunRow
        run={makeRun({
          status: "SUCCESS",
          started_at: "2026-05-30T06:00:00Z",
          deals_found: 41,
          deals_above_threshold: 8,
          drafts_produced: 5,
        })}
      />,
    );

    expect(screen.getByText("SUCCESS")).toBeInTheDocument();
    expect(
      screen.getByText("41 listings scored, 8 in view, 5 emails drafted"),
    ).toBeInTheDocument();
  });

  it("renders the GUARDRAIL_BLOCKED 'Stopped at …' blurb", () => {
    renderRow(
      <RunRow
        run={makeRun({
          status: "GUARDRAIL_BLOCKED",
          guardrail_triggers: ["score_deals"],
        })}
      />,
    );

    expect(screen.getByText("GUARDRAIL_BLOCKED")).toBeInTheDocument();
    expect(screen.getByText("Stopped at score deals")).toBeInTheDocument();
  });

  it("keeps the disclosure closed by default so the full run_id is hidden", () => {
    renderRow(
      <RunRow
        run={makeRun({
          run_id: "3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e",
          status: "SUCCESS",
        })}
      />,
    );

    // jsdom keeps <details> children in the DOM regardless of `open`; the
    // browser hides them via UA CSS. The semantic invariant we care about
    // is that the <details> hasn't been opened — that's what guarantees the
    // disclosure body (and the full UUID) isn't surfaced to the user.
    const details = screen.getByText("SUCCESS").closest("details");
    expect(details).not.toBeNull();
    expect(details?.open).toBe(false);
  });
});

describe("RunRow — open disclosure", () => {
  it("reveals the full run_id when the <details> is opened", () => {
    renderRow(
      <RunRow
        run={makeRun({
          run_id: "3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e",
          status: "SUCCESS",
        })}
      />,
    );

    openDetailsFor("SUCCESS");

    expect(
      screen.getByText("3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e"),
    ).toBeInTheDocument();
  });

  it("lists humanized guardrail triggers when present", () => {
    renderRow(
      <RunRow
        run={makeRun({
          status: "GUARDRAIL_BLOCKED",
          guardrail_triggers: ["score_deals", "validate_listings"],
        })}
      />,
    );

    openDetailsFor("GUARDRAIL_BLOCKED");

    expect(screen.getByText("Guardrails")).toBeInTheDocument();
    expect(screen.getByText("score deals, validate listings")).toBeInTheDocument();
  });

  it("lists long-form source errors when present", () => {
    renderRow(
      <RunRow
        run={makeRun({
          status: "SUCCESS",
          source_errors: ["marketcheck:rate_limited"],
        })}
      />,
    );

    openDetailsFor("SUCCESS");

    expect(screen.getByText("Source errors")).toBeInTheDocument();
    expect(screen.getByText(/MarketCheck rate-limited the request/i)).toBeInTheDocument();
  });
});

describe("RunRow — copy run ID", () => {
  it("copies the full UUID and toasts success", async () => {
    writeText.mockResolvedValue(undefined);
    renderRow(
      <RunRow
        run={makeRun({
          run_id: "3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e",
          status: "SUCCESS",
        })}
      />,
    );

    fireEvent.click(screen.getByLabelText("Copy run ID"));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e");
    });
    expect(await screen.findByText("Run ID copied")).toBeInTheDocument();
  });

  it("surfaces an error toast when clipboard.writeText rejects", async () => {
    writeText.mockRejectedValue(new Error("denied"));
    renderRow(
      <RunRow
        run={makeRun({
          run_id: "3f8a5b27-4d61-4c4d-9e7a-aa8e7b1d4b1e",
          status: "SUCCESS",
        })}
      />,
    );

    fireEvent.click(screen.getByLabelText("Copy run ID"));

    expect(await screen.findByText("Couldn't copy run ID")).toBeInTheDocument();
  });
});
