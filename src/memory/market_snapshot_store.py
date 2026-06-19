"""DynamoDB read/write for per-run market-signal snapshots.

A snapshot is a small derived view: composite index, factor contributions,
flower position, variance — Lookout's own computed numbers, NOT raw
MarketCheck rows. That distinction matters for TTL: snapshots have no TTL
(they back trend charts up to 24 months), but they're so light it's fine.

Keys:
  snapshot_key (PK):
    "<user_id>"               for personalized signals (most rows)
    "market:zip:<zip_code>"   for the synthetic per-zip "market" signal
  timestamp (SK): ISO-8601 UTC

The optional GSI `zip-code-index` lets us look up all market-zip rows for
a given zip across users — needed when a real user logs in and we want to
serve them the latest snapshot for their zip without scanning the table.
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
    name = os.environ.get("MARKET_SNAPSHOTS_TABLE_NAME", "lookout-dev-market-snapshots")
    return boto3.resource("dynamodb").Table(name)


def _floats_to_decimals(obj: Any) -> Any:
    """DynamoDB rejects floats — convert to Decimal first (recursive)."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _floats_to_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimals(v) for v in obj]
    return obj


def _decimals_to_native(obj: Any) -> Any:
    """boto3 returns Decimals for DynamoDB numbers; convert back for serialization."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _decimals_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimals_to_native(v) for v in obj]
    return obj


# ---- Write ----


def write_snapshot(
    *,
    snapshot_key: str,
    timestamp: str | None = None,
    zip_code: str | None = None,
    segment_key: str | None = None,
    listing_count: int = 0,
    median_discount_pct: float | None = None,
    median_eff_price_usd: float | None = None,
    factor_contributions: dict[str, float] | None = None,
    macro_index: float = 0.0,
    segment_index: float = 0.0,
    personal_index: float = 0.0,
    composite_index: float = 0.0,
    variance_score: float = 0.0,
    flower_position: float = 0.0,
    label: str = "Quiet",
    headline_text: str = "",
    partial: bool = False,
    prefs_snapshot: dict[str, Any] | None = None,
) -> None:
    """Write a per-run snapshot row.

    Idempotent on (snapshot_key, timestamp) — re-running the same run
    overwrites instead of duplicating, which keeps the chart honest.

    Suppressed in TEST_MODE so the agent's eval suite doesn't write to AWS.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: would write market snapshot %s @ %s", snapshot_key, timestamp)
        return

    ts = timestamp or datetime.now(UTC).isoformat()
    record: dict[str, Any] = {
        "snapshot_key": snapshot_key,
        "timestamp": ts,
        "listing_count": int(listing_count),
        "macro_index": macro_index,
        "segment_index": segment_index,
        "personal_index": personal_index,
        "composite_index": composite_index,
        "variance_score": variance_score,
        "flower_position": flower_position,
        "label": label,
        "headline_text": headline_text,
        "partial": partial,
        "factor_contributions": factor_contributions or {},
    }
    # Optional fields — only include when meaningful so the GSI stays sparse.
    if zip_code:
        record["zip_code"] = zip_code
    if segment_key:
        record["segment_key"] = segment_key
    if median_discount_pct is not None:
        record["median_discount_pct"] = median_discount_pct
    if median_eff_price_usd is not None:
        record["median_eff_price_usd"] = median_eff_price_usd
    # `prefs_snapshot` captures the search criteria the run was scored against
    # so the 90-day calibration audit (see ADR 010) can compare apples-to-apples:
    # we shouldn't credit (or blame) the signal for moves caused by the user
    # rewriting their prefs mid-window. Only stamp when the caller provides one
    # — legacy rows omit the attribute and the API treats absence as "unknown".
    if prefs_snapshot:
        record["prefs_snapshot"] = prefs_snapshot

    _table().put_item(Item=_floats_to_decimals(record))
    logger.info(
        "Wrote market snapshot key=%s ts=%s composite=%.1f label=%s",
        snapshot_key,
        ts,
        composite_index,
        label,
    )


# ---- Read ----


_WINDOW_DAYS: dict[str, int] = {
    "30d": 30,
    "90d": 90,
    "6m": 180,
    "12m": 365,
    "24m": 730,
}


def query_snapshots(
    snapshot_key: str,
    *,
    window: str = "90d",
    include_partial: bool = True,
) -> list[dict[str, Any]]:
    """Return snapshots for one key over the window, newest-first.

    Window keys: 30d, 90d, 6m, 12m, 24m. Unknown windows fall back to 90d.

    `include_partial` controls whether guardrail-blocked rows (computed from
    incomplete data) appear in the result. The chart wants them in (as greyed
    dots); the trailing-baseline math wants them out. Caller decides.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return []

    days = _WINDOW_DAYS.get(window, 90)
    earliest = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    resp = _table().query(
        KeyConditionExpression=Key("snapshot_key").eq(snapshot_key)
        & Key("timestamp").gte(earliest),
        ScanIndexForward=False,  # newest first
    )
    items = resp.get("Items", []) or []
    if not include_partial:
        items = [it for it in items if not it.get("partial")]
    return [_decimals_to_native(it) for it in items]
