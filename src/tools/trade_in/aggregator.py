"""Trade-in value aggregator — combines estimates from multiple sources."""

from __future__ import annotations

import logging
from typing import Any

from src.config.loader import load_preferences
from src.tools.trade_in.carmax import get_carmax_estimate
from src.tools.trade_in.kbb import get_kbb_estimate

logger = logging.getLogger(__name__)


def get_trade_in_estimate() -> dict[str, Any]:
    """Aggregate trade-in estimates from all configured sources.

    Returns a dict matching TradeInOutput schema.
    Raises ValueError if no sources return a valid estimate.
    """
    prefs = load_preferences()
    vin = prefs.vehicle.vin
    mileage = prefs.vehicle.mileage
    condition = prefs.vehicle.condition
    zip_code = prefs.search.location_zip

    sources: list[dict[str, Any]] = []

    kbb = get_kbb_estimate(vin, mileage, condition, zip_code)
    if kbb:
        sources.append(kbb)

    carmax = get_carmax_estimate(vin, mileage, condition, zip_code)
    if carmax:
        sources.append(carmax)

    if not sources:
        raise ValueError("No trade-in sources returned a valid estimate")

    estimates = [s["estimate_usd"] for s in sources]
    avg = sum(estimates) / len(estimates)

    return {
        "vin": vin,
        "sources": sources,
        "average_estimate_usd": round(avg, 2),
        "lowest_estimate_usd": min(estimates),
        "highest_estimate_usd": max(estimates),
    }
