"use client";

import { ExternalLink, Heart } from "lucide-react";
import { useState } from "react";

import { ScoreGauge } from "@/components/ScoreGauge";
import { Badge, cn, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import {
  dealTitle,
  discountPct,
  formatDealer,
  formatMileage,
  formatPrice,
} from "@/lib/dealFormat";
import type { Deal } from "@/lib/schemas";

interface Props {
  deal: Deal;
  /**
   * When provided, the heart always acts as "remove" and the parent is told
   * to drop the row after a successful unfavorite (used on the Favorites
   * page). Omit on the Deals page → the heart toggles add/remove.
   */
  onUnfavorite?: (listingId: string) => void;
}

/** "ACTED" → success, "NOTIFIED" → warning, everything else → neutral. */
function statusTone(status: string | undefined): "success" | "warning" | "neutral" {
  switch (status) {
    case "ACTED":
      return "success";
    case "NOTIFIED":
      return "warning";
    default:
      return "neutral";
  }
}

export function DealCard({ deal, onUnfavorite }: Props) {
  const toast = useToast();
  const title = dealTitle(deal);
  const discount = discountPct(deal);
  const mileage = formatMileage(deal.mileage);
  const dealer = formatDealer(deal);
  const subs = deal.score_breakdown;
  const incentives = deal.applicable_incentives ?? [];

  const onFavoritesPage = onUnfavorite !== undefined;
  const [fav, setFav] = useState(Boolean(deal.is_favorite) || onFavoritesPage);
  const [busy, setBusy] = useState(false);
  // Drives the one-shot heart "pop" animation; reset on animation end.
  const [popping, setPopping] = useState(false);

  async function toggleFavorite() {
    // Double-click guard: ignore taps while a request is in flight.
    if (busy) return;
    setBusy(true);

    // On the Favorites page the only action is "remove"; on the Deals page
    // it flips. Update optimistically, revert + tell the user on failure.
    const next = onFavoritesPage ? false : !fav;
    setFav(next);
    if (next) setPopping(true);

    try {
      if (next) {
        await api.addFavorite(deal.listing_id);
      } else {
        await api.removeFavorite(deal.listing_id);
        onUnfavorite?.(deal.listing_id);
      }
    } catch {
      // Revert the optimistic flip and surface a recoverable error.
      setFav(!next);
      toast.error(
        next
          ? "Couldn't save that favorite — please try again."
          : "Couldn't remove that favorite — please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="group px-5 py-4 transition-colors duration-fast hover:bg-stone-50/70 dark:hover:bg-stone-800/30">
      <div className="flex items-start justify-between gap-4">
        {/* Left column: vehicle + reasoning */}
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-100">
              {title}
            </h3>
            {deal.status ? <Badge tone={statusTone(deal.status)}>{deal.status}</Badge> : null}
          </div>

          {/* Price line */}
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
            <span className="font-mono text-sm font-medium tabular-nums text-stone-900 dark:text-stone-100">
              {formatPrice(deal.selling_price)}
            </span>
            {discount !== null ? (
              <span className="font-medium text-emerald-700 dark:text-emerald-400">
                {discount}% off MSRP
              </span>
            ) : null}
            {deal.msrp ? (
              <span className="tabular-nums text-stone-400 line-through">
                {formatPrice(deal.msrp)}
              </span>
            ) : null}
          </div>

          {/* Specs line */}
          {(mileage ?? dealer) ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-500">
              {mileage ? <span className="tabular-nums">{mileage}</span> : null}
              {dealer ? <span>{dealer}</span> : null}
              {deal.url ? (
                <a
                  href={deal.url}
                  target="_blank"
                  rel="noreferrer"
                  className="focus-ring inline-flex items-center gap-1 text-stone-600 transition-colors duration-fast hover:text-stone-900 dark:text-stone-400 dark:hover:text-stone-100"
                >
                  View listing <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          ) : null}

          {/* Reasoning + sub-scores */}
          {subs?.reasoning ? (
            <p className="text-xs leading-relaxed text-stone-600 dark:text-stone-400">
              {subs.reasoning}
            </p>
          ) : null}

          {subs ? (
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-2xs text-stone-500">
              <SubScore label="Price" value={subs.price_score} />
              <SubScore label="Financing" value={subs.financing_score} />
              <SubScore label="Incentive" value={subs.incentive_score} />
              <SubScore label="Pref. match" value={subs.preference_match_score} />
            </div>
          ) : null}

          {/* Effective out-of-pocket — only render when meaningfully different
              from selling_price (ie. trade-in or incentives applied). */}
          {deal.effective_out_of_pocket_usd !== undefined &&
          deal.effective_out_of_pocket_usd !== deal.selling_price ? (
            <p className="text-xs text-stone-500">
              After incentives + trade-in:{" "}
              <span className="font-mono tabular-nums text-stone-700 dark:text-stone-300">
                {formatPrice(deal.effective_out_of_pocket_usd)}
              </span>
              {incentives.length > 0
                ? ` · ${String(incentives.length)} incentive${incentives.length === 1 ? "" : "s"} applied`
                : ""}
            </p>
          ) : null}
        </div>

        {/* Right column: favorite + score */}
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <button
            type="button"
            onClick={toggleFavorite}
            disabled={busy}
            aria-pressed={fav}
            aria-label={fav ? "Remove from favorites" : "Add to favorites"}
            title={fav ? "Remove from favorites" : "Add to favorites"}
            className="focus-ring rounded-md p-1 text-stone-400 transition-colors duration-fast hover:text-rose-500 disabled:opacity-50"
          >
            <Heart
              onAnimationEnd={() => {
                setPopping(false);
              }}
              className={cn(
                "h-4 w-4 transition-colors duration-fast",
                fav ? "fill-rose-500 text-rose-500" : "fill-none",
                popping && "animate-heart-pop",
              )}
            />
          </button>
          <ScoreGauge score={deal.overall_score} />
          <p className="text-2xs font-medium uppercase tracking-wide text-stone-500">Score</p>
        </div>
      </div>
    </article>
  );
}

function SubScore({ label, value }: { label: string; value: number | undefined }) {
  if (value === undefined) return null;
  return (
    <span>
      <span className="text-stone-400">{label}</span>{" "}
      <span className="font-medium tabular-nums text-stone-700 dark:text-stone-300">
        {value.toFixed(2)}
      </span>
    </span>
  );
}
