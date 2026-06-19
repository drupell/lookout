"""Per-user preferences handlers.

Effective prefs = bundled YAML defaults + global config-table overrides + user overrides.
Default-tier users have a subset of fields locked (see _user_repo.writable_paths_for_tier).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from src.api._errors import HttpError
from src.api._user_repo import (
    DEFAULT_TIER_CAPS,
    get_user,
    upsert_user,
    writable_paths_for_tier,
)
from src.config.loader import deep_merge, load_preferences


def get_prefs(user_id: str) -> dict[str, Any]:
    """Return the user's effective preferences (defaults + global + user overrides)."""
    effective = load_preferences(user_id=user_id).model_dump(mode="json")
    user = get_user(user_id) or {}
    return {
        "tier": user.get("tier", "default"),
        "effective": effective,
        "user_overrides": user.get("prefs_overrides") or {},
        "writable_paths": sorted(writable_paths_for_tier(user.get("tier", "default"))),
    }


def put_prefs(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update the user's overrides. Returns the new merged effective view.

    Behavior:
      1. Look up user (auto-provision missing).
      2. Reject paths not in the writable set for the user's tier (400).
      3. For default-tier users, server-cap pagination knobs.
      4. Merge over existing overrides + bundled defaults, run through Pydantic
         validator. If invalid, reject (400) without writing.
      5. Persist the new overrides.
    """
    from src.config.loader import Preferences

    user = get_user(user_id)
    if user is None:
        # Auto-provision so we have a tier to enforce against
        user = upsert_user(user_id, email="")
    tier = user.get("tier", "default")
    allowed = writable_paths_for_tier(tier)

    flat = _flatten_paths(payload)
    rejected = sorted(p for p in flat if p not in allowed)
    if rejected:
        raise HttpError(
            403,
            f"Tier '{tier}' cannot modify these paths",
            details={"rejected_paths": rejected},
        )

    # Server-side caps (default tier only) — silently clamp instead of rejecting,
    # since these came from a UI that may be stale.
    if tier == "default":
        payload = _apply_default_caps(payload)

    # Merge over existing overrides (so PUT is partial-update friendly)
    existing_overrides = user.get("prefs_overrides") or {}
    new_overrides = deep_merge(existing_overrides, payload)

    # Validate by simulating the full merge. Capture the validated instance
    # so any field-level transformations (e.g. radius_miles clamped to the
    # MarketCheck free-tier max) flow through to the returned `effective`
    # view — otherwise the UI would render the user's raw input as the
    # effective value, contradicting what the agent will actually use.
    base_prefs = load_preferences().model_dump(mode="json")
    merged_for_validation = deep_merge(base_prefs, new_overrides)
    try:
        validated = Preferences(**merged_for_validation)
    except ValidationError as e:
        raise HttpError(400, "Preferences would be invalid", details={"errors": e.errors()}) from e

    upsert_user(
        user_id,
        email=user.get("email", ""),
        prefs_overrides=new_overrides,
    )

    return {
        "tier": tier,
        "user_overrides": new_overrides,
        "effective": validated.model_dump(mode="json"),
        "writable_paths": sorted(allowed),
    }


# --- helpers ---


def _flatten_paths(payload: dict[str, Any], prefix: str = "") -> set[str]:
    """Flatten {a: {b: 1}} → {"a.b"}; stops at non-dict leaves (incl. lists)."""
    paths: set[str] = set()
    for k, v in payload.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            paths |= _flatten_paths(v, path)
        else:
            paths.add(path)
    return paths


def _apply_default_caps(payload: dict[str, Any]) -> dict[str, Any]:
    """Clamp default-tier-only fields to their max permissible values."""
    from copy import deepcopy

    out = deepcopy(payload)
    search = out.setdefault("search", {})
    if "max_pages" in search:
        search["max_pages"] = min(search["max_pages"], DEFAULT_TIER_CAPS["max_pages"])
    if "target_listings" in search:
        search["target_listings"] = min(
            search["target_listings"], DEFAULT_TIER_CAPS["target_listings"]
        )
    return out
