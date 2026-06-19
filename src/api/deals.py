"""Deals listing + lifecycle handlers (user-scoped)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from src.api._errors import HttpError
from src.api._user_repo import decimals_to_native, get_user

logger = logging.getLogger(__name__)


def list_deals(user_id: str, query: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Return the latest completed run's scored deals for this user.

    Scoped to the user's `last_run_id` snapshot — old runs' rows are retained
    in the table (audit / TTL) but never surface here. Returns [] when the
    user has no completed run yet (never fall back to "show everything").

    Query params:
        min_score: float — drop deals scored below this
        status: str — filter to one status (NEW/NOTIFIED/ACTED)
        limit: int — cap result size (default 100)
    """
    query = query or {}
    min_score = float(query.get("min_score", 0))
    status_filter = query.get("status") or None
    limit = int(query.get("limit", 100))

    user = get_user(user_id)
    last_run_id = (user or {}).get("last_run_id")
    if not last_run_id:
        return []

    table = boto3.resource("dynamodb").Table(os.environ["DEALS_TABLE_NAME"])
    resp = table.query(
        IndexName="user-id-index",
        KeyConditionExpression=Key("user_id").eq(user_id),
        ScanIndexForward=False,  # newest first by first_seen — latest run leads
        Limit=min(limit * 4, 500),  # over-fetch to allow client-side filtering
    )
    items = decimals_to_native(resp.get("Items", []))

    # Snapshot scoping: only the latest completed run's rows.
    items = [d for d in items if d.get("run_id") == last_run_id]

    if status_filter:
        items = [d for d in items if d.get("status") == status_filter]
    if min_score > 0:
        items = [d for d in items if (d.get("overall_score") or 0) >= min_score]

    # Stamp is_favorite from one cheap Query so the heart renders filled.
    from src.api.favorites import favorite_listing_ids

    fav_ids = favorite_listing_ids(user_id)
    for d in items:
        d["is_favorite"] = d.get("listing_id") in fav_ids

    # Sort by overall_score desc, take top `limit`
    items.sort(key=lambda d: d.get("overall_score") or 0, reverse=True)
    return items[:limit]


def act_on_deal(user_id: str, listing_id: str) -> dict[str, Any]:
    """Mark a deal as acted on. 404 if not found, 403 if owned by another user."""
    if not listing_id:
        raise HttpError(400, "listing_id required")

    table = boto3.resource("dynamodb").Table(os.environ["DEALS_TABLE_NAME"])
    # Need first_seen (sort key) to update — query by listing_id
    resp = table.query(
        KeyConditionExpression=Key("listing_id").eq(listing_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        raise HttpError(404, "deal not found")
    deal = items[0]
    if deal.get("user_id") and deal.get("user_id") != user_id:
        raise HttpError(403, "not your deal")

    table.update_item(
        Key={"listing_id": listing_id, "first_seen": deal["first_seen"]},
        UpdateExpression="SET #s = :s, acted_at = :a",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "ACTED", ":a": datetime.now(UTC).isoformat()},
    )
    return {"listing_id": listing_id, "status": "ACTED"}
