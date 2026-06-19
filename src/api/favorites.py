"""Favorites — generic per-user feedback store (user-scoped).

Today every row carries signal="FAVORITE". The schema is deliberately
dislike-ready: adding signal="DISLIKE" later is an enum + endpoint change,
not a migration. Each favorite stores a full snapshot of the deal at
favorite-time so it survives the deal row's 90-day TTL and run rotation.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from src.api._errors import HttpError
from src.api._user_repo import decimals_to_native, floats_to_decimals

logger = logging.getLogger(__name__)

SIGNAL_FAVORITE = "FAVORITE"


def _table():
    return boto3.resource("dynamodb").Table(os.environ["FAVORITES_TABLE_NAME"])


def _deals_table():
    return boto3.resource("dynamodb").Table(os.environ["DEALS_TABLE_NAME"])


def list_favorites(user_id: str) -> list[dict[str, Any]]:
    """Return this user's favorited deals (newest-favorited first)."""
    resp = _table().query(KeyConditionExpression=Key("user_id").eq(user_id))
    items = decimals_to_native(resp.get("Items", []))
    items.sort(key=lambda f: f.get("created_at") or "", reverse=True)
    return items


def add_favorite(user_id: str, listing_id: str, note: str | None = None) -> dict[str, Any]:
    """Favorite a deal. Idempotent — re-favoriting refreshes the snapshot.

    404 if the deal does not exist, 403 if it belongs to another user.
    """
    if not listing_id:
        raise HttpError(400, "listing_id required")

    # Resolve the deal the same way act_on_deal does (deals.py:56-67).
    resp = _deals_table().query(
        KeyConditionExpression=Key("listing_id").eq(listing_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        raise HttpError(404, "deal not found")
    deal = items[0]
    if deal.get("user_id") and deal.get("user_id") != user_id:
        raise HttpError(403, "not your deal")

    item: dict[str, Any] = {
        "user_id": user_id,
        "listing_id": listing_id,
        "signal": SIGNAL_FAVORITE,
        "created_at": datetime.now(UTC).isoformat(),
        "deal": deal,  # full snapshot — survives the deal row's TTL
    }
    if note:
        item["note"] = note

    # Unconditional put = idempotent (PK+SK = user_id+listing_id). Defensive
    # float→Decimal in case the snapshot carries any native floats.
    _table().put_item(Item=floats_to_decimals(item))
    return {"listing_id": listing_id, "signal": SIGNAL_FAVORITE}


def remove_favorite(user_id: str, listing_id: str) -> dict[str, Any]:
    """Unfavorite a deal. Idempotent — deleting a missing key is a no-op."""
    if not listing_id:
        raise HttpError(400, "listing_id required")
    _table().delete_item(Key={"user_id": user_id, "listing_id": listing_id})
    return {"listing_id": listing_id, "removed": True}


def favorite_listing_ids(user_id: str) -> set[str]:
    """Return the set of listing_ids this user has favorited.

    Cheap single Query (projection-only) used by list_deals to stamp
    `is_favorite` on each deal so the heart renders filled.
    """
    resp = _table().query(
        KeyConditionExpression=Key("user_id").eq(user_id),
        ProjectionExpression="listing_id",
    )
    return {f["listing_id"] for f in resp.get("Items", [])}
