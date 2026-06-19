"""DynamoDB read/write for deal history.

Stores every scored deal with full score breakdown and status tracking.
Every write includes a run_id foreign key linking to the audit log.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def _get_table():
    table_name = os.environ.get("DEALS_TABLE_NAME", "lookout-dev-deals")
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(table_name)


def _convert_floats(obj: Any) -> Any:
    """Convert floats to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(v) for v in obj]
    return obj


# Records auto-delete via DynamoDB TTL. Bounded retention keeps us in line with
# MarketCheck ToU (don't accumulate a long-term dataset).
TTL_DAYS = 90


def write_deals(deals: list[dict[str, Any]], run_id: str, user_id: str | None = None) -> int:
    """Write scored deals to DynamoDB.

    Args:
        deals: scored deal records to persist
        run_id: foreign key to the run record
        user_id: owning user (Cognito sub). None for legacy single-tenant rows;
            prevents the row from appearing in any user-scoped query.

    Returns count of items written.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: would write %d deals for run %s", len(deals), run_id)
        return len(deals)

    table = _get_table()
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    expires_at = int(now_dt.timestamp()) + TTL_DAYS * 86400

    written = 0
    for deal in deals:
        item = {
            # --- Identity / lifecycle ---
            "listing_id": deal["listing_id"],
            "first_seen": now,
            "run_id": run_id,
            "status": "NEW",
            "expires_at": expires_at,  # epoch seconds — DynamoDB TTL attribute
            # --- Scoring ---
            "overall_score": deal.get("overall_score", 0),
            "above_threshold": bool(deal.get("above_threshold", False)),
            "score_breakdown": deal.get("score_breakdown", {}),
            "effective_out_of_pocket_usd": deal.get("effective_out_of_pocket_usd", 0),
            "trade_in_value_at_scoring": deal.get("trade_in_value_at_scoring", 0),
            "applicable_incentives": deal.get("applicable_incentives", []),
            # --- Vehicle ---
            "make": deal.get("make", ""),
            "model": deal.get("model", ""),
            "trim": deal.get("trim", ""),
            "year": deal.get("year", 0),
            "mileage": deal.get("mileage", 0),
            "msrp": deal.get("msrp"),
            "selling_price": deal.get("selling_price", 0),
            "fuel_type": deal.get("fuel_type", ""),
            "powertrain_type": deal.get("powertrain_type", ""),
            "body_type": deal.get("body_type", ""),
            # --- Dealer ---
            "dealer_name": deal.get("dealer_name", ""),
            "dealer_distance_miles": deal.get("dealer_distance_miles", 0),
            "url": deal.get("url", ""),
        }
        # Only set user_id if we have one — keeps the GSI sparse for legacy rows.
        if user_id:
            item["user_id"] = user_id
        table.put_item(Item=_convert_floats(item))
        written += 1

    logger.info("Wrote %d deals for run %s", written, run_id)
    return written


def wipe_user_deals(user_id: str) -> int:
    """Delete every deal belonging to a single user.

    Called at the top of a per-user agent run when REFRESH_DEALS_ON_RUN=true,
    so the user's deals view always reflects only the latest run rather than
    a growing pile of stale rows from previous runs. Scoped via the
    `user-id-index` GSI so we never touch other users' rows.

    Returns the count of rows deleted.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: would wipe deals for user_id=%s", user_id)
        return 0

    from boto3.dynamodb.conditions import Key

    table = _get_table()
    deleted = 0

    query_kwargs: dict[str, Any] = {
        "IndexName": "user-id-index",
        "KeyConditionExpression": Key("user_id").eq(user_id),
        "ProjectionExpression": "listing_id, first_seen",
    }
    while True:
        response = table.query(**query_kwargs)
        items = response.get("Items", [])
        if items:
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(
                        Key={
                            "listing_id": item["listing_id"],
                            "first_seen": item["first_seen"],
                        }
                    )
                    deleted += 1
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key

    logger.info("Wiped %d deals for user_id=%s", deleted, user_id)
    return deleted


def wipe_all_deals() -> int:
    """Delete every row in the deals table.

    Used when REFRESH_DEALS_ON_RUN=true so the table reflects only the latest
    run's results — appropriate for low-frequency cadences (weekly) where we
    don't want stale records lingering.

    Returns the count of rows deleted.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: would wipe deals table")
        return 0

    table = _get_table()
    deleted = 0

    # Scan keys, then batch-delete in chunks of 25 (DynamoDB BatchWriteItem cap)
    paginator_kwargs: dict[str, Any] = {"ProjectionExpression": "listing_id, first_seen"}
    while True:
        response = table.scan(**paginator_kwargs)
        items = response.get("Items", [])
        if items:
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(
                        Key={
                            "listing_id": item["listing_id"],
                            "first_seen": item["first_seen"],
                        }
                    )
                    deleted += 1
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        paginator_kwargs["ExclusiveStartKey"] = last_key

    logger.info("Wiped %d deals from table", deleted)
    return deleted


def get_known_listing_ids(user_id: str | None = None) -> set[str]:
    """Return listing_ids already in the deals table for deduplication.

    Scoped per user when `user_id` is given (via the `user-id-index` GSI) so
    one user's history never suppresses another user's "new deal" detection —
    this gates email drafting, not persistence. Legacy single-tenant runs
    (user_id=None) keep the full-table scan for backward compatibility.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return set()

    table = _get_table()
    listing_ids: set[str] = set()

    if user_id:
        from boto3.dynamodb.conditions import Key

        query_kwargs: dict[str, Any] = {
            "IndexName": "user-id-index",
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "ProjectionExpression": "listing_id",
        }
        while True:
            response = table.query(**query_kwargs)
            for item in response.get("Items", []):
                listing_ids.add(item["listing_id"])
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key
        return listing_ids

    # Legacy single-tenant: scan is acceptable here — table is small.
    response = table.scan(ProjectionExpression="listing_id")
    for item in response.get("Items", []):
        listing_ids.add(item["listing_id"])

    while "LastEvaluatedKey" in response:
        response = table.scan(
            ProjectionExpression="listing_id",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        for item in response.get("Items", []):
            listing_ids.add(item["listing_id"])

    return listing_ids


def update_deal_status(listing_id: str, first_seen: str, new_status: str) -> None:
    """Update a deal's status (NEW → NOTIFIED → EXPIRED → ACTED)."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return

    table = _get_table()
    table.update_item(
        Key={"listing_id": listing_id, "first_seen": first_seen},
        UpdateExpression="SET #s = :status",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":status": new_status},
    )
