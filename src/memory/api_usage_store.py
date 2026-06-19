"""DynamoDB read/write for per-API-key monthly call counts.

Surfaces MarketCheck quota utilization to users *before* they get blocked.
Default-tier users share Lookout's MarketCheck key; BYOK users have their
own. The PK distinguishes the two:

    "shared"          — Lookout's default-tier key (collective 500/mo bucket)
    "byok:<user_id>"  — a single user's BYOK key (their own bucket)

The SK is "YYYY-MM" so each row is a one-month aggregate. We do atomic
counter increments via UpdateItem with an ADD action — concurrent worker
runs in the same month never lose counts the way a get-modify-put would.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3

logger = logging.getLogger(__name__)

# Default-tier shared-key constant. Used by the MarketCheck client when no
# per-user BYOK key was resolved. Centralized here so a typo in the key
# string doesn't show up as "two different sharing buckets."
SHARED_KEY_ID = "shared"


def _table():
    name = os.environ.get("API_USAGE_TABLE_NAME", "lookout-dev-api-usage")
    return boto3.resource("dynamodb").Table(name)


def _current_yyyy_mm() -> str:
    now = datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


def shared_key_id() -> str:
    """Return the PK used for the default-tier shared bucket."""
    return SHARED_KEY_ID


def byok_key_id(user_id: str) -> str:
    """Return the PK used for a single BYOK user's bucket."""
    return f"byok:{user_id}"


# ---- Write ----


def increment_calls(api_key_id: str, *, delta: int = 1) -> int:
    """Atomically increment this month's call count; return the new total.

    Safe to call from concurrent Lambda invocations — DynamoDB's ADD action
    on a number attribute is an atomic counter, so two simultaneous +1s
    always land as +2, never as 1.

    Returns 0 in TEST_MODE without contacting AWS (so the agent's eval
    suite isn't writing real counters to a shared bucket).
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return 0

    yyyy_mm = _current_yyyy_mm()
    resp = _table().update_item(
        Key={"api_key_id": api_key_id, "yyyy_mm": yyyy_mm},
        UpdateExpression="ADD #c :d SET last_increment_at = :now",
        ExpressionAttributeNames={"#c": "calls"},
        ExpressionAttributeValues={":d": Decimal(delta), ":now": datetime.now(UTC).isoformat()},
        ReturnValues="UPDATED_NEW",
    )
    new_count = int(resp.get("Attributes", {}).get("calls", 0))
    logger.debug("api_usage: key=%s month=%s now=%d", api_key_id, yyyy_mm, new_count)
    return new_count


# ---- Read ----


def get_usage(api_key_id: str, *, yyyy_mm: str | None = None) -> dict[str, Any]:
    """Return current-month usage for a key.

    Shape: { "calls": int, "yyyy_mm": "YYYY-MM" } — calls=0 when there's
    no row yet (still in this month's first run). Same shape regardless of
    whether the row exists, so callers don't need a separate None branch.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return {"calls": 0, "yyyy_mm": yyyy_mm or _current_yyyy_mm()}

    target_month = yyyy_mm or _current_yyyy_mm()
    resp = _table().get_item(Key={"api_key_id": api_key_id, "yyyy_mm": target_month})
    item = resp.get("Item")
    if not item:
        return {"calls": 0, "yyyy_mm": target_month}
    return {
        "calls": int(item.get("calls", 0)),
        "yyyy_mm": target_month,
        "last_increment_at": item.get("last_increment_at"),
    }
