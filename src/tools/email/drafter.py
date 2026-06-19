"""Email draft generator — produces EmailDraft objects for human review.

ARCHITECTURAL BOUNDARY: This module MUST NOT contain any send, post, smtp,
or requests calls. It produces drafts only. This is a deliberate HITL trust
boundary, not a convenience.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.provider import get_llm

if TYPE_CHECKING:
    from src.llm.config import ModelConfig

logger = logging.getLogger(__name__)

DRAFTER_SYSTEM_PROMPT = """\
You are a professional assistant helping a car buyer draft initial \
outreach emails to dealerships. Your tone should be:
- Professional but friendly
- Expressing genuine interest without desperation
- Asking clear questions about financing, trade-in, and incentives
- Never making price commitments or specific dollar offers
- Never claiming urgency or using pressure language
- Brief and respectful of the dealer's time

Respond with ONLY a JSON object (no markdown, no extra text):
{
    "subject": "email subject line",
    "body": "full email body text"
}"""


def draft_outreach_email(
    deal: dict[str, Any],
    trade_in_value: float,
    model_config: ModelConfig,
) -> dict[str, Any]:
    """Generate a draft outreach email for a qualifying deal.

    Returns a dict matching EmailDraft schema.
    Never sends anything — draft production only.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return _fixture_draft(deal)

    llm = get_llm(model_config)

    prompt = (
        f"Draft an email to {deal.get('dealer_name', 'the dealer')} about a "
        f"{deal.get('year', '')} {deal.get('make', '')} {deal.get('model', '')}.\n\n"
        f"Key details:\n"
        f"- Listed at ${deal.get('selling_price', 0):,.0f} (MSRP ${deal.get('msrp', 0):,.0f})\n"
        f"- I have a trade-in valued at approximately ${trade_in_value:,.0f}\n"
        f"- I'm interested in 0% APR or lowest available financing rate\n"
        f"- Ask about any current manufacturer incentives or rebates\n"
        f"- Ask about their trade-in evaluation process\n\n"
        f"Remember: Do NOT make any specific price offers or commitments."
    )

    messages = [
        SystemMessage(content=DRAFTER_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)

    try:
        result = json.loads(content)
        subject = result.get(
            "subject",
            f"Inquiry about {deal.get('year', '')} {deal.get('make', '')} {deal.get('model', '')}",
        )
        body = result.get("body", content)
    except (json.JSONDecodeError, AttributeError):
        subject = (
            f"Inquiry about {deal.get('year', '')} {deal.get('make', '')} {deal.get('model', '')}"
        )
        body = content

    return {
        "listing_id": deal.get("listing_id", ""),
        "dealer_name": deal.get("dealer_name", "Unknown"),
        "subject": subject,
        "body": body,
        "deal_score": deal.get("overall_score", 0.0),
    }


def _fixture_draft(deal: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic draft for test mode."""
    year = deal.get("year", "")
    make = deal.get("make", "")
    model = deal.get("model", "")
    dealer = deal.get("dealer_name", "the dealership")

    return {
        "listing_id": deal.get("listing_id", ""),
        "dealer_name": dealer,
        "subject": f"Inquiry about {year} {make} {model}",
        "body": (
            f"Hello,\n\n"
            f"I am writing to express my interest in the {year} {make} {model} "
            f"currently listed on your website. I have a vehicle to trade in and "
            f"would appreciate the opportunity to discuss available financing options "
            f"and any current manufacturer incentives.\n\n"
            f"Could you please provide information on:\n"
            f"- Current financing rates and any 0% APR promotions\n"
            f"- Your trade-in evaluation process\n"
            f"- Any additional manufacturer rebates or incentives\n\n"
            f"I am flexible on timing and happy to schedule at your convenience.\n\n"
            f"Thank you for your time.\n"
        ),
        "deal_score": deal.get("overall_score", 0.0),
    }
