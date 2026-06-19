"""ListingSource protocol — common interface for listing data providers.

Any new provider (MarketCheck, CarAPI, manufacturer direct, etc.) implements this
protocol. The aggregator orchestrates them, so adding a new source doesn't require
changes to the agent graph or scoring logic.

Standard listing dict shape (what every provider must return):
    {
        "listing_id": str,          # stable, unique per (provider, listing)
        "make": str,
        "model": str,
        "year": int,
        "msrp": float | None,       # None if provider doesn't expose it
        "selling_price": float,
        "effective_apr": float | None,
        "lease_monthly": float | None,
        "dealer_name": str,
        "dealer_distance_miles": float,
        "url": str,
        "is_ev": bool,
        "source": str,              # provider name, e.g. "marketcheck"
    }
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ListingSource(Protocol):
    """Protocol every listings provider implements."""

    name: str

    def fetch_listings(
        self,
        zip_code: str,
        radius_miles: int,
        max_vehicle_age_years: int,
    ) -> list[dict[str, Any]]:
        """Return a list of standardized listing dicts.

        Must raise on hard failures (network, auth). Soft failures (no results,
        parsing issues on specific records) should log and return what parsed.
        """
        ...
