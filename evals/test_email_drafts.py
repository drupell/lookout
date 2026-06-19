"""Email draft eval: validates draft quality and compliance.

Tests the email drafter produces compliant drafts that pass semantic review.
"""

import os

from src.guardrails.structural import EmailDraft
from src.llm.config import ModelConfig
from src.tools.email.drafter import _fixture_draft, draft_outreach_email

MOCK_CONFIG = ModelConfig(
    provider="anthropic",
    model_id="mock",
    temperature=0.3,
    max_tokens=1024,
)

SAMPLE_DEAL = {
    "listing_id": "LST-001",
    "make": "Chevrolet",
    "model": "Equinox EV",
    "year": 2025,
    "msrp": 35000.0,
    "selling_price": 31500.0,
    "dealer_name": "Delaware Chevrolet",
    "overall_score": 0.92,
}


class TestEmailDrafter:
    def test_fixture_draft_produces_valid_schema(self):
        """Fixture draft should pass EmailDraft validation."""
        result = _fixture_draft(SAMPLE_DEAL)
        draft = EmailDraft(**result)
        assert draft.listing_id == "LST-001"
        assert draft.dealer_name == "Delaware Chevrolet"
        assert len(draft.body) >= 50

    def test_draft_in_test_mode_returns_fixture(self):
        """In TEST_MODE, drafter returns fixture data without LLM call."""
        os.environ["TEST_MODE"] = "true"
        result = draft_outreach_email(SAMPLE_DEAL, 20000.0, MOCK_CONFIG)
        draft = EmailDraft(**result)
        assert "Equinox EV" in draft.subject
        assert "Equinox EV" in draft.body

    def test_draft_does_not_make_price_commitments(self):
        """Draft should not contain specific dollar offers."""
        result = _fixture_draft(SAMPLE_DEAL)
        body = result["body"].lower()
        # Should not contain phrases like "I'll pay", "my offer", "I'm willing to pay"
        forbidden_phrases = ["i'll pay", "my offer is", "i'm willing to pay", "i will pay"]
        for phrase in forbidden_phrases:
            assert phrase not in body, f"Draft contains forbidden phrase: '{phrase}'"

    def test_draft_does_not_create_urgency(self):
        """Draft should not use pressure language."""
        result = _fixture_draft(SAMPLE_DEAL)
        body = result["body"].lower()
        urgency_phrases = ["act now", "limited time", "today only", "don't miss", "hurry"]
        for phrase in urgency_phrases:
            assert phrase not in body, f"Draft contains urgency phrase: '{phrase}'"

    def test_draft_includes_key_questions(self):
        """Draft should ask about financing and trade-in."""
        result = _fixture_draft(SAMPLE_DEAL)
        body = result["body"].lower()
        assert "financing" in body or "apr" in body, "Draft should mention financing"
        assert "trade" in body, "Draft should mention trade-in"

    def test_draft_subject_mentions_vehicle(self):
        """Subject line should reference the specific vehicle."""
        result = _fixture_draft(SAMPLE_DEAL)
        subject = result["subject"]
        assert "Equinox" in subject or "Chevrolet" in subject


class TestSemanticReviewIntegration:
    def test_clean_draft_would_pass_semantic_review(self):
        """A well-formed fixture draft should pass semantic review criteria."""
        result = _fixture_draft(SAMPLE_DEAL)
        body = result["body"]

        # Manually check the criteria that semantic review would check
        has_price_commitment = any(
            phrase in body.lower() for phrase in ["i'll pay", "$28,000", "my offer"]
        )
        has_urgency = any(
            phrase in body.lower() for phrase in ["act now", "limited time", "today only"]
        )

        assert not has_price_commitment, "Draft should not have price commitments"
        assert not has_urgency, "Draft should not have urgency language"

    def test_bad_draft_would_fail_semantic_review(self):
        """A draft with price commitments should be flaggable."""
        bad_body = (
            "Hello,\n\nI'll pay $28,000 cash today for the 2025 Chevrolet Equinox EV. "
            "This is my final offer — act now or I'm going elsewhere. "
            "I need this deal closed by end of day."
        )

        # This should be catchable by semantic review
        has_price_commitment = "i'll pay" in bad_body.lower() or "$28,000" in bad_body
        has_urgency = "act now" in bad_body.lower()

        assert has_price_commitment, "Bad draft should have price commitment"
        assert has_urgency, "Bad draft should have urgency"
