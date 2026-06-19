"""Market-signal handler — backs `GET /me/signal`.

Returns a time series of composite-index snapshots plus the latest point and
factor breakdown. Two views:

  view="personalized" — snapshots written under PK = user_id (the user's own runs)
  view="market"       — snapshots written under PK = "market:zip:<zip>" (the
                        synthetic per-zip "market user" runs)

The trailing-baseline math on the consuming end is poisoned by partial rows
(guardrail-blocked runs computed from incomplete data), so we always exclude
those via `include_partial=False`. A v2 chart-render endpoint can re-fetch
with partials included to grey them out on the chart per the plan.

Per-row tolerance: a single malformed snapshot (missing `composite_index`,
for example) is silently dropped from `points[]` rather than 500-ing the
whole response. Mirrors the frontend's per-row `safeParse + drop` pattern
so one bad row never blanks the chart.

The persisted column is `factor_contributions`; the response shape uses the
shorter `factor_contribs` key (matches the frontend schema). Renaming on
projection keeps the wire format stable even if the store column changes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.api._errors import HttpError
from src.api._user_repo import get_user
from src.memory.market_snapshot_store import query_snapshots

logger = logging.getLogger(__name__)


# Required scalar fields for a point to be considered valid. Missing any of
# these means the row predates a schema bump or was written by a buggy node
# — either way, drop it silently rather than failing the whole response.
_REQUIRED_POINT_FIELDS: tuple[str, ...] = (
    "timestamp",
    "composite_index",
)


def get_market_signal(
    user_id: str,
    window: str = "90d",
    view: str = "personalized",
) -> dict[str, Any]:
    """Return market-signal time series + latest point + factor breakdown.

    Args:
        user_id: caller's Cognito sub. Used directly as the snapshot_key for
            the personalized view; resolved to a zip for the market view.
        window: one of 30d, 90d, 6m, 12m, 24m. Unknown values are silently
            mapped to 90d at the store layer — we deliberately don't 400
            here so a stale dashboard tab doesn't see hard failures after
            the window list changes.
        view: "personalized" or "market". Anything else falls through as
            personalized (defensive; the frontend only emits the two values).

    Returns:
        Dict with `window`, `view`, `points` (newest-first), and `latest`
        (the first point, or None when there are no valid points).
    """
    if view == "market":
        zip_code = _resolve_user_zip(user_id)
        if not zip_code:
            raise HttpError(400, "No zip on file for market view")
        snapshot_key = f"market:zip:{zip_code}"
    else:
        snapshot_key = user_id

    rows = query_snapshots(snapshot_key, window=window, include_partial=False)
    points = _project_points(rows)
    latest = points[0] if points else None

    return {
        "window": window,
        "view": view,
        "points": points,
        "latest": latest,
    }


def _resolve_user_zip(user_id: str) -> str | None:
    """Resolve the user's zip for the market view.

    Preference order:
      1. `prefs_overrides.search.location_zip` on the user row (the field the
         dashboard actually writes to when the user edits their zip).
      2. The merged effective prefs from `load_preferences(user_id=...)` —
         covers the case where the zip is only set via global defaults.

    Returns None when neither source produces a string zip. The caller maps
    that to a 400.
    """
    user = get_user(user_id) or {}
    overrides_zip = (user.get("prefs_overrides") or {}).get("search", {}).get("location_zip")
    if isinstance(overrides_zip, str) and overrides_zip:
        return overrides_zip

    # Fall back to the merged prefs. Imported lazily so the API doesn't pull
    # the YAML loader into memory on every request that doesn't need it.
    try:
        from src.config.loader import load_preferences

        prefs = load_preferences(user_id=user_id)
    except Exception:
        logger.exception("Failed to load preferences for user_id=%s", user_id)
        return None

    location_zip = getattr(prefs.search, "location_zip", None)
    if isinstance(location_zip, str) and location_zip:
        return location_zip
    return None


def _project_points(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project store rows to the wire shape, dropping malformed rows.

    Drop rules:
      - missing any required scalar in `_REQUIRED_POINT_FIELDS`
      - `composite_index` not numeric

    Newest-first ordering is preserved from the store (we don't re-sort).
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        point = _project_one(row)
        if point is not None:
            out.append(point)
    return out


def _project_one(row: dict[str, Any]) -> dict[str, Any] | None:
    """Project a single row; return None if it fails validation.

    Renames the persisted `factor_contributions` column to `factor_contribs`
    so the wire format matches the frontend schema (one canonical name in
    JS-land; the DB column can evolve separately).
    """
    for field in _REQUIRED_POINT_FIELDS:
        if field not in row or row[field] is None:
            logger.debug("Dropping market snapshot row: missing %s", field)
            return None
    composite = row.get("composite_index")
    if not isinstance(composite, (int, float)):
        logger.debug("Dropping market snapshot row: composite_index not numeric")
        return None

    return {
        "timestamp": row["timestamp"],
        "composite_index": float(composite),
        "factor_contribs": row.get("factor_contributions") or {},
        "variance_score": _as_float(row.get("variance_score"), default=0.0),
        "flower_position": _as_float(row.get("flower_position"), default=0.0),
        "label": row.get("label") or "Quiet",
    }


def _as_float(value: Any, *, default: float) -> float:
    """Coerce to float, defaulting on None / non-numeric.

    Snapshots without a `variance_score` or `flower_position` (older rows
    written before those columns existed) read as the default rather than
    propagating None into the JSON response.
    """
    if isinstance(value, (int, float)):
        return float(value)
    return default
