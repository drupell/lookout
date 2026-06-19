import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import { DealCard } from "./DealCard";

const { addFavorite, removeFavorite } = vi.hoisted(() => ({
  addFavorite: vi.fn(),
  removeFavorite: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { addFavorite, removeFavorite },
}));

const deal = { listing_id: "lst-1", first_seen: "2026-01-01T00:00:00", is_favorite: false };

// DealCard now reads useToast(), so it must render under a ToastProvider.
function renderCard(ui: ReactElement) {
  return render(<ToastProvider>{ui}</ToastProvider>);
}

afterEach(() => {
  cleanup();
  addFavorite.mockReset();
  removeFavorite.mockReset();
});

describe("DealCard favorite toggle", () => {
  it("optimistically favorites and calls the API", async () => {
    addFavorite.mockResolvedValue({ listing_id: "lst-1", signal: "FAVORITE" });
    renderCard(<DealCard deal={deal} />);

    const btn = screen.getByLabelText("Add to favorites");
    fireEvent.click(btn);

    // Optimistic flip is immediate.
    expect(screen.getByLabelText("Remove from favorites")).toBeInTheDocument();
    await waitFor(() => {
      expect(addFavorite).toHaveBeenCalledWith("lst-1");
    });
  });

  it("reverts the optimistic flip and surfaces an error toast when the API rejects", async () => {
    addFavorite.mockRejectedValue(new Error("boom"));
    renderCard(<DealCard deal={deal} />);

    fireEvent.click(screen.getByLabelText("Add to favorites"));

    await waitFor(() => {
      expect(screen.getByLabelText("Add to favorites")).toBeInTheDocument();
    });
    // The failure is communicated, not swallowed.
    expect(await screen.findByText(/couldn't save that favorite/i)).toBeInTheDocument();
  });

  it("acts as remove on the favorites page and notifies the parent", async () => {
    removeFavorite.mockResolvedValue({ listing_id: "lst-1", removed: true });
    const onUnfavorite = vi.fn();
    renderCard(<DealCard deal={deal} onUnfavorite={onUnfavorite} />);

    // On the favorites page the heart starts filled (it IS a favorite).
    fireEvent.click(screen.getByLabelText("Remove from favorites"));

    await waitFor(() => {
      expect(removeFavorite).toHaveBeenCalledWith("lst-1");
      expect(onUnfavorite).toHaveBeenCalledWith("lst-1");
    });
  });
});
