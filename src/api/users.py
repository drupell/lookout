"""User profile handlers — read/upsert the Users metadata table.

Auto-provisions a default-tier user on first call. Tier upgrades to BYOK happen
self-service via PUT /me/byok-key (see src/api/byok.py).
"""

from __future__ import annotations

import logging
from typing import Any

from src.api._user_repo import get_user, set_next_run_at, tier_caps, upsert_user

logger = logging.getLogger(__name__)


def get_me(user_id: str, email: str) -> dict[str, Any]:
    """Return the user's profile, auto-provisioning on first hit."""
    user = get_user(user_id)
    if user is None:
        user = upsert_user(user_id, email=email)

    tier = user.get("tier", "default")
    return {
        "user_id": user_id,
        "email": user.get("email", email),
        "tier": tier,
        "tier_caps": tier_caps(tier),
        # Boolean only — never expose the ARN to the client. The dashboard
        # uses this to decide whether to show "Add key" vs "Rotate / Remove".
        "marketcheck_secret_configured": bool(user.get("marketcheck_secret_arn")),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        # ISO-8601 of the most recent completed run, or null. Used by the
        # dashboard's "settings changed since last run" stale-warning.
        "last_run_at": user.get("last_run_at"),
        # ISO-8601 (UTC) of the next scheduled run. Computed + persisted on
        # first sight so the scheduler and the dashboard agree, and a brand-new
        # user immediately sees when fresh listings will land.
        "next_run_at": _ensure_next_run_at(user_id, user),
    }


def _ensure_next_run_at(user_id: str, user: dict[str, Any]) -> str | None:
    """Return next_run_at, computing + persisting it once if not yet set."""
    existing = user.get("next_run_at")
    if existing:
        return str(existing)

    from datetime import UTC, datetime

    from src.config.loader import load_preferences
    from src.scheduling import next_run_after

    try:
        schedule = load_preferences(user_id=user_id).schedule
        next_run = next_run_after(schedule, datetime.now(UTC)).isoformat()
    except Exception:
        logger.exception("Failed to compute next_run_at for user_id=%s", user_id)
        return None

    try:
        set_next_run_at(user_id, next_run)
    except Exception:
        # Non-fatal — the scheduler computes the same value lazily anyway.
        logger.exception("Failed to persist next_run_at for user_id=%s", user_id)
    return next_run
