"""Calibration log API — backs `GET /me/calibration`.

This endpoint exposes the raw audit material for the 90-day calibration loop
described in ADR 010. It returns a window of the user's market-signal
snapshots projected to a stable wire shape, plus a summary of how often the
signal landed in each editorial label. The 90-day comparison (was "Now"
followed by lower next-30d median? was "Not yet" followed by higher?) is
computed offline against this same data, not here — the endpoint is read-only
audit material, not a verdict.

Design contracts (called out in the eval suite):

  - Never raises. Empty store → `points=[]`, summary has zeros across the
    board. A malformed row gets silently dropped (per-row safeParse pattern,
    matches the frontend `MarketSignalSchema` tolerance) rather than 500-ing
    the whole response.
  - `include_partial=False` at the store boundary so guardrail-blocked
    runs (computed from incomplete data) don't poison the calibration math.
    The chart view (`/me/signal`) can re-fetch with partials if needed; the
    audit must not see them.
  - Unknown windows fall through as the original string (no 400). The store
    layer maps anything outside the known list to 90d; we surface whatever
    the caller asked for in the `window` field so a stale dashboard tab
    doesn't see hard failures after the window list changes.

The persisted column is `factor_contributions`; the wire field is
`factor_contributions` here (not `factor_contribs` like /me/signal) because
the calibration consumer is an offline auditor and prefers the long form for
readability. Both names ultimately point at the same column.
"""

from __future__ import annotations

import logging
from typing import Any

from src.memory.market_snapshot_store import query_snapshots

logger = logging.getLogger(__name__)


# The set of editorial labels the snapshot can carry. Mirrors the Literal in
# `src/guardrails/structural.py::MarketSnapshotPayload.label`. Kept as a
# constant here so the summary counts a known-closed set of buckets rather
# than discovering labels at runtime (a malformed label is silently dropped
# from the summary, same as a malformed row).
_KNOWN_LABELS: tuple[str, ...] = ("Now", "Quiet", "Not yet")


# Scalar fields a row must have to count as a valid point. Same idea as
# `_REQUIRED_POINT_FIELDS` in market_signal.py — if any is missing or the
# composite isn't numeric, we drop the row silently.
_REQUIRED_FIELDS: tuple[str, ...] = ("timestamp", "composite_index", "label")


def get_calibration(user_id: str, *, window: str = "90d") -> dict[str, Any]:
    """Return the user's snapshot history + label-bucket summary.

    Args:
        user_id: caller's Cognito sub. Used directly as the snapshot_key.
        window: one of 30d, 90d, 6m, 12m, 24m. Defaults to 90d (per ADR 010
            — the audit cycle runs at 90 days). Unknown values are silently
            mapped to 90d at the store layer; we echo whatever the caller
            sent in the response.

    Returns:
        Dict with `window`, `points` (newest-first, calibration-shaped),
        and `summary` (total counts + label buckets + temporal bounds).
        Never raises — store failures degrade to an empty result.
    """
    rows = _safe_query_snapshots(user_id, window=window)
    points = _project_points(rows)
    summary = _summarize(points)

    return {
        "window": window,
        "points": points,
        "summary": summary,
    }


def _safe_query_snapshots(user_id: str, *, window: str) -> list[dict[str, Any]]:
    """Pull non-partial snapshots, swallowing read errors.

    Calibration must never raise — an unavailable snapshot store should look
    like "no history yet" to the caller, not like a 500. Same defensive
    pattern as `_safe_prior_snapshots` in the persist node.
    """
    try:
        return query_snapshots(user_id, window=window, include_partial=False)
    except Exception:
        logger.exception("get_calibration: snapshot read failed for user_id=%s", user_id)
        return []


def _project_points(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project store rows to the calibration wire shape, dropping malformed rows.

    Drop rules:
      - Missing any required field in `_REQUIRED_FIELDS`
      - `composite_index` not numeric

    Newest-first ordering is preserved from the store; we don't re-sort.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        point = _project_one(row)
        if point is not None:
            out.append(point)
    return out


def _project_one(row: dict[str, Any]) -> dict[str, Any] | None:
    """Project a single store row; return None if it fails the row schema.

    Mirrors the per-row tolerance pattern in `market_signal._project_one`
    — but with a calibration-shaped projection (richer factor breakdown,
    macro/personal split, prefs snapshot when present).
    """
    for field in _REQUIRED_FIELDS:
        if field not in row or row[field] is None:
            logger.debug("Dropping calibration row: missing %s", field)
            return None
    composite = row.get("composite_index")
    if not isinstance(composite, (int, float)):
        logger.debug("Dropping calibration row: composite_index not numeric")
        return None

    point: dict[str, Any] = {
        "timestamp": row["timestamp"],
        "composite_index": float(composite),
        "label": row.get("label") or "Quiet",
        "flower_position": _as_float(row.get("flower_position"), default=0.0),
        "median_eff_price_usd": _as_optional_float(row.get("median_eff_price_usd")),
        "median_discount_pct": _as_optional_float(row.get("median_discount_pct")),
        "listing_count": _as_int(row.get("listing_count"), default=0),
        "factor_contributions": row.get("factor_contributions") or {},
        "macro_index": _as_float(row.get("macro_index"), default=0.0),
        "personal_index": _as_float(row.get("personal_index"), default=0.0),
        "variance_score": _as_float(row.get("variance_score"), default=0.0),
    }
    # Pass through the prefs snapshot when present so the auditor can verify
    # criteria parity at compare time. Absence is fine — legacy rows wrote
    # before this attribute existed; the auditor treats them as "criteria
    # unknown for this snapshot."
    prefs = row.get("prefs_snapshot")
    if isinstance(prefs, dict) and prefs:
        point["prefs_snapshot"] = prefs
    return point


def _summarize(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the summary block — counts per label + temporal bounds.

    Empty input → all-zero summary with `earliest`/`latest` as None. Counts
    are keyed by the editorial label set, so a snapshot with an unknown
    label (shouldn't happen — the guardrail rejects it on write — but the
    audit is allergic to crashes) doesn't increment any bucket.

    Wire field names mirror the spec in the task ADR: `lean_now_count`,
    `lean_not_yet_count`, `quiet_count`. Renaming on projection is cheap
    and lets the storage layer keep using the editorial label verbatim.
    """
    total = len(points)
    if total == 0:
        return {
            "total_snapshots": 0,
            "earliest": None,
            "latest": None,
            "lean_now_count": 0,
            "lean_not_yet_count": 0,
            "quiet_count": 0,
        }

    buckets = dict.fromkeys(_KNOWN_LABELS, 0)
    for point in points:
        label = point.get("label")
        if label in buckets:
            buckets[label] += 1

    # Points are newest-first; first = latest, last = earliest. Cheap rather
    # than sorting again.
    return {
        "total_snapshots": total,
        "earliest": points[-1]["timestamp"],
        "latest": points[0]["timestamp"],
        "lean_now_count": buckets["Now"],
        "lean_not_yet_count": buckets["Not yet"],
        "quiet_count": buckets["Quiet"],
    }


def _as_float(value: Any, *, default: float) -> float:
    """Coerce to float; default on None / non-numeric."""
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_optional_float(value: Any) -> float | None:
    """Coerce to float, returning None when missing or non-numeric.

    Used for median_eff_price_usd / median_discount_pct, which the store
    deliberately omits on cold-start runs (no deals = no median). Carrying
    None through to the wire keeps the calibration auditor honest about
    what it has vs. inferring 0 from a missing value.
    """
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_int(value: Any, *, default: int) -> int:
    """Coerce to int; default on None / non-numeric."""
    if isinstance(value, (int, float)):
        return int(value)
    return default
