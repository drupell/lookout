"""DynamoDB read/write for the external macro-series cache.

Holds time-series values from external providers — FRED economic indicators
(auto loan rate, used-car CPI, total vehicle sales) and the monthly Manheim
Used Vehicle Value Index. The cache is the load-bearing piece in the
cross-source resilience design: when a provider is unavailable, the
persist_market_snapshot node falls back to the most recent cached value
and badges the UI surface so users know the data is stale, instead of
blanking the Macro layer entirely.

Keys:
  series_key (PK): "<provider>:<series_id>"
    e.g. "fred:TERMCBAUTO48NS"   (48-mo new car loan rate)
    e.g. "fred:CUUR0000SETA02"   (CPI used cars)
    e.g. "manheim:headline"      (MUVVI top-line index)
    e.g. "manheim:pickup"        (MUVVI segment index)
  timestamp (SK): ISO-8601 UTC of the observation
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)


def _table():
    name = os.environ.get("MACRO_SERIES_TABLE_NAME", "lookout-dev-macro-series")
    return boto3.resource("dynamodb").Table(name)


def _floats_to_decimals(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _floats_to_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimals(v) for v in obj]
    return obj


def _decimals_to_native(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _decimals_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimals_to_native(v) for v in obj]
    return obj


# ---- Write ----


def write_observation(
    *,
    series_key: str,
    timestamp: str,
    value: float,
    units: str | None = None,
    provider_metadata: dict[str, Any] | None = None,
) -> None:
    """Cache one observation from an external provider.

    `timestamp` is the *observation* time (e.g. the month FRED tagged a
    rate to), not the time we fetched it. This way re-fetching the same
    month is idempotent — we just overwrite the same (PK, SK).
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: would cache %s @ %s = %s", series_key, timestamp, value)
        return

    record: dict[str, Any] = {
        "series_key": series_key,
        "timestamp": timestamp,
        "value": value,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    if units:
        record["units"] = units
    if provider_metadata:
        record["provider_metadata"] = provider_metadata

    _table().put_item(Item=_floats_to_decimals(record))
    logger.info("Cached macro series %s @ %s = %s", series_key, timestamp, value)


# ---- Read ----


def get_latest(series_key: str) -> dict[str, Any] | None:
    """Return the newest cached observation for a series, or None.

    The persist_market_snapshot node calls this when composing the Macro
    layer. None means "no cached value" — caller decides whether to skip
    the factor (graceful degradation) or fail loud.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return None

    resp = _table().query(
        KeyConditionExpression=Key("series_key").eq(series_key),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", []) or []
    return _decimals_to_native(items[0]) if items else None


def get_history(series_key: str, *, days: int = 365) -> list[dict[str, Any]]:
    """Return cached observations for a series over the last N days, newest first."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return []

    earliest = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    resp = _table().query(
        KeyConditionExpression=Key("series_key").eq(series_key) & Key("timestamp").gte(earliest),
        ScanIndexForward=False,
    )
    items = resp.get("Items", []) or []
    return [_decimals_to_native(it) for it in items]
