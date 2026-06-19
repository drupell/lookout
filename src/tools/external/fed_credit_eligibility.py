"""Federal Section 30D EV-credit eligibility lookup.

The IRS Clean Vehicle Credit (Section 30D) eligibility list is published
quarterly by fueleconomy.gov. Rather than build a live fetcher against a
brittle HTML table, we ship a curated JSON snapshot at
`src/data/fed_credit_eligible_vehicles.json` and refresh it quarterly by
hand.

API: a single `is_eligible(make, model, year)` function. Case-insensitive
match. Unknown vehicles return `{credit_usd: 0, reason: "..."}` instead of
raising — the `/me/incentives` handler treats this as "skip the federal
credit row" and never blows up the whole response.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent.parent.parent / "data" / "fed_credit_eligible_vehicles.json"

NOT_IN_LIST_REASON = "not in eligible-vehicles list"


@lru_cache(maxsize=1)
def _load() -> list[dict[str, Any]]:
    """Read the bundled JSON file once per cold start.

    Returns the `vehicles` list, or [] if the file is missing/malformed. We
    never raise — callers see "unknown vehicle" for everything, which is the
    safe default.
    """
    try:
        with _DATA_PATH.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("fed_credit_eligibility data load failed: %s", exc)
        return []
    vehicles = data.get("vehicles")
    if not isinstance(vehicles, list):
        return []
    return vehicles


def is_eligible(make: str, model: str, year: int) -> dict[str, Any]:
    """Look up Section 30D eligibility for a make/model/year.

    Args:
        make: Manufacturer name (case-insensitive).
        model: Model name (case-insensitive).
        year: Model year.

    Returns:
        `{"credit_usd": int, "reason": str}` — credit_usd is the dollar
        amount the buyer would receive at purchase (0 if not eligible), and
        `reason` is a short, user-friendly explanation suitable for surfacing
        in the dashboard.
    """
    make_norm = (make or "").strip().lower()
    model_norm = (model or "").strip().lower()

    for row in _load():
        row_make = str(row.get("make", "")).strip().lower()
        row_model = str(row.get("model", "")).strip().lower()
        row_year = row.get("year")
        if row_make != make_norm or row_model != model_norm:
            continue
        if row_year != year:
            continue
        # `criteria_passed` lets us list vehicles that historically qualified
        # but don't right now (battery sourcing rule changes). For now every
        # row in the JSON has criteria_passed=true; the field is forward-
        # compatible.
        if not row.get("criteria_passed", True):
            return {
                "credit_usd": 0,
                "reason": "does not currently meet battery sourcing criteria",
            }
        return {
            "credit_usd": int(row.get("credit_amount_usd") or 0),
            "reason": f"qualifies for Section 30D — MSRP cap ${int(row.get('msrp_cap', 0)):,}",
        }

    return {"credit_usd": 0, "reason": NOT_IN_LIST_REASON}
