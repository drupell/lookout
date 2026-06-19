"""BYOK MarketCheck key submission/removal.

Endpoints:
  PUT    /me/byok-key  → validate key, upsert lookout/byok/<user_id> secret,
                         set marketcheck_secret_arn, tier="byok"
  DELETE /me/byok-key  → delete secret, clear marketcheck_secret_arn,
                         demote tier="default", scrub BYOK-only prefs overrides

See docs/decisions/007-byok-key-via-api.md for the threat model.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import boto3
import httpx

from src.api._errors import HttpError
from src.api._user_repo import get_user, upsert_user
from src.tools.listings.marketcheck import MARKETCHECK_BASE_URL

logger = logging.getLogger(__name__)

# Path prefix that lets the API Lambda's IAM scope match all per-user secrets
# without granting access to the shared default-tier secret.
_BYOK_SECRET_PREFIX = "lookout/byok/"  # noqa: S105 — this is a path prefix, not a password

# Preference paths only BYOK-tier users can write. On tier demotion, scrub
# these from the user's overrides so a demoted user can't keep using BYOK
# perks they're no longer entitled to. Keep in sync with BYOK_WRITABLE_PATHS
# in _user_repo.py — that file is the source of truth for write gating.
_BYOK_ONLY_OVERRIDE_PATHS: frozenset[str] = frozenset(
    {
        "search.target_listings",
        "search.max_pages",
        "scoring.threshold_notify",
        "scoring.threshold_draft_email",
        "schedule.days_of_week",
        "schedule.time_of_day_utc",
    }
)


def put_byok_key(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + upsert the user's BYOK MarketCheck key, then upgrade tier."""
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        raise HttpError(400, "api_key is required")

    if not _validate_marketcheck_key(api_key):
        raise HttpError(
            400,
            "MarketCheck rejected the key — double-check it's active and try again",
        )

    secret_arn = _upsert_secret(user_id, api_key)

    user = get_user(user_id) or {}
    upsert_user(
        user_id,
        email=user.get("email", ""),
        tier="byok",
        marketcheck_secret_arn=secret_arn,
    )

    return {
        "tier": "byok",
        "marketcheck_secret_configured": True,
    }


def delete_byok_key(user_id: str) -> dict[str, Any]:
    """Remove the user's BYOK key, demote tier, and scrub BYOK-only overrides."""
    user = get_user(user_id)
    if not user or not user.get("marketcheck_secret_arn"):
        # Idempotent — no key, nothing to do.
        return {
            "tier": user.get("tier", "default") if user else "default",
            "marketcheck_secret_configured": False,
        }

    _delete_secret(user_id)

    scrubbed_overrides = _scrub_byok_paths(user.get("prefs_overrides") or {})

    upsert_user(
        user_id,
        email=user.get("email", ""),
        tier="default",
        prefs_overrides=scrubbed_overrides,
        marketcheck_secret_arn="",  # explicit clear; upsert_user removes empty ARNs
    )

    return {"tier": "default", "marketcheck_secret_configured": False}


# --- helpers ---


def _validate_marketcheck_key(api_key: str) -> bool:
    """One cheap MarketCheck call to confirm the key actually works."""
    try:
        resp = httpx.get(
            MARKETCHECK_BASE_URL,
            params={"api_key": api_key, "zip": "10001", "radius": 1, "rows": 1},
            timeout=10.0,
        )
    except httpx.HTTPError:
        logger.exception("MarketCheck validation request failed")
        # Network/timeout — treat as transient and reject so the user retries.
        return False
    if resp.status_code in (401, 403):
        return False
    if resp.status_code >= 500:
        # Don't penalize the user for MarketCheck being down — fail closed.
        logger.warning("MarketCheck validation got 5xx (%s); rejecting", resp.status_code)
        return False
    return resp.status_code == 200


def _secret_name(user_id: str) -> str:
    return f"{_BYOK_SECRET_PREFIX}{user_id}"


def _upsert_secret(user_id: str, api_key: str) -> str:
    """Create or update the user's secret. Returns the ARN."""
    import json

    client = boto3.client("secretsmanager")
    name = _secret_name(user_id)
    body = json.dumps({"api_key": api_key})

    try:
        client.describe_secret(SecretId=name)
        # Exists → update value
        client.put_secret_value(SecretId=name, SecretString=body)
        resp = client.describe_secret(SecretId=name)
        return resp["ARN"]
    except client.exceptions.ResourceNotFoundException:
        resp = client.create_secret(
            Name=name,
            Description=f"BYOK MarketCheck key for user {user_id}",
            SecretString=body,
        )
        return resp["ARN"]


def _delete_secret(user_id: str) -> None:
    """Delete with no recovery window so the user can re-create immediately."""
    client = boto3.client("secretsmanager")
    # Idempotent — already gone is success.
    with contextlib.suppress(client.exceptions.ResourceNotFoundException):
        client.delete_secret(
            SecretId=_secret_name(user_id),
            ForceDeleteWithoutRecovery=True,
        )


def _scrub_byok_paths(overrides: dict[str, Any]) -> dict[str, Any]:
    """Drop BYOK-only override paths so demoted users lose BYOK perks."""
    from copy import deepcopy

    out = deepcopy(overrides)
    for dotted in _BYOK_ONLY_OVERRIDE_PATHS:
        parts = dotted.split(".")
        node = out
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if isinstance(node, dict):
            node.pop(parts[-1], None)

    # Drop now-empty parent dicts (e.g., schedule: {} after all 3 fields stripped)
    for parent_key in list(out.keys()):
        if isinstance(out[parent_key], dict) and not out[parent_key]:
            del out[parent_key]

    return out
