"""Users table access + tier capability definitions.

The dashboard auto-provisions a user row on first /me call. Default tier has
restricted writable fields; BYOK tier unlocks everything (and stores the user's
own MarketCheck API key in their own Secrets Manager secret).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3

logger = logging.getLogger(__name__)

# Fields a default-tier user can write (dotted paths into Preferences).
DEFAULT_WRITABLE_PATHS: frozenset[str] = frozenset(
    {
        "search.location_zip",
        "search.radius_miles",
        "search.max_vehicle_age_years",
        "search.fuel_types",
        "search.body_styles",
        "search.target_listings",  # default tier: clamped to DEFAULT_TIER_CAPS
        "search.min_price_usd",
        "search.max_price_usd",
        "search.max_mileage_miles",
        "included_brands",
        "excluded_brands",
        "excluded_models",
        "vehicle.vin",
        "vehicle.mileage",
        "vehicle.condition",
        "vehicle.trade_in_floor_usd",
        "deal_criteria.max_effective_monthly_usd",
        "deal_criteria.min_discount_off_msrp_pct",
        "deal_criteria.acceptable_apr_max",
        "deal_criteria.lease_to_own_preferred",
        "deal_criteria.zero_percent_financing_preferred",
        # Personal field (not tier-gated) — coarse income bracket for
        # federal EV credit eligibility checks.
        "income_tier",
    }
)
# BYOK adds: pagination knobs (cost-impacting), scoring thresholds, and
# the schedule (when/how often the agent runs for this user).
# `target_listings` is in the default set too but BYOK can pick a higher value.
BYOK_WRITABLE_PATHS: frozenset[str] = DEFAULT_WRITABLE_PATHS | frozenset(
    {
        "search.max_pages",
        "scoring.threshold_notify",
        "scoring.threshold_draft_email",
        "schedule.days_of_week",
        "schedule.time_of_day_utc",
    }
)

# Caps the API enforces server-side for default-tier users (regardless of overrides).
DEFAULT_TIER_CAPS: dict[str, int] = {
    "max_pages": 1,
    "target_listings": 50,
}


def writable_paths_for_tier(tier: str) -> frozenset[str]:
    return BYOK_WRITABLE_PATHS if tier == "byok" else DEFAULT_WRITABLE_PATHS


def tier_caps(tier: str) -> dict[str, Any]:
    """Read-only metadata for the dashboard to render disabled fields."""
    if tier == "byok":
        return {
            "schedule_editable": True,
            "pagination_editable": True,
            "scoring_thresholds_editable": True,
            # Coarse income bracket — personal field, editable on both tiers.
            "income_tier_editable": True,
        }
    return {
        "schedule_editable": False,
        "pagination_editable": False,
        "scoring_thresholds_editable": False,
        # Coarse income bracket — personal field, editable on both tiers.
        "income_tier_editable": True,
        "max_pages": DEFAULT_TIER_CAPS["max_pages"],
        "target_listings": DEFAULT_TIER_CAPS["target_listings"],
        # Inform the dashboard what the locked schedule is so it can render it
        # as read-only without having to dig into the merged prefs.
        "fixed_schedule_note": "Default tier runs weekly. Upgrade to BYOK to customize.",
    }


def _table():
    name = os.environ["USERS_TABLE_NAME"]
    return boto3.resource("dynamodb").Table(name)


def get_user(user_id: str) -> dict[str, Any] | None:
    """Fetch a user record, or None if not provisioned yet."""
    resp = _table().get_item(Key={"user_id": user_id})
    item = resp.get("Item")
    return decimals_to_native(item) if item else None


def upsert_user(
    user_id: str,
    email: str,
    *,
    tier: str | None = None,
    prefs_overrides: dict[str, Any] | None = None,
    marketcheck_secret_arn: str | None = None,
) -> dict[str, Any]:
    """Create the row if missing; update tier/overrides/secret if given.

    Returns the full row after the upsert.
    """
    existing = get_user(user_id) or {}
    now = datetime.now(UTC).isoformat()

    item: dict[str, Any] = {
        "user_id": user_id,
        "email": email or existing.get("email", ""),
        "tier": tier or existing.get("tier", "default"),
        "prefs_overrides": prefs_overrides
        if prefs_overrides is not None
        else existing.get("prefs_overrides", {}),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
    }
    # Tri-state semantics: None = preserve existing, "" = explicitly clear,
    # any other str = set. Lets BYOK demotion drop the ARN cleanly.
    arn = (
        marketcheck_secret_arn
        if marketcheck_secret_arn is not None
        else existing.get("marketcheck_secret_arn")
    )
    if arn:
        item["marketcheck_secret_arn"] = arn

    _table().put_item(Item=floats_to_decimals(item))
    return item


def set_next_run_at(user_id: str, next_run_at: str) -> None:
    """Persist a user's computed `next_run_at` (ISO-8601 UTC).

    Written at provisioning so the scheduler and the dashboard agree on the
    same first-run time and a brand-new user immediately sees when they're due.
    """
    _table().update_item(
        Key={"user_id": user_id},
        UpdateExpression="SET next_run_at = :n",
        ExpressionAttributeValues={":n": next_run_at},
    )


def decimals_to_native(obj: Any) -> Any:
    """boto3 returns Decimals for DynamoDB numbers; convert for JSON serialization."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: decimals_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimals_to_native(v) for v in obj]
    return obj


def floats_to_decimals(obj: Any) -> Any:
    """DynamoDB rejects floats — convert to Decimal before write."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: floats_to_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [floats_to_decimals(v) for v in obj]
    return obj
