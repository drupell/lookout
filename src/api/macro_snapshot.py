"""Macro-snapshot handler — backs `GET /me/macro`.

Returns the current macro-series snapshot composed of FRED-cached series:
  * `auto_loan_apr` — 48-mo new car loan rate (`fred:TERMCBAUTO48NS`)
  * `cpi_used_cars` — used-car CPI (`fred:CUUR0000SETA02`)

Both fields are independently nullable. When the macro-series cache hasn't
been populated for a series (nightly cron hasn't run yet, FRED was down on
the last refresh, etc.) the corresponding field is `null` and the frontend
hides that pill rather than rendering a stale or misleading number.

Context label (`auto_loan_apr.context`) is a quiet editorial annotation:
  * "24-month high"   — current value within 5% of the 24mo trailing max
  * "near 12-mo low"  — current value within 5% of the 12mo trailing min
  * "near 12-mo high" — current value within 5% of the 12mo trailing max
                        but not at the 24mo high
  * null              — nothing newsworthy; pill renders just label + value

Read-only handler: never raises. Per-series failures degrade independently;
a cache-read exception on `auto_loan_apr` does not blank `cpi_used_cars`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.memory import macro_series_store

logger = logging.getLogger(__name__)


# FRED series keys cached by the nightly refresh cron. Centralized here so a
# rename in the puller propagates with one edit.
_APR_SERIES_KEY = "fred:TERMCBAUTO48NS"
_CPI_SERIES_KEY = "fred:CUUR0000SETA02"

# How far back to pull history for the context-label computation. 24 months
# gives us enough headroom to flag a "24-month high" call honestly; 13 months
# of CPI lets us compute mom + yoy without padding with zeros.
_APR_HISTORY_DAYS = 365 * 2
_CPI_HISTORY_DAYS = 400

# Within 5% of the trailing min/max counts as "near" — matches the editorial
# tone of the pill (we're not declaring "exact 24-month high" because monthly
# series rarely re-touch their precise prior peak; "near" is honest).
_NEAR_THRESHOLD_PCT = 5.0


def get_macro_snapshot(user_id: str) -> dict[str, Any]:
    """Return the current macro-series snapshot: auto loan APR + CPI used cars.

    Reads from macro_series_store (populated by the nightly refresh cron).
    Each field is null if no cached row exists — frontend hides the pill.
    Never raises.

    `user_id` is accepted for parity with the other `/me/*` handlers even
    though the snapshot is currently identical for every user; per the plan
    macro is a US-wide read. A future per-zip variant can shadow this signature.
    """
    return {
        "auto_loan_apr": _build_apr_block(),
        "cpi_used_cars": _build_cpi_block(),
    }


def _build_apr_block() -> dict[str, Any] | None:
    """Compose the auto-loan APR pill payload, or None when no cache."""
    try:
        latest = macro_series_store.get_latest(_APR_SERIES_KEY)
    except Exception:
        logger.exception("get_latest failed for %s", _APR_SERIES_KEY)
        return None
    if not latest:
        return None

    value_pct = _coerce_float(latest.get("value"))
    as_of = latest.get("timestamp")
    if value_pct is None or not isinstance(as_of, str):
        return None

    try:
        history = macro_series_store.get_history(_APR_SERIES_KEY, days=_APR_HISTORY_DAYS)
    except Exception:
        logger.exception("get_history failed for %s", _APR_SERIES_KEY)
        history = []

    context = _compute_apr_context(value_pct, history)

    return {
        "value_pct": value_pct,
        "as_of": as_of,
        "label_short": "48-mo new car APR",
        "context": context,
    }


def _compute_apr_context(value_pct: float, history: list[dict[str, Any]]) -> str | None:
    """Editorial context label based on trailing 24mo high / 12mo low / 12mo high.

    Returns:
        "24-month high"   when value is within 5% of the trailing 24mo max,
        "near 12-mo low"  when value is within 5% of the trailing 12mo min,
        "near 12-mo high" when value is within 5% of the trailing 12mo max
                          (but not already flagged as 24mo high),
        None              otherwise (the boring middle).

    A sparse history (no observations in the trailing window) returns None
    rather than guessing — the pill just renders without the context suffix.
    """
    values_24mo = _values_within(history, days=_APR_HISTORY_DAYS)
    if values_24mo:
        max_24 = max(values_24mo)
        if max_24 > 0 and (max_24 - value_pct) / max_24 * 100.0 <= _NEAR_THRESHOLD_PCT:
            return "24-month high"

    values_12mo = _values_within(history, days=365)
    if values_12mo:
        min_12 = min(values_12mo)
        if min_12 > 0 and (value_pct - min_12) / min_12 * 100.0 <= _NEAR_THRESHOLD_PCT:
            return "near 12-mo low"
        max_12 = max(values_12mo)
        if max_12 > 0 and (max_12 - value_pct) / max_12 * 100.0 <= _NEAR_THRESHOLD_PCT:
            return "near 12-mo high"

    return None


def _build_cpi_block() -> dict[str, Any] | None:
    """Compose the CPI used-cars pill payload, or None when no cache."""
    try:
        latest = macro_series_store.get_latest(_CPI_SERIES_KEY)
    except Exception:
        logger.exception("get_latest failed for %s", _CPI_SERIES_KEY)
        return None
    if not latest:
        return None

    value = _coerce_float(latest.get("value"))
    as_of = latest.get("timestamp")
    if value is None or not isinstance(as_of, str):
        return None

    try:
        history = macro_series_store.get_history(_CPI_SERIES_KEY, days=_CPI_HISTORY_DAYS)
    except Exception:
        logger.exception("get_history failed for %s", _CPI_SERIES_KEY)
        history = []

    mom = _change_pct_at_offset(value, history, target_days=30, tolerance_days=15)
    yoy = _change_pct_at_offset(value, history, target_days=365, tolerance_days=45)

    return {
        "value": value,
        "as_of": as_of,
        "mom_change_pct": mom,
        "yoy_change_pct": yoy,
    }


# ---- helpers --------------------------------------------------------------


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _values_within(history: list[dict[str, Any]], *, days: int) -> list[float]:
    """Pluck float `value`s from history rows whose timestamp is within `days`."""
    now = datetime.now(UTC)
    out: list[float] = []
    for row in history:
        ts = _parse_timestamp(row.get("timestamp"))
        if ts is None:
            continue
        delta = (now - ts).total_seconds() / 86400.0
        if delta < 0 or delta > days:
            continue
        val = _coerce_float(row.get("value"))
        if val is not None:
            out.append(val)
    return out


def _change_pct_at_offset(
    current: float,
    history: list[dict[str, Any]],
    *,
    target_days: int,
    tolerance_days: int,
) -> float | None:
    """Find the history row closest to `target_days` ago and return %-change.

    Used for mom (≈30d) and yoy (≈365d) for CPI. A monthly series rarely
    lands exactly on the offset, so we accept anything within `tolerance_days`
    of the target and pick the closest. Returns None when nothing in range —
    the frontend just hides the delta rather than rendering a misleading 0.0%.
    """
    now = datetime.now(UTC)
    best: tuple[float, float] | None = None  # (abs distance in days, value)
    for row in history:
        ts = _parse_timestamp(row.get("timestamp"))
        if ts is None:
            continue
        delta_days = (now - ts).total_seconds() / 86400.0
        if delta_days <= 0:
            continue
        distance = abs(delta_days - target_days)
        if distance > tolerance_days:
            continue
        val = _coerce_float(row.get("value"))
        if val is None or val == 0.0:
            continue
        if best is None or distance < best[0]:
            best = (distance, val)
    if best is None:
        return None
    prior = best[1]
    return round((current - prior) / prior * 100.0, 1)
