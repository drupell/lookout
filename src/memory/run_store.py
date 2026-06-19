"""DynamoDB read/write for execution audit log.

Every agent run produces a structured record for auditability.
Every record includes run_id as the primary key.
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
    table_name = os.environ.get("RUNS_TABLE_NAME", "lookout-dev-runs")
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


def write_run_record(state: dict[str, Any], timestamp: str | None = None) -> None:
    """Write a complete run audit record to DynamoDB.

    `timestamp` is the runs-table sort key. When the worker has been writing
    in-flight progress to a row keyed by the run's started_at, pass that same
    value here so this final put_item *overwrites* the running row instead of
    creating a sibling. Defaults to now() for legacy callers.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: would write run record for %s", state.get("run_id"))
        return

    table = _get_table()

    # Snapshot the active config so each run is auditable: which preferences
    # produced which scored deals? Snapshot the per-user view when present.
    config_snapshot: dict[str, Any] = {}
    try:
        from src.config.loader import load_preferences

        prefs = load_preferences(user_id=state.get("user_id"))
        config_snapshot = prefs.model_dump(mode="json")
    except Exception:
        logger.exception("Failed to snapshot config for run record — continuing")

    record = {
        "run_id": state["run_id"],
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
        "environment": state.get("environment", "unknown"),
        "status": state.get("status", "UNKNOWN"),
        "nodes_executed": state.get("nodes_executed", []),
        "deals_found": len(state.get("scored_deals", [])),
        "deals_above_threshold": len(state.get("deals_above_threshold", [])),
        "drafts_produced": len(state.get("email_drafts", [])),
        "guardrail_triggers": state.get("guardrail_triggers", []),
        "token_usage": state.get("token_usage", {}),
        "total_cost_usd": state.get("total_cost_usd", 0),
        "config_snapshot": config_snapshot,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    # Carry through optional fields the in-flight writer may have set so the
    # finalized record stays informative.
    if state.get("started_at"):
        record["started_at"] = state["started_at"]
    if state.get("source_errors"):
        record["source_errors"] = state["source_errors"]
    # Only set user_id if state carries one — keeps the GSI sparse for legacy rows.
    user_id = state.get("user_id")
    if user_id:
        record["user_id"] = user_id

    table.put_item(Item=_convert_floats(record))
    logger.info("Wrote run record %s", state["run_id"])


def get_run_record(run_id: str) -> dict[str, Any] | None:
    """Retrieve a run record by run_id."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return None

    table = _get_table()
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("run_id").eq(run_id),
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None
