"""API quota usage handler — backs `GET /me/usage`.

Surfaces MarketCheck quota utilization to users *before* they get blocked.
Default-tier users share Lookout's 500/mo bucket; BYOK users have their own
bucket whose plan-specific limit we don't know yet (we can't introspect the
user's MarketCheck plan from their key alone), so the limit is reported as
`null` and the approaching-limit flag stays `false` for BYOK until the
frontend gives us a way to set it.

Response shape is intentionally one key (`marketcheck`) keyed by service so
future services (auto.dev, AFDC) slot in without shape changes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.api._user_repo import get_user
from src.memory.api_usage_store import byok_key_id, shared_key_id
from src.memory.api_usage_store import get_usage as _get_usage_store

logger = logging.getLogger(__name__)

# Lookout's shared MarketCheck default-tier monthly call cap. Hardcoded here
# rather than read from config because the API plan it derives from is
# Lookout-owned and not user-tunable.
DEFAULT_TIER_MARKETCHECK_LIMIT = 500

# Threshold for the dashboard banner — at >80% we warn the user that they
# may run out before month end. Matches the banner copy in the plan.
APPROACHING_LIMIT_PCT = 80.0


def get_usage(user_id: str) -> dict[str, Any]:
    """Return current-month API quota utilization for this user.

    The shape returned per service is:

        {
            "tier": "default" | "byok",
            "api_key_id": "shared" | "byok:<user_id>",
            "calls": int,
            "limit": int | None,
            "yyyy_mm": "YYYY-MM",
            "pct_used": float | None,
            "approaching_limit": bool,
        }

    `limit` is null and `approaching_limit` is false for BYOK because we
    don't know what plan the user is on. `pct_used` rounds to 1 decimal so
    the dashboard pill doesn't render noise like "62.3987%".
    """
    user = get_user(user_id) or {}
    tier = str(user.get("tier", "default"))

    if tier == "byok":
        api_key_id = byok_key_id(user_id)
        limit: int | None = None
    else:
        api_key_id = shared_key_id()
        limit = DEFAULT_TIER_MARKETCHECK_LIMIT

    usage = _get_usage_store(api_key_id)
    calls = int(usage.get("calls", 0))
    yyyy_mm = str(usage.get("yyyy_mm", ""))

    pct_used: float | None = None
    approaching = False
    if limit is not None and limit > 0:
        pct_used = round((calls / limit) * 100.0, 1)
        approaching = pct_used > APPROACHING_LIMIT_PCT

    return {
        "marketcheck": {
            "tier": tier,
            "api_key_id": api_key_id,
            "calls": calls,
            "limit": limit,
            "yyyy_mm": yyyy_mm,
            "pct_used": pct_used,
            "approaching_limit": approaching,
        }
    }
