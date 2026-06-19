"""Semantic guardrail eval: validates LLM-as-judge catches problematic drafts.

In TEST_MODE, the semantic reviewer returns a safe default.
This test validates the structural contract of SemanticReviewResult.
"""

import os

import pytest

from src.guardrails.semantic import review_email_draft
from src.guardrails.structural import SemanticReviewResult
from src.llm.config import ModelConfig

MOCK_CONFIG = ModelConfig(
    provider="anthropic",
    model_id="mock",
    temperature=0.1,
    max_tokens=1024,
)


class TestSemanticGuardrail:
    def test_test_mode_returns_approved(self):
        """In TEST_MODE, semantic review returns approved by default."""
        os.environ["TEST_MODE"] = "true"
        result = review_email_draft("Any draft text here", MOCK_CONFIG)
        assert isinstance(result, SemanticReviewResult)
        assert result.approved is True
        assert len(result.flags) == 0

    def test_review_result_schema_valid(self):
        """SemanticReviewResult must have all required fields."""
        result = SemanticReviewResult(
            approved=True,
            flags=[],
            reasoning="All checks passed",
        )
        assert result.approved is True
        assert result.reasoning

    def test_rejected_result_must_have_flags(self):
        """A rejected review must include at least one flag."""
        with pytest.raises(Exception):
            SemanticReviewResult(
                approved=False,
                flags=[],
                reasoning="Something wrong",
            )

    def test_rejected_result_with_flags_valid(self):
        """A properly flagged rejection should validate."""
        result = SemanticReviewResult(
            approved=False,
            flags=["Price commitment detected: 'I'll pay $28,000 cash'"],
            reasoning=(
                "Email contains a specific dollar amount offer"
                " which should not be sent without review."
            ),
        )
        assert result.approved is False
        assert len(result.flags) == 1
        assert "Price commitment" in result.flags[0]

    def test_multiple_flags_valid(self):
        """Multiple flags should be supported."""
        result = SemanticReviewResult(
            approved=False,
            flags=[
                "Price commitment: '$28,000 cash'",
                "Urgency language: 'act now'",
                "Pressure tactic: 'going elsewhere'",
            ],
            reasoning="Multiple compliance issues detected.",
        )
        assert len(result.flags) == 3
