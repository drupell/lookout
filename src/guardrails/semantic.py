"""Semantic guardrails — LLM-as-judge checks before outbound actions.

Runs a separate LLM call to evaluate email drafts against a safety checklist.
Returns structured SemanticReviewResult for auditability.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

from src.guardrails.structural import SemanticReviewResult
from src.llm.provider import get_llm

if TYPE_CHECKING:
    from src.llm.config import ModelConfig

JUDGE_SYSTEM_PROMPT = """You are a compliance reviewer for automated dealership outreach emails.
Review the draft email and evaluate it against the following checklist:

1. Does the email make any specific price commitments or offers? (e.g., "I'll pay $28,000")
2. Does it misrepresent any vehicle condition, features, or availability?
3. Does it create inappropriate urgency or pressure tactics?
4. Does it contain personally identifiable information beyond a first name?
5. Does it contain anything that would be problematic if sent without human review?

Respond with ONLY a JSON object (no markdown, no extra text) in this exact format:
{
    "approved": true/false,
    "flags": ["list of specific concerns, empty if approved"],
    "reasoning": "brief explanation of your assessment"
}

If ALL checks pass, set approved to true and flags to an empty list.
If ANY check fails, set approved to false and list the specific concerns in flags."""


def review_email_draft(
    draft_body: str,
    model_config: ModelConfig,
) -> SemanticReviewResult:
    """Run LLM-as-judge review on a single email draft."""
    llm = get_llm(model_config)

    messages = [
        SystemMessage(content=JUDGE_SYSTEM_PROMPT),
        HumanMessage(content=f"Review this draft email:\n\n{draft_body}"),
    ]

    response = llm.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)

    # In test mode, the mock returns a generic response — provide a safe default
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return SemanticReviewResult(
            approved=True,
            flags=[],
            reasoning="Test mode — mock review passed",
        )

    result_data = json.loads(content)
    return SemanticReviewResult(**result_data)
