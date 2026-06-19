"""DynamoDB time-series for trade-in value tracking.

Records trade-in estimates over time to enable depreciation analysis.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)


def _get_table():
    table_name = os.environ.get("TRADE_IN_TABLE_NAME", "lookout-dev-trade-in")
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


def write_trade_in_estimate(
    trade_in_data: dict[str, Any],
    run_id: str,
) -> None:
    """Write a trade-in estimate snapshot to DynamoDB."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: would write trade-in estimate for run %s", run_id)
        return

    table = _get_table()
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    item = {
        "vin": trade_in_data["vin"],
        "date": today,
        "run_id": run_id,
        "sources": trade_in_data.get("sources", []),
        "average_estimate_usd": trade_in_data.get("average_estimate_usd", 0),
        "lowest_estimate_usd": trade_in_data.get("lowest_estimate_usd", 0),
        "highest_estimate_usd": trade_in_data.get("highest_estimate_usd", 0),
        "recorded_at": datetime.now(UTC).isoformat(),
    }

    table.put_item(Item=_convert_floats(item))
    logger.info("Wrote trade-in estimate for VIN %s on %s", trade_in_data["vin"], today)


def get_trade_in_history(vin: str, limit: int = 30) -> list[dict[str, Any]]:
    """Retrieve trade-in value history for a VIN, most recent first."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return []

    table = _get_table()
    response = table.query(
        KeyConditionExpression=Key("vin").eq(vin),
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])
