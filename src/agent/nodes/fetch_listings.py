"""Fetch vehicle listings from configured sources (currently MarketCheck)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.state import AgentState

logger = logging.getLogger(__name__)


def fetch_listings(state: AgentState) -> dict[str, Any]:
    """Fetch listings. In TEST_MODE, returns fixture data; otherwise calls MarketCheck."""
    started_at = datetime.now(UTC).isoformat()
    tool_name = "marketcheck"
    source_error: str | None = None

    if os.environ.get("TEST_MODE", "").lower() == "true":
        tool_name = "fixture"
        fixture_path = (
            Path(__file__).parent.parent.parent.parent
            / "evals"
            / "fixtures"
            / "listings_sample.json"
        )
        raw_listings = json.loads(fixture_path.read_text()) if fixture_path.exists() else []
    else:
        raw_listings, source_error = _fetch_from_marketcheck(user_id=state.get("user_id"))

    completed_at = datetime.now(UTC).isoformat()

    node_record: dict[str, Any] = {
        "node": "fetch_listings",
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_calls": [{"tool": tool_name, "test_mode": state.get("test_mode", False)}],
        "output_record_count": len(raw_listings),
        "guardrail_result": "PENDING",
    }
    if source_error:
        node_record["source_error"] = source_error

    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)

    # Carry source_errors at run level so the dashboard / API summary can
    # surface "MarketCheck rate-limited" instead of "0 deals (we got nothing)".
    existing_errors = list(state.get("source_errors", []) or [])
    if source_error:
        existing_errors.append(f"marketcheck:{source_error}")

    out: dict[str, Any] = {
        "raw_listings": raw_listings,
        "nodes_executed": existing_nodes,
    }
    if existing_errors:
        out["source_errors"] = existing_errors
    return out


def _fetch_from_marketcheck(
    user_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch listings from MarketCheck using preferences for search criteria.

    Brand whitelist/blacklist + price filters are pushed into the client (which
    forwards what it can to MarketCheck and post-filters the rest).
    Model exclusions are still applied here since MarketCheck's model filter is
    awkward (couples to make).

    Returns a `(listings, error_code)` tuple. `listings` is empty on any
    failure (downstream nodes handle empty listings). `error_code` is one of:
        - "quota_exhausted" — monthly bucket on the shared key is gone
          (distinct from a transient rate-limit; the UI surfaces this as
          "monthly quota reached — resumes next month")
        - "pagination_exceeded" — MarketCheck 422'd a `start >= 500`
          request; on free tier, this means max_pages > 1 is structurally
          unsupported. UI guidance is "drop max_pages or upgrade plan."
        - "rate_limited" — MarketCheck returned 429 (transient throttle)
        - "auth_failed" — 401 (bad/missing API key)
        - "unavailable" — any other non-recoverable error
        - None — call succeeded (zero results is *not* an error)
    """
    from src.config.loader import load_preferences
    from src.memory.api_usage_store import byok_key_id, shared_key_id
    from src.tools.listings.marketcheck import (
        MarketCheckClient,
        MarketCheckPaginationExceeded,
        MarketCheckQuotaExhausted,
    )

    prefs = load_preferences(user_id=user_id)
    byok_secret_arn = _resolve_user_byok_secret_arn(user_id)
    # Identify the bucket so the per-key monthly counter increments correctly.
    # BYOK users get their own bucket; everyone else shares the default-tier key.
    api_key_id = byok_key_id(user_id) if byok_secret_arn and user_id else shared_key_id()

    try:
        client = MarketCheckClient(secret_arn_override=byok_secret_arn, api_key_id=api_key_id)
        listings = client.fetch_listings(
            zip_code=prefs.search.location_zip,
            radius_miles=prefs.search.radius_miles,
            max_vehicle_age_years=prefs.search.max_vehicle_age_years,
            fuel_types=prefs.search.fuel_types,
            included_brands=prefs.included_brands,
            excluded_brands=prefs.excluded_brands,
            min_price_usd=prefs.search.min_price_usd,
            max_price_usd=prefs.search.max_price_usd,
            target_count=prefs.search.target_listings,
            max_pages=prefs.search.max_pages,
        )
    except MarketCheckQuotaExhausted as e:
        # Pre-flight check tripped — distinct code so the dashboard messaging
        # is "monthly quota reached" rather than "rate-limited" (the latter
        # implies "try again in a minute," which is wrong for a monthly bucket).
        logger.warning("MarketCheck quota exhausted: %s", e)
        return [], "quota_exhausted"
    except MarketCheckPaginationExceeded as e:
        # Free-tier 500-row pagination ceiling. The fix is config (drop
        # max_pages) or upgrade — not a transient retry. Distinct code so
        # the dashboard can guide rather than imply "try again later."
        logger.warning("MarketCheck pagination exceeded: %s", e)
        return [], "pagination_exceeded"
    except RuntimeError as e:
        # MarketCheckClient raises RuntimeError with a known message for the
        # operationally-meaningful failure modes; map to a stable code.
        msg = str(e).lower()
        code = (
            "rate_limited"
            if "rate limit" in msg
            else "auth_failed"
            if "auth failed" in msg
            else "unavailable"
        )
        logger.warning("MarketCheck fetch failed (%s): %s", code, e)
        return [], code
    except Exception:
        logger.exception("MarketCheck fetch failed — continuing with empty listings")
        return [], "unavailable"

    # Max-mileage filter — applied client-side because MarketCheck's miles_max
    # query param isn't reliable across all plans. 0 = no cap, matching the
    # price-bound convention.
    max_mileage = prefs.search.max_mileage_miles
    if max_mileage:
        before = len(listings)
        listings = [lst for lst in listings if (lst.get("mileage") or 0) <= max_mileage]
        if before != len(listings):
            logger.info(
                "Dropped %d/%d listings via max_mileage_miles=%d",
                before - len(listings),
                before,
                max_mileage,
            )

    # Model exclusions (the only other filter we don't push to the client)
    excluded_models = {m.lower() for m in prefs.excluded_models}
    if excluded_models:
        before = len(listings)
        listings = [lst for lst in listings if lst.get("model", "").lower() not in excluded_models]
        if before != len(listings):
            logger.info(
                "Dropped %d/%d listings via excluded_models",
                before - len(listings),
                before,
            )

    return listings, None


def _resolve_user_byok_secret_arn(user_id: str | None) -> str | None:
    """Look up the BYOK secret ARN for this user, or None to use the shared key."""
    if not user_id:
        return None
    import os

    users_table_name = os.environ.get("USERS_TABLE_NAME")
    if not users_table_name:
        return None
    try:
        import boto3

        table = boto3.resource("dynamodb").Table(users_table_name)
        item = table.get_item(
            Key={"user_id": user_id},
            ProjectionExpression="marketcheck_secret_arn",
        ).get("Item")
        arn = (item or {}).get("marketcheck_secret_arn")
        return arn if isinstance(arn, str) and arn else None
    except Exception:
        # Don't fail the run on a Users-table read hiccup — fall back to shared key.
        logger.exception("Failed to resolve BYOK secret for user_id=%s", user_id)
        return None
