"""In-flight run state — supports the dashboard's "Run in progress" indicator.

Lifecycle of a row in the runs table:
    1. Worker starts → `mark_running(...)` puts a row with status=RUNNING and
       initial progress.
    2. Each LangGraph node transition → `update_progress(...)` patches the
       row with the current node and how many have completed.
    3. Run completes (success or error) → `write_run_record(...)` in
       `run_store.py` overwrites the same key with the full audit record.
       Finalized rows have status != RUNNING, so the in-flight reader stops
       considering them "active".

Keying: row is `(run_id, started_at)`. The worker generates both upfront and
threads them through the lifecycle so every write hits the same row.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

logger = logging.getLogger(__name__)

# Hard-coded count of nodes the agent traverses on the longest path:
# fetch_listings, validate_listings, fetch_trade_in, validate_trade_in,
# rag_enrich, validate_rag, score_deals, validate_scores, filter_new_deals,
# draft_emails, validate_drafts, persist_results, validate_persist.
# When a run takes the no-drafts path it'll cap at ~11/13, which is fine —
# the bar disappears when status flips to SUCCESS so the user never sees
# "stuck at 85%".
TOTAL_NODES = 13

# Anything older than this is treated as crashed-mid-run rather than active.
# Lambda's max execution is 900s; double for headroom.
STALE_RUN_SECONDS = 30 * 60


def _table():
    name = os.environ.get("RUNS_TABLE_NAME", "lookout-dev-runs")
    return boto3.resource("dynamodb").Table(name)


def mark_running(
    run_id: str,
    user_id: str | None,
    started_at: str,
    environment: str,
) -> None:
    """Write the initial RUNNING row. Called once at the top of a run."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return
    item: dict[str, Any] = {
        "run_id": run_id,
        "timestamp": started_at,
        "started_at": started_at,
        "status": "RUNNING",
        "environment": environment,
        "current_node": "starting",
        "progress_completed": 0,
        "progress_total": TOTAL_NODES,
    }
    if user_id:
        item["user_id"] = user_id
    try:
        _table().put_item(Item=item)
    except Exception:
        logger.exception("Failed to write RUNNING row for run_id=%s", run_id)


def update_progress(
    run_id: str,
    started_at: str,
    current_node: str,
    completed: int,
) -> None:
    """Patch the RUNNING row with the current node + completion count."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return
    try:
        _table().update_item(
            Key={"run_id": run_id, "timestamp": started_at},
            UpdateExpression=(
                "SET current_node = :n, progress_completed = :c, progress_total = :t"
            ),
            ExpressionAttributeValues={
                ":n": current_node,
                ":c": completed,
                ":t": TOTAL_NODES,
            },
        )
    except Exception:
        logger.exception("Failed to update progress for run_id=%s", run_id)


def get_in_flight(user_id: str) -> dict[str, Any] | None:
    """Return the active in-flight run for this user, or None.

    "Active" means status=RUNNING and started_at is recent (not orphaned by a
    crashed Lambda). The user-id-index GSI scopes the query.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return None
    try:
        resp = _table().query(
            IndexName="user-id-index",
            KeyConditionExpression=Key("user_id").eq(user_id),
            FilterExpression=Attr("status").eq("RUNNING"),
            ScanIndexForward=False,  # newest first
            Limit=5,
        )
    except Exception:
        logger.exception("Failed to query in-flight runs for user_id=%s", user_id)
        return None

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    for item in resp.get("Items", []):
        started_raw = item.get("started_at")
        if not started_raw:
            continue
        try:
            started = datetime.fromisoformat(str(started_raw))
        except ValueError:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        age = (now - started).total_seconds()
        if 0 <= age <= STALE_RUN_SECONDS:
            return _to_native(item)
    return None


def _to_native(obj: Any) -> Any:
    """Convert DynamoDB Decimals to int/float for JSON serialization."""
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    return obj
