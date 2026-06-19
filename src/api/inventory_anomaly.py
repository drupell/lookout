"""Inventory-anomaly handler — backs `GET /me/inventory-anomaly`.

Surfaces "your matching-listing count is unusually thick / unusually thin"
as one of the supporting metric pills around the market-trend chart. The
pill fires only in two editorially-interesting states — the latest run's
`listing_count` is materially above or below the user's own trailing
baseline. The boring middle returns None so the dashboard doesn't carry a
perpetually-noisy "inventory: normal" pill.

The compute leans on data we already persist on every run: each market
snapshot row stamps `listing_count`. So no new external dependency, no
new table — we just project the existing snapshot history into a verdict.

Resilience contract: read-only handler, never raises. Store failures,
empty history, and below-threshold history all degrade to `None`; the
frontend hides the pill rather than rendering a placeholder.
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

from src.memory.market_snapshot_store import query_snapshots

logger = logging.getLogger(__name__)

# Window we look back across for the trailing baseline. 90d gives enough
# samples to median through one bad week (e.g. a holiday gap) while still
# tracking the seasonality the user actually shops on.
_WINDOW = "90d"

# Need at least one current observation plus three trailing ones to take a
# median we'd defend in a review. Below this we suppress the pill rather
# than fire a verdict off too-thin history.
_MIN_HISTORY = 4

# Verdict thresholds — current vs trailing baseline, ratio.
#
# Tuned to fire on "you'd notice if you were watching" moves, not the
# everyday week-to-week jitter. Inventory swings +/-10-15% in normal weeks;
# a 1.8x thick or <=0.55x thin call has to clear that noise floor
# decisively to earn the pill's real estate.
_THICK_RATIO = 1.8
_THIN_RATIO = 0.55

# Don't compute a meaningful ratio when the baseline rounds to zero — the
# math goes to infinity for any positive `current_count` and "infinity-x
# usual" is not a verdict we'd defend at a 1 a.m. review. Suppress.
_MIN_BASELINE_COUNT = 1


def get_inventory_anomaly(user_id: str) -> dict[str, Any] | None:
    """Compute the user's matching-listings anomaly verdict, or None.

    Returns the payload described in the route contract when the latest
    `listing_count` falls outside the trailing-baseline band, or None when:
      - we have fewer than `_MIN_HISTORY` valid snapshots,
      - the trailing baseline is effectively zero,
      - the ratio falls in the boring middle, or
      - the snapshot store raises.

    Never raises — wrapping query failures in `None` is the same idiom the
    other supporting-metric handlers (`/me/used-vs-new-arbitrage`, etc.)
    use, so the dashboard's `Promise.allSettled` always sees a fulfilled
    promise for this surface.
    """
    rows = _query_history_safe(user_id)
    if rows is None or len(rows) < _MIN_HISTORY:
        return None

    # Snapshots are returned newest-first; the head is "current," the rest
    # is the trailing baseline. Skipping the head in the median call keeps
    # the comparison apples-to-apples — we're asking "does today look like
    # the recent weeks?" not "does today look like itself."
    current_row = rows[0]
    history_rows = rows[1:]

    current_count = _coerce_count(current_row.get("listing_count"))
    history_counts = [
        c for c in (_coerce_count(r.get("listing_count")) for r in history_rows) if c is not None
    ]
    if current_count is None or len(history_counts) < _MIN_HISTORY - 1:
        return None

    baseline_count = round(statistics.median(history_counts))
    if baseline_count < _MIN_BASELINE_COUNT:
        return None

    ratio = current_count / baseline_count
    verdict = _classify(ratio)
    if verdict == "normal":
        return None

    as_of = current_row.get("timestamp")
    if not isinstance(as_of, str) or not as_of:
        return None

    return {
        "current_count": current_count,
        "baseline_count": baseline_count,
        # One decimal is plenty at this surface — "1.8x" reads cleaner than
        # "1.83333x" and the frontend pill rounds again for display anyway.
        "ratio": round(ratio, 2),
        "verdict": verdict,
        "as_of": as_of,
    }


def _classify(ratio: float) -> str:
    """Bucket the ratio into `thick` / `thin` / `normal`.

    "thick"  — current is >=1.8x the trailing baseline. UI surfaces this as
               "unusually thick inventory in your radius." Often means
               dealers are stocking up ahead of a known calendar push.
    "thin"   — current is <=0.55x the trailing baseline. UI surfaces this as
               "unusually thin inventory in your radius." Often means a
               supply pinch (chip shortage echo, segment-wide sellout).
    "normal" — boring middle; pill suppressed.
    """
    if ratio >= _THICK_RATIO:
        return "thick"
    if ratio <= _THIN_RATIO:
        return "thin"
    return "normal"


def _coerce_count(value: Any) -> int | None:
    """Coerce a snapshot row's listing_count to a non-negative int, or None."""
    if isinstance(value, bool):  # bools are ints in Python; reject explicitly
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        return v if v >= 0 else None
    return None


def _query_history_safe(user_id: str) -> list[dict[str, Any]] | None:
    """Query trailing snapshots for the user; None on any store failure.

    Partial runs are excluded — the calibration audit log already pays the
    price of including them; this surface is editorial, so a partial run's
    half-populated `listing_count` would steer the verdict in the wrong
    direction. Better to wait for a clean run.
    """
    # Handler-level swallow per the resilience contract — never raise into
    # the dashboard's Promise.allSettled. We log and degrade to None; the
    # frontend hides the pill rather than rendering a placeholder.
    try:
        return query_snapshots(user_id, window=_WINDOW, include_partial=False)
    except Exception:
        logger.exception("inventory-anomaly: snapshot query raised for user=%s", user_id)
        return None
