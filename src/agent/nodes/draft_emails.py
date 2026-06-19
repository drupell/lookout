"""Draft dealership outreach emails for qualifying deals.

ARCHITECTURAL BOUNDARY: This module produces EmailDraft objects only.
It must NEVER send, post, or transmit any communication.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.llm.config import drafting_model_config
from src.tools.email.drafter import draft_outreach_email

if TYPE_CHECKING:
    from src.agent.state import AgentState


def draft_emails(state: AgentState) -> dict[str, Any]:
    """Draft outreach emails for deals above the email threshold."""
    started_at = datetime.now(UTC).isoformat()

    new_deals = state.get("new_deals", [])
    trade_in = state.get("trade_in_estimate", {})
    trade_in_value = trade_in.get("average_estimate_usd", 0)

    drafting_config = drafting_model_config()

    drafts: list[dict[str, Any]] = []
    for deal in new_deals:
        draft = draft_outreach_email(deal, trade_in_value, drafting_config)
        drafts.append(draft)

    completed_at = datetime.now(UTC).isoformat()

    node_record = {
        "node": "draft_emails",
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_calls": [{"tool": "email_drafter", "count": len(drafts)}],
        "output_record_count": len(drafts),
        "guardrail_result": "PENDING",
    }

    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)

    return {
        "email_drafts": drafts,
        "nodes_executed": existing_nodes,
    }
