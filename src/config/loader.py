"""Loads and validates user preferences.

Three-layer config (later layers override earlier ones):
  1. Bundled defaults from src/config/preferences.yaml (in Docker image)
  2. Global overrides from DynamoDB ConfigTable (key="active")
  3. Per-user overrides from DynamoDB UsersTable (when user_id given)

The merged dict is validated by Pydantic. Lists in overrides replace base lists.
"""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class IncomeTier(StrEnum):
    """Coarse household-income brackets for federal EV credit eligibility checks.

    The IRS Section 30D limits for direct purchase: $150k single / $225k HOH /
    $300k MFJ. Brackets defined at single-filer thresholds; users in the
    "above_300k_mfj" bracket are over the cap for direct purchase regardless of
    filing status, but can capture the credit via lease (Section 45W).
    """

    UNDER_75K = "under_75k"
    UNDER_150K = "under_150k"
    UNDER_300K = "under_300k"
    ABOVE_300K = "above_300k"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"  # treated as "above" for safety


class VehicleConfig(BaseModel):
    vin: str
    mileage: int = Field(ge=0)
    condition: str
    trade_in_floor_usd: float = Field(gt=0)


class SearchConfig(BaseModel):
    location_zip: str
    # MarketCheck's free tier caps search radius at 100 miles. We accept any
    # positive value at the model boundary (so legacy prefs from before this
    # cap don't fail to load) and clamp down to 100 in the validator below.
    radius_miles: int = Field(gt=0)
    max_vehicle_age_years: int = Field(gt=0, le=10)

    @field_validator("radius_miles", mode="before")
    @classmethod
    def _clamp_radius_to_marketcheck_max(cls, v: Any) -> Any:
        """Quietly clamp legacy radius_miles > 100 down to MarketCheck's free-tier max.

        Existing users may have radius_miles > 100 in their stored prefs from
        before we enforced this cap. Rejecting at load would break their
        dashboard; clamping migrates them transparently.
        """
        if isinstance(v, int | float) and v > 100:
            return 100
        return v

    body_styles: list[str]
    # MarketCheck fuel-type values: "Electric", "Hybrid", "Plug-in Hybrid",
    # "Gasoline", "Diesel", "Flex Fuel", "Hydrogen". Empty list = no filter.
    fuel_types: list[str] = Field(default_factory=lambda: ["Electric"])
    # Price range filter (USD). 0 means no bound on that side.
    min_price_usd: float = Field(default=0, ge=0)
    max_price_usd: float = Field(default=0, ge=0)
    # Max odometer reading (miles). 0 means no upper bound — same convention
    # as the price fields above. Filters out high-mileage vehicles client-side
    # since MarketCheck's `max_miles` param is unreliable for some plans.
    max_mileage_miles: int = Field(default=0, ge=0, le=500_000)
    # Adaptive pagination — fetch until we have target_listings useful results
    # OR we hit max_pages, whichever comes first. Hard cap at 500 to keep LLM
    # scoring costs predictable; tier-level caps (DEFAULT_TIER_CAPS) clamp
    # default-tier users to 50 server-side regardless of what they submit.
    target_listings: int = Field(default=50, ge=1, le=500)
    max_pages: int = Field(default=10, ge=1, le=100)


class DealCriteria(BaseModel):
    max_effective_monthly_usd: float = Field(gt=0)
    min_discount_off_msrp_pct: float = Field(ge=0, le=100)
    acceptable_apr_max: float = Field(ge=0)
    lease_to_own_preferred: bool
    zero_percent_financing_preferred: bool


class ScoringConfig(BaseModel):
    threshold_notify: float = Field(ge=0.0, le=1.0)
    threshold_draft_email: float = Field(ge=0.0, le=1.0)


class ScheduleConfig(BaseModel):
    """When to run for this user.

    days_of_week: weekdays the agent runs, 0=Monday … 6=Sunday. Any non-empty
        subset: [3]=weekly Thursday (the default), [0,3]=Mondays & Thursdays,
        all seven=daily. Normalized to a sorted, de-duplicated list.
    time_of_day_utc: HH:MM in UTC. The scheduler runs hourly and queues users
        whose next_run_at <= now, so granularity is one hour.
    """

    days_of_week: list[int] = Field(default_factory=lambda: [3])
    time_of_day_utc: str = Field(default="13:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("days_of_week")
    @classmethod
    def _normalize_days(cls, v: list[int]) -> list[int]:
        cleaned = sorted(set(v))
        if not cleaned:
            raise ValueError("days_of_week must list at least one weekday (0=Mon … 6=Sun)")
        if cleaned[0] < 0 or cleaned[-1] > 6:
            raise ValueError("days_of_week entries must be in 0-6 (0=Mon to 6=Sun)")
        return cleaned


class Preferences(BaseModel):
    vehicle: VehicleConfig
    search: SearchConfig
    # Empty included_brands = no whitelist (all brands allowed).
    # excluded_brands wins over included_brands if both name the same brand.
    included_brands: list[str] = Field(default_factory=list)
    excluded_brands: list[str]
    excluded_models: list[str]
    deal_criteria: DealCriteria
    scoring: ScoringConfig
    # Per-user schedule (BYOK editable; default tier locked to system defaults).
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    # Coarse household-income bracket for federal EV credit (IRS Section 30D)
    # eligibility checks. Personal field — editable on both default and BYOK
    # tiers. Defaults to "prefer_not_to_say" which the credit logic treats as
    # the conservative (does-not-qualify-direct) worst case.
    income_tier: IncomeTier = Field(default=IncomeTier.PREFER_NOT_TO_SAY)


def load_preferences(
    path: Path | None = None,
    *,
    apply_overrides: bool = True,
    user_id: str | None = None,
) -> Preferences:
    """Load and validate preferences.

    Args:
        path: Path to defaults YAML. Defaults to src/config/preferences.yaml.
        apply_overrides: If True (default), merge DynamoDB overrides.
            Set False for offline/test scenarios where DynamoDB isn't available.
        user_id: When given, merge that user's overrides on top of bundled defaults
            and global overrides. Single-tenant runs (no SQS event) pass None.
    """
    if path is None:
        path = Path(__file__).parent / "preferences.yaml"

    if not path.exists():
        raise FileNotFoundError(f"Preferences file not found: {path}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Preferences file must contain a YAML mapping, got {type(raw).__name__}")

    user_tier = "default"
    if apply_overrides and not _is_test_mode():
        global_overrides = _load_global_overrides()
        if global_overrides:
            raw = deep_merge(raw, global_overrides)
            logger.info("Applied global DynamoDB overrides")

        if user_id:
            user_overrides, user_tier = _load_user_overrides(user_id)
            if user_overrides:
                raw = deep_merge(raw, user_overrides)
                logger.info("Applied per-user overrides for user_id=%s", user_id)

    # Clamp tier-locked fields at LOAD time. Default-tier users land here even
    # when their stored overrides don't set the capped fields — the YAML
    # defaults would otherwise exceed MarketCheck's free-tier pagination
    # limit and trip a 422 on page 2 requests.
    raw = _apply_tier_caps(raw, user_tier)

    return Preferences(**raw)


def merged_overrides_dict(user_id: str | None = None) -> dict[str, Any]:
    """Return the raw merged dict (without Pydantic validation).

    Useful for the API's /me/prefs endpoint, which needs to expose the merged
    view as JSON without re-validating.
    """
    if user_id:
        return load_preferences(user_id=user_id).model_dump(mode="json")
    return load_preferences().model_dump(mode="json")


def _is_test_mode() -> bool:
    return os.environ.get("TEST_MODE", "").lower() == "true"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base. Lists in override replace base.

    Public so other modules (like the API /me/prefs handler) can use the same
    merge semantics rather than reimplementing them.
    """
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_global_overrides() -> dict[str, Any]:
    """Fetch the active global overrides from the ConfigTable. Returns {} on error."""
    table_name = os.environ.get("CONFIG_TABLE_NAME", "")
    if not table_name:
        return {}

    try:
        import boto3

        table = boto3.resource("dynamodb").Table(table_name)
        response = table.get_item(Key={"key": "active"})
        item = response.get("Item") or {}
        overrides = item.get("overrides") or {}
        if not isinstance(overrides, dict):
            logger.warning("ConfigTable 'active' item has non-dict overrides — ignoring")
            return {}
        return _decimal_to_native(overrides)
    except Exception:
        logger.exception("Failed to load global overrides — using defaults only")
        return {}


def _load_user_overrides(user_id: str) -> tuple[dict[str, Any], str]:
    """Fetch a user's overrides + tier from the UsersTable.

    Returns `({}, "default")` if missing/error so callers always get a usable
    tier for the cap-clamp decision downstream. The tier is needed because
    DEFAULT_TIER_CAPS (max_pages, target_listings) must also apply at LOAD
    time, not just at write time — otherwise the bundled YAML defaults can
    exceed MarketCheck's free-tier pagination limit for users who've never
    written prefs through the UI.
    """
    table_name = os.environ.get("USERS_TABLE_NAME", "")
    if not table_name:
        return {}, "default"

    try:
        import boto3

        table = boto3.resource("dynamodb").Table(table_name)
        response = table.get_item(Key={"user_id": user_id})
        item = response.get("Item") or {}
        overrides = item.get("prefs_overrides") or {}
        if not isinstance(overrides, dict):
            logger.warning("UsersTable %s has non-dict prefs_overrides — ignoring", user_id)
            overrides = {}
        tier = item.get("tier") or "default"
        if tier not in {"default", "byok"}:
            logger.warning("UsersTable %s has unknown tier=%r — defaulting", user_id, tier)
            tier = "default"
        return _decimal_to_native(overrides), tier
    except Exception:
        logger.exception("Failed to load user overrides for %s — using defaults only", user_id)
        return {}, "default"


# Default-tier hard limits enforced at LOAD time. These mirror the
# `DEFAULT_TIER_CAPS` in `src/api/_user_repo.py` (the write-side cap). We
# duplicate the numbers here rather than import across layers — the
# write-side cap is API-concern, the load-side cap is config-concern, and
# both should change together on the rare occasions they move.
_LOAD_TIER_CAPS_DEFAULT: dict[str, int] = {
    "max_pages": 1,  # MarketCheck free tier hard-caps pagination at 500 rows total
    "target_listings": 500,
}


def _apply_tier_caps(raw: dict[str, Any], tier: str) -> dict[str, Any]:
    """Clamp tier-locked fields. Default tier only; BYOK is unaffected.

    Idempotent: applying twice yields the same result. Mutates the nested
    `search` dict in place via deep_merge semantics rather than reassigning,
    so any other keys present are preserved.
    """
    if tier != "default":
        return raw
    search = raw.get("search")
    if not isinstance(search, dict):
        return raw
    for field, ceiling in _LOAD_TIER_CAPS_DEFAULT.items():
        if field in search and isinstance(search[field], (int, float)) and search[field] > ceiling:
            search[field] = ceiling
    return raw


def _decimal_to_native(obj: Any) -> Any:
    """Convert DynamoDB Decimal values to int/float for Pydantic compatibility."""
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_native(v) for v in obj]
    return obj
