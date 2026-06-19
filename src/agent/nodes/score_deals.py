"""Score each listing against user preferences, trade-in value, and incentives."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.config.loader import load_preferences
from src.llm.config import scoring_model_config
from src.llm.provider import get_llm

if TYPE_CHECKING:
    from src.agent.state import AgentState

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """\
You are an expert automotive deal analyst. \
Score each EV listing based on the buyer's preferences and financial context.

For each listing, produce a score from 0.0 to 1.0 across four dimensions:
- price_score: How good is the discount off MSRP? 0% APR or exceptional financing boosts this.
- financing_score: Quality of financing terms (0% APR = 1.0, >5% = low score)
- incentive_score: How many applicable incentives reduce effective cost?
- preference_match_score: How well does it match buyer preferences (body style, distance)?

Also calculate effective_out_of_pocket_usd: selling_price - trade_in_value - applicable_incentives

Respond with ONLY a JSON array (no markdown, no extra text). Each element:
{
    "listing_id": "...",
    "overall_score": 0.0-1.0,
    "score_breakdown": {
        "price_score": 0.0-1.0,
        "financing_score": 0.0-1.0,
        "incentive_score": 0.0-1.0,
        "preference_match_score": 0.0-1.0,
        "reasoning": "brief explanation"
    },
    "effective_out_of_pocket_usd": number,
    "trade_in_value_at_scoring": number,
    "applicable_incentives": ["list of applicable incentives with amounts"]
}"""


def score_deals(state: AgentState) -> dict[str, Any]:
    """Score validated listings against preferences and trade-in value."""
    started_at = datetime.now(UTC).isoformat()

    if os.environ.get("TEST_MODE", "").lower() == "true":
        fixture_path = (
            Path(__file__).parent.parent.parent.parent
            / "evals"
            / "fixtures"
            / "scored_deals_sample.json"
        )
        if fixture_path.exists():
            scored_data = json.loads(fixture_path.read_text())
        else:
            scored_data = {"scored_deals": [], "model_used": "mock", "scored_at": started_at}

        return _build_result(state, scored_data, started_at)

    # Real scoring with LLM
    listings = state.get("validated_listings", [])
    trade_in = state.get("trade_in_estimate", {})
    rag_context = state.get("rag_context", [])
    trade_in_value = trade_in.get("average_estimate_usd", 0)

    if not listings:
        scored_data = {"scored_deals": [], "model_used": "none", "scored_at": started_at}
        return _build_result(state, scored_data, started_at)

    prefs = load_preferences(user_id=state.get("user_id"))

    context_text = "\n".join(
        f"[{c.get('source_document', 'unknown')}]: {c.get('content', '')}" for c in rag_context
    )
    preference_block = (
        f"Score these EV listings for a buyer with these preferences:\n"
        f"- Max monthly payment: ${prefs.deal_criteria.max_effective_monthly_usd}\n"
        f"- Min discount off MSRP: {prefs.deal_criteria.min_discount_off_msrp_pct}%\n"
        f"- Max acceptable APR: {prefs.deal_criteria.acceptable_apr_max}%\n"
        f"- Prefers 0% financing: {prefs.deal_criteria.zero_percent_financing_preferred}\n"
        f"- Prefers lease-to-own: {prefs.deal_criteria.lease_to_own_preferred}\n"
        f"- Trade-in value: ${trade_in_value:,.0f}\n\n"
        f"Incentive context:\n{context_text}\n\n"
    )

    scoring_config = scoring_model_config()
    llm = get_llm(scoring_config)

    # Batch listings to fit within the model's max_tokens output budget.
    # Each scored deal is ~150 output tokens; cap at 50 listings per batch
    # to comfortably fit Nova Lite's 10K output limit.
    batch_size = 50
    batches = [listings[i : i + batch_size] for i in range(0, len(listings), batch_size)]
    logger.info(
        "Scoring %d listings in %d batch(es) of up to %d via %s",
        len(listings),
        len(batches),
        batch_size,
        scoring_config.model_id,
    )

    scored_deals: list[dict[str, Any]] = []
    for batch_idx, batch in enumerate(batches):
        prompt = preference_block + f"Listings:\n{json.dumps(batch, indent=2)}"
        messages = [
            SystemMessage(content=SCORING_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        response = llm.invoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)

        try:
            batch_scored = json.loads(content)
            if isinstance(batch_scored, dict) and "scored_deals" in batch_scored:
                batch_scored = batch_scored["scored_deals"]
        except json.JSONDecodeError as e:
            logger.error(
                "Batch %d/%d failed to parse (len=%d, error=%s at pos %d): head=%r ... tail=%r",
                batch_idx + 1,
                len(batches),
                len(content),
                e.msg,
                e.pos,
                content[:200],
                content[-200:],
            )
            batch_scored = []

        scored_deals.extend(batch_scored)
        logger.info(
            "Batch %d/%d: scored %d listings",
            batch_idx + 1,
            len(batches),
            len(batch_scored),
        )

    # Merge original listing metadata into each scored deal. The LLM only
    # returns scoring fields keyed by listing_id; the actual car data
    # (make/model/year/dealer/url/etc.) lives on the source listing.
    listing_lookup = {lst["listing_id"]: lst for lst in listings if "listing_id" in lst}
    enriched: list[dict[str, Any]] = []
    for deal in scored_deals:
        listing_id = deal.get("listing_id")
        source = listing_lookup.get(listing_id, {})
        # LLM scoring fields take precedence; original listing fills the rest.
        merged = {**source, **deal}
        if "trade_in_value_at_scoring" not in merged:
            merged["trade_in_value_at_scoring"] = trade_in_value
        enriched.append(merged)

    if not enriched and scored_deals:
        logger.warning(
            "Scored %d deals but failed to enrich any — listing_id mismatch? Sample LLM "
            "ids=%s vs source ids=%s",
            len(scored_deals),
            [d.get("listing_id") for d in scored_deals[:3]],
            list(listing_lookup.keys())[:3],
        )
    scored_deals = enriched

    # Distribution log — useful for calibrating deal_score_threshold
    if scored_deals:
        scores = sorted(d.get("overall_score", 0) for d in scored_deals)
        n = len(scores)
        logger.info(
            "Score distribution (n=%d): min=%.2f p25=%.2f median=%.2f p75=%.2f max=%.2f",
            n,
            scores[0],
            scores[n // 4],
            scores[n // 2],
            scores[(3 * n) // 4],
            scores[-1],
        )

    scored_data = {
        "scored_deals": scored_deals,
        "model_used": scoring_config.model_id,
        "scored_at": started_at,
    }

    return _build_result(state, scored_data, started_at)


def _build_result(
    state: AgentState,
    scored_data: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    """Build the node return dict with audit trail."""
    completed_at = datetime.now(UTC).isoformat()

    node_record = {
        "node": "score_deals",
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_calls": [{"tool": "llm_scoring", "model": scored_data.get("model_used", "")}],
        "output_record_count": len(scored_data.get("scored_deals", [])),
        "guardrail_result": "PENDING",
    }

    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)

    prefs = load_preferences(user_id=state.get("user_id"))
    threshold = prefs.scoring.threshold_notify

    deals_above = [
        d for d in scored_data.get("scored_deals", []) if d.get("overall_score", 0) >= threshold
    ]

    return {
        "scored_deals": scored_data.get("scored_deals", []),
        "deals_above_threshold": deals_above,
        "nodes_executed": existing_nodes,
    }
