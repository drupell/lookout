"""Filter and deduplicate deals against historical records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.memory.deal_store import get_known_listing_ids

if TYPE_CHECKING:
    from src.agent.state import AgentState


def filter_new_deals(state: AgentState) -> dict[str, Any]:
    """Cross-reference deals against DynamoDB history. Deduplicate by listing_id."""
    started_at = datetime.now(UTC).isoformat()

    deals_above = state.get("deals_above_threshold", [])

    # Deduplicate by listing_id within this run
    seen_ids: set[str] = set()
    unique_deals: list[dict[str, Any]] = []
    for deal in deals_above:
        lid = deal.get("listing_id", "")
        if lid not in seen_ids:
            seen_ids.add(lid)
            unique_deals.append(deal)

    # Cross-reference with this user's DynamoDB deal history. Scoped per user
    # so another user's listings never suppress this user's notifications.
    known_ids = get_known_listing_ids(state.get("user_id"))
    new_deals = [d for d in unique_deals if d.get("listing_id", "") not in known_ids]

    completed_at = datetime.now(UTC).isoformat()

    node_record = {
        "node": "filter_new_deals",
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_calls": [{"tool": "dynamodb_query", "known_ids_count": len(known_ids)}],
        "output_record_count": len(new_deals),
        "guardrail_result": "EXEMPT",
    }

    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)

    return {
        "new_deals": new_deals,
        "nodes_executed": existing_nodes,
    }
