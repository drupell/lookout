"""Persist scored deals, email drafts, and run records to DynamoDB."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.memory.deal_store import write_deals
from src.memory.trade_in_store import write_trade_in_estimate

if TYPE_CHECKING:
    from src.agent.state import AgentState

logger = logging.getLogger(__name__)


def persist_results(state: AgentState) -> dict[str, Any]:
    """Write deals, trade-in data, and the run audit record to DynamoDB.

    All scored deals are persisted (not just above-threshold) so the dashboard
    can filter by score interactively. Each row carries `overall_score` and
    `above_threshold` for filtering. Records expire via DynamoDB TTL after
    90 days to bound the dataset (MarketCheck ToU compliance).
    """
    from src.config.loader import load_preferences

    started_at = datetime.now(UTC).isoformat()

    user_id = state.get("user_id")
    prefs = load_preferences(user_id=user_id)
    threshold = prefs.scoring.threshold_notify

    run_id = state.get("run_id", "unknown")
    scored_deals = state.get("scored_deals", [])
    trade_in = state.get("trade_in_estimate")

    # Mark each scored deal with whether it cleared the threshold for filtering later
    for deal in scored_deals:
        deal["above_threshold"] = deal.get("overall_score", 0) >= threshold

    deals_written = write_deals(scored_deals, run_id, user_id=user_id)

    if trade_in:
        write_trade_in_estimate(trade_in, run_id)

    completed_at = datetime.now(UTC).isoformat()

    node_record = {
        "node": "persist_results",
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_calls": [{"tool": "dynamodb_write", "tables": ["deals", "trade_in"]}],
        "output_record_count": deals_written,
        "guardrail_result": "PENDING",
    }

    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)

    return {
        "nodes_executed": existing_nodes,
    }
