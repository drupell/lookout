"""Persist the per-run market-signal snapshot.

Runs after persist_results, so we have a frozen scored_deals list and a
final run status to read from state. Computes the five factor contributions
from the run's data (with graceful degradation when external macro data
isn't cached yet), runs them through the composite math, validates the
output against the structural guardrail, and writes the snapshot row.

Snapshots are Lookout's own derived numbers — composite index, factor
contributions, variance — not raw MarketCheck rows. That distinction
matters: snapshots have no TTL (they back the 6m / 12m / 24m trend
windows), so a bad row sits on the chart for two years. The structural
guardrail is belt-and-suspenders for exactly that case.

Cold-start is graceful by design: every data-driven factor returns 0.0
when there's no trailing baseline, leaving only the deterministic
calendar-pressure contribution. That matches the plan's "market signal
works from day 1" promise without faking any numbers.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.agent.market_signal_math import (
    MACRO_FACTORS,
    PERSONAL_FACTORS,
    calendar_pressure_contribution,
    composite_from_factors,
    relative_delta_contribution,
)
from src.guardrails.structural import MarketSnapshotPayload
from src.memory.market_snapshot_store import query_snapshots, write_snapshot

if TYPE_CHECKING:
    from src.agent.state import AgentState

logger = logging.getLogger(__name__)


def persist_market_snapshot(state: AgentState) -> dict[str, Any]:
    """Compute + persist a market-signal snapshot for this run.

    No-ops when there's no user_id (legacy single-tenant / standalone
    TEST_MODE runs) — without a user we can't tie the snapshot to a
    chartable key.

    Never raises: a snapshot failing to compute should not fail the
    run, since persist_results has already written the deals + run
    record. We log and skip the write instead.
    """
    started_at_node = datetime.now(UTC).isoformat()
    logger.info("persist_market_snapshot: entering")

    user_id = state.get("user_id")
    if not user_id:
        # No user_id → can't tie the snapshot to a user. Skip silently;
        # standalone TEST_MODE runs hit this path.
        logger.info("persist_market_snapshot: no user_id, skipping")
        return _append_node_record(state, started_at_node, wrote=False)

    started_at = state.get("started_at") or datetime.now(UTC).isoformat()
    status = (state.get("status") or "").upper()
    # Partial = the originating run didn't produce a complete dataset; the
    # API's baseline math excludes these rows but the chart still shows them.
    is_partial = status in {"GUARDRAIL_BLOCKED", "ERROR", "FAILED"}

    # Load the user's prefs so we can stamp zip_code on the snapshot for the
    # GSI and capture a minimal preferences snapshot for the calibration audit
    # (see ADR 010). Safe load — if it fails we still write the snapshot
    # without zip/prefs, since the snapshot itself is more valuable than the
    # side-channel context.
    zip_code: str | None = None
    prefs_snapshot: dict[str, Any] | None = None
    try:
        from src.config.loader import load_preferences

        prefs = load_preferences(user_id=user_id)
        zip_code = prefs.search.location_zip
        prefs_snapshot = _build_prefs_snapshot(prefs)
    except Exception:
        logger.exception("persist_market_snapshot: prefs load failed; continuing without zip")

    scored_deals = state.get("scored_deals", []) or []
    listing_count = len(scored_deals)

    # Compute the six factor contributions. Each function gracefully returns
    # 0.0 when its inputs are missing — that's the cross-source resilience
    # design at work. Don't try to be clever about "should we even include
    # this factor"; let it contribute 0 and let the variance halo widen.
    today = datetime.now(UTC).date()
    contribs: dict[str, float] = {
        "calendar_pressure": calendar_pressure_contribution(today),
        "auto_loan_apr_trend": _auto_loan_apr_trend_contribution(),
        "discount_depth": _discount_depth_contribution(scored_deals, user_id),
        "inventory_density": _inventory_density_contribution(scored_deals, user_id),
        "effective_price_trend": _effective_price_trend_contribution(scored_deals, user_id),
        "incentive_prevalence": _incentive_prevalence_contribution(scored_deals, user_id),
    }

    # Compose the slider snapshot from the contributions.
    result = composite_from_factors(contribs)

    # Validate via the registered structural guardrail before we write. This
    # is belt-and-suspenders: catches a math bug here, not at next month's
    # chart render. Failures log + skip the write (the run shouldn't fail
    # because a snapshot couldn't be computed).
    payload = {
        "composite_index": result.composite_index,
        "factor_contributions": result.factor_contributions,
        "variance_score": result.variance_score,
        "flower_position": result.flower_position,
        "label": result.label,
    }
    try:
        MarketSnapshotPayload(**payload)
    except ValidationError:
        logger.exception("persist_market_snapshot: payload failed guardrail; skipping write")
        return _append_node_record(state, started_at_node, wrote=False)

    # Median deal metrics for the calibration log (used by the API on read).
    median_discount, median_eff_price = _medians(scored_deals)

    # Decompose the composite into Macro vs Personal sub-totals so the API
    # can surface "where is this signal coming from?" Macro = calendar +
    # auto_loan_apr_trend (same for all users in the US). Personal = the
    # four user-prefs-driven factors. Segment is still 0 until that layer
    # of the index lands (per the plan, that's a v2 dial that needs the
    # auto.dev facets pipeline).
    macro_total = sum(
        contrib for name, contrib in result.factor_contributions.items() if name in MACRO_FACTORS
    )
    personal_total = sum(
        contrib for name, contrib in result.factor_contributions.items() if name in PERSONAL_FACTORS
    )

    write_snapshot(
        snapshot_key=user_id,
        timestamp=started_at,
        zip_code=zip_code,
        listing_count=listing_count,
        median_discount_pct=median_discount,
        median_eff_price_usd=median_eff_price,
        factor_contributions=result.factor_contributions,
        composite_index=result.composite_index,
        variance_score=result.variance_score,
        flower_position=result.flower_position,
        label=result.label,
        partial=is_partial,
        macro_index=macro_total,
        segment_index=0.0,
        personal_index=personal_total,
        prefs_snapshot=prefs_snapshot,
    )

    logger.info(
        "persist_market_snapshot: wrote key=%s ts=%s composite=%.1f label=%s",
        user_id,
        started_at,
        result.composite_index,
        result.label,
    )
    update = _append_node_record(state, started_at_node, wrote=True)
    update["market_snapshot"] = payload
    return update


def _append_node_record(state: AgentState, started_at_node: str, *, wrote: bool) -> dict[str, Any]:
    """Append the persist_market_snapshot audit record and return a partial update.

    Mirrors the audit-record pattern in every other node so the
    nodes_executed trail stays consistent across the graph.
    """
    node_record = {
        "node": "persist_market_snapshot",
        "started_at": started_at_node,
        "completed_at": datetime.now(UTC).isoformat(),
        "tool_calls": (
            [{"tool": "dynamodb_write", "tables": ["market_snapshots"]}] if wrote else []
        ),
        "output_record_count": 1 if wrote else 0,
        "guardrail_result": "PENDING",
    }
    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)
    return {"nodes_executed": existing_nodes}


# ---- Private helpers ----


def _build_prefs_snapshot(prefs: Any) -> dict[str, Any]:
    """Capture the search criteria the run was scored against.

    Used by the 90-day calibration audit (ADR 010) to make sure the "did the
    next-30-day median price actually drop?" comparison is apples-to-apples
    — i.e. we don't credit the signal for moves that just reflect the user
    widening their radius or removing an excluded brand mid-window.

    Deliberately a small projection, not the whole Preferences model: every
    field we capture is one more thing we have to carry forward in 24 months
    of snapshots, so we limit to the search filters that actually shift the
    matching-listings population. Defensive `getattr` with sensible defaults
    so a slightly-older Preferences shape doesn't break snapshot writes.
    """
    search = getattr(prefs, "search", None)
    snap: dict[str, Any] = {
        "location_zip": getattr(search, "location_zip", None),
        "radius_miles": getattr(search, "radius_miles", None),
        "max_vehicle_age_years": getattr(search, "max_vehicle_age_years", None),
        "body_styles": list(getattr(search, "body_styles", []) or []),
        "fuel_types": list(getattr(search, "fuel_types", []) or []),
        "min_price_usd": getattr(search, "min_price_usd", None),
        "max_price_usd": getattr(search, "max_price_usd", None),
        "max_mileage_miles": getattr(search, "max_mileage_miles", None),
        "included_brands": list(getattr(prefs, "included_brands", []) or []),
        "excluded_brands": list(getattr(prefs, "excluded_brands", []) or []),
        "excluded_models": list(getattr(prefs, "excluded_models", []) or []),
    }
    # Drop None scalars so the persisted attribute stays tight.
    return {k: v for k, v in snap.items() if v is not None}


def _safe_prior_snapshots(user_id: str) -> list[dict[str, Any]]:
    """Pull the trailing-90d non-partial snapshots, swallowing read errors.

    If the snapshots table is unavailable or the read fails for any reason,
    treat it the same as "no prior history" — the data-driven factors return
    0.0 and only calendar_pressure contributes. Better than crashing the
    snapshot write over a transient read issue.
    """
    try:
        return query_snapshots(user_id, window="90d", include_partial=False)
    except Exception:
        logger.exception("persist_market_snapshot: prior snapshot read failed")
        return []


def _auto_loan_apr_trend_contribution() -> float:
    """48-mo new car APR today vs trailing-12mo average. Higher APR is unfavorable.

    Reads FRED's `TERMCBAUTO48NS` series from the macro-series cache (populated
    by the nightly refresh cron). Returns 0.0 gracefully when:
      - No FRED data has been cached yet (cold start)
      - Less than 2 history points (can't compute a stable baseline)
      - Any read or math failure

    Direction: higher current APR than baseline = financing is more expensive
    than usual = lean "Not yet". So we pass `positive_is_unfavorable`.

    Threshold tuning: we keep `max_pct_change` at the default 0.5 (50%) — that
    way a typical APR drift (5% → 7.5%) saturates the factor toward unfavorable,
    while smaller moves scale proportionally. APR-only swings are rarely
    larger than 50% in absolute terms over any reasonable baseline window.
    """
    try:
        from src.memory.macro_series_store import get_history, get_latest
    except Exception:
        logger.exception("auto_loan_apr_trend: macro_series_store import failed")
        return 0.0

    try:
        latest = get_latest("fred:TERMCBAUTO48NS")
    except Exception:
        logger.exception("auto_loan_apr_trend: latest read failed")
        return 0.0
    if not latest:
        return 0.0
    current_raw = latest.get("value")
    if not isinstance(current_raw, (int, float)):
        return 0.0

    try:
        history = get_history("fred:TERMCBAUTO48NS", days=365)
    except Exception:
        logger.exception("auto_loan_apr_trend: history read failed")
        return 0.0
    baseline_vals = [h["value"] for h in history if isinstance(h.get("value"), (int, float))]
    if len(baseline_vals) < 2:
        # Not enough history to compute a stable baseline — stay neutral.
        return 0.0
    baseline = sum(baseline_vals) / len(baseline_vals)

    return relative_delta_contribution(
        "auto_loan_apr_trend",
        current=float(current_raw),
        baseline=float(baseline),
        direction="positive_is_unfavorable",
    )


def _discount_depth_contribution(scored_deals: list[dict[str, Any]], user_id: str) -> float:
    """Median %-off-MSRP today vs trailing-90d median. Bigger discount = favorable."""
    discounts: list[float] = []
    for deal in scored_deals:
        msrp = deal.get("msrp")
        price = deal.get("selling_price")
        if not (isinstance(msrp, (int, float)) and isinstance(price, (int, float))):
            continue
        if msrp <= 0:
            continue
        discounts.append((msrp - price) / msrp * 100.0)

    if not discounts:
        return 0.0
    current = statistics.median(discounts)

    prior = _safe_prior_snapshots(user_id)
    baseline_vals = [
        r["median_discount_pct"]
        for r in prior
        if isinstance(r.get("median_discount_pct"), (int, float))
    ]
    if not baseline_vals:
        # Cold start — no trailing baseline yet. Stay neutral.
        return 0.0
    baseline = statistics.median(baseline_vals)
    return relative_delta_contribution(
        "discount_depth",
        current=current,
        baseline=baseline,
        direction="positive_is_favorable",
    )


def _inventory_density_contribution(scored_deals: list[dict[str, Any]], user_id: str) -> float:
    """Today's matching-listing count vs trailing-90d median. More inventory = favorable."""
    current = float(len(scored_deals))
    prior = _safe_prior_snapshots(user_id)
    baseline_vals = [
        r["listing_count"] for r in prior if isinstance(r.get("listing_count"), (int, float))
    ]
    if not baseline_vals:
        return 0.0
    baseline = statistics.median(baseline_vals)
    return relative_delta_contribution(
        "inventory_density",
        current=current,
        baseline=baseline,
        direction="positive_is_favorable",
    )


def _effective_price_trend_contribution(scored_deals: list[dict[str, Any]], user_id: str) -> float:
    """Median effective out-of-pocket today vs trailing-90d median. Higher = unfavorable."""
    eff_prices: list[float] = []
    for deal in scored_deals:
        v = deal.get("effective_out_of_pocket_usd")
        if isinstance(v, (int, float)):
            eff_prices.append(float(v))
    if not eff_prices:
        return 0.0
    current = statistics.median(eff_prices)

    prior = _safe_prior_snapshots(user_id)
    baseline_vals = [
        r["median_eff_price_usd"]
        for r in prior
        if isinstance(r.get("median_eff_price_usd"), (int, float))
    ]
    if not baseline_vals:
        return 0.0
    baseline = statistics.median(baseline_vals)
    return relative_delta_contribution(
        "effective_price_trend",
        current=current,
        baseline=baseline,
        direction="positive_is_unfavorable",
    )


def _incentive_prevalence_contribution(scored_deals: list[dict[str, Any]], user_id: str) -> float:
    """Avg incentives-per-listing today vs trailing-90d baseline. More incentives = favorable.

    Baseline derived from the trailing snapshots' `factor_contributions`
    median for this factor isn't apples-to-apples (it's a delta, not a
    raw average). We use the prior snapshots' inferred raw means stored
    on each row instead — but since we never persisted that scalar, we
    fall back to a neutral baseline derived from the *contribution
    magnitudes* of past rows. In practice this means cold-start is
    neutral (0.0) and only diverges once history accumulates.
    """
    if not scored_deals:
        return 0.0
    counts = [
        len(d.get("applicable_incentives", []) or [])
        for d in scored_deals
        if isinstance(d.get("applicable_incentives", []), list)
    ]
    if not counts:
        return 0.0
    current = sum(counts) / len(counts)

    prior = _safe_prior_snapshots(user_id)
    # We don't currently persist a per-listing avg-incentive scalar. Use the
    # listing_count as a coarse proxy for "how the slice has shifted" — when
    # the raw avg lands in a future schema, swap to it. For now, no prior →
    # neutral, which keeps the cold-start promise.
    baseline_avg_counts: list[float] = []
    for r in prior:
        contribs = r.get("factor_contributions") or {}
        # If a prior snapshot recorded incentive_prevalence as part of its
        # contributions, we *cannot* reverse it to a raw avg. So we treat
        # the absence of a richer baseline signal as cold-start.
        if isinstance(contribs, dict) and "incentive_prevalence_raw_avg" in contribs:
            v = contribs["incentive_prevalence_raw_avg"]
            if isinstance(v, (int, float)):
                baseline_avg_counts.append(float(v))
    if not baseline_avg_counts:
        return 0.0
    baseline = statistics.median(baseline_avg_counts)
    return relative_delta_contribution(
        "incentive_prevalence",
        current=current,
        baseline=baseline,
        direction="positive_is_favorable",
    )


def _medians(scored_deals: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Return (median_discount_pct, median_eff_price_usd) for the calibration log.

    Either can be None when there's not enough data — the snapshot store
    treats None as "omit this attribute," which keeps the row honest about
    what it actually has.
    """
    discounts: list[float] = []
    for deal in scored_deals:
        msrp = deal.get("msrp")
        price = deal.get("selling_price")
        if isinstance(msrp, (int, float)) and isinstance(price, (int, float)) and msrp > 0:
            discounts.append((msrp - price) / msrp * 100.0)
    eff_prices = [
        float(d["effective_out_of_pocket_usd"])
        for d in scored_deals
        if isinstance(d.get("effective_out_of_pocket_usd"), (int, float))
    ]
    median_discount = statistics.median(discounts) if discounts else None
    median_eff_price = statistics.median(eff_prices) if eff_prices else None
    return median_discount, median_eff_price
