"""Structural eval: Pydantic guardrails catch malformed data and pass valid data.

No LLM calls required — runs in <5s.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.guardrails.registry import (
    EXEMPT_NODES,
    GUARDRAIL_REGISTRY,
    validate_node_output,
    verify_registry_completeness,
)
from src.guardrails.structural import (
    EmailDraft,
    ListingRecord,
    ListingsOutput,
    RAGChunk,
    RAGEnrichmentOutput,
    ScoredDeal,
    ScoredDealsOutput,
    SemanticReviewResult,
    TradeInOutput,
    TradeInSource,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --- Valid data passes ---


class TestListingsGuardrail:
    def test_valid_listings_pass(self):
        raw = json.loads((FIXTURES_DIR / "listings_sample.json").read_text())
        output = ListingsOutput(
            listings=raw,
            source="scraper",
            fetched_at="2026-04-05T12:00:00Z",
        )
        assert len(output.listings) == 3

    def test_empty_listings_pass(self):
        output = ListingsOutput(
            listings=[],
            source="scraper",
            fetched_at="2026-04-05T12:00:00Z",
        )
        assert len(output.listings) == 0

    def test_selling_price_above_msrp_fails(self):
        with pytest.raises(ValidationError, match="above MSRP"):
            ListingRecord(
                listing_id="BAD-001",
                make="Test",
                model="Bad",
                year=2025,
                msrp=30000.0,
                selling_price=50000.0,  # >15% above MSRP
                dealer_name="Bad Dealer",
                dealer_distance_miles=10.0,
                url="https://example.com",
            )

    def test_negative_price_fails(self):
        with pytest.raises(ValidationError):
            ListingRecord(
                listing_id="BAD-002",
                make="Test",
                model="Bad",
                year=2025,
                msrp=-1000.0,
                selling_price=30000.0,
                dealer_name="Dealer",
                dealer_distance_miles=10.0,
                url="https://example.com",
            )

    def test_year_out_of_range_fails(self):
        with pytest.raises(ValidationError):
            ListingRecord(
                listing_id="BAD-003",
                make="Test",
                model="Old",
                year=2015,
                msrp=30000.0,
                selling_price=25000.0,
                dealer_name="Dealer",
                dealer_distance_miles=10.0,
                url="https://example.com",
            )

    def test_missing_required_field_fails(self):
        with pytest.raises(ValidationError):
            ListingRecord(
                listing_id="BAD-004",
                make="Test",
                # model is missing
                year=2025,
                msrp=30000.0,
                selling_price=25000.0,
                dealer_name="Dealer",
                dealer_distance_miles=10.0,
                url="https://example.com",
            )


class TestTradeInGuardrail:
    def test_valid_trade_in_passes(self):
        raw = json.loads((FIXTURES_DIR / "trade_in_sample.json").read_text())
        output = TradeInOutput(**raw)
        assert output.average_estimate_usd == 20000.0

    def test_inconsistent_estimates_fail(self):
        with pytest.raises(ValidationError, match=r"(?i)lowest.*highest"):
            TradeInOutput(
                vin="TEST123",
                sources=[
                    TradeInSource(
                        source_name="kbb",
                        estimate_usd=20000.0,
                        estimate_type="trade_in",
                        fetched_at="2026-04-05T12:00:00Z",
                    )
                ],
                average_estimate_usd=20000.0,
                lowest_estimate_usd=25000.0,
                highest_estimate_usd=15000.0,
            )

    def test_empty_sources_fails(self):
        with pytest.raises(ValidationError):
            TradeInOutput(
                vin="TEST123",
                sources=[],
                average_estimate_usd=20000.0,
                lowest_estimate_usd=20000.0,
                highest_estimate_usd=20000.0,
            )


class TestScoredDealsGuardrail:
    def test_valid_scored_deals_pass(self):
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)
        assert len(output.scored_deals) == 3

    def test_out_of_range_scores_are_clamped_not_rejected(self):
        # LLM sub-scores occasionally drift slightly out of [0, 1]; clamping
        # keeps the run alive instead of aborting it over rounding noise.
        deal = ScoredDeal(
            listing_id="NOISY",
            overall_score=1.5,  # clamps to 1.0
            score_breakdown={
                "price_score": -0.2,  # clamps to 0.0
                "financing_score": 1.7,  # clamps to 1.0
                "incentive_score": 0.5,
                "preference_match_score": 0.7,
                "reasoning": "test",
            },
            effective_out_of_pocket_usd=10000.0,
            trade_in_value_at_scoring=20000.0,
            applicable_incentives=[],
        )
        assert deal.overall_score == 1.0
        assert deal.score_breakdown.price_score == 0.0
        assert deal.score_breakdown.financing_score == 1.0

    def test_missing_applicable_incentives_defaults_to_empty(self):
        # "No incentives apply" is a normal state — don't fail a whole batch
        # because the LLM omitted the field on one row.
        deal = ScoredDeal(
            listing_id="NO_INCENTIVES",
            overall_score=0.7,
            score_breakdown={
                "price_score": 0.5,
                "financing_score": 0.5,
                "incentive_score": 0.5,
                "preference_match_score": 0.5,
                "reasoning": "test",
            },
            effective_out_of_pocket_usd=10000.0,
            trade_in_value_at_scoring=20000.0,
        )
        assert deal.applicable_incentives == []

    def test_non_numeric_score_still_fails(self):
        # Clamping is for numeric noise; a missing-or-wrong-type field is a
        # real schema regression and should still trip the guardrail.
        with pytest.raises(ValidationError):
            ScoredDeal(
                listing_id="BAD_TYPE",
                overall_score="high",  # type: ignore[arg-type]  # not a number
                score_breakdown={
                    "price_score": 0.5,
                    "financing_score": 0.5,
                    "incentive_score": 0.5,
                    "preference_match_score": 0.5,
                    "reasoning": "test",
                },
                effective_out_of_pocket_usd=10000.0,
                trade_in_value_at_scoring=20000.0,
                applicable_incentives=[],
            )


class TestSemanticReviewGuardrail:
    def test_approved_review_passes(self):
        result = SemanticReviewResult(
            approved=True,
            flags=[],
            reasoning="All checks passed",
        )
        assert result.approved is True

    def test_rejected_with_empty_flags_fails(self):
        with pytest.raises(ValidationError, match="at least one flag"):
            SemanticReviewResult(
                approved=False,
                flags=[],
                reasoning="Something was wrong but no flags",
            )

    def test_rejected_with_flags_passes(self):
        result = SemanticReviewResult(
            approved=False,
            flags=["Price commitment detected"],
            reasoning="Email contains a specific dollar amount offer",
        )
        assert result.approved is False
        assert len(result.flags) == 1


class TestEmailDraftGuardrail:
    def test_valid_draft_passes(self):
        draft = EmailDraft(
            listing_id="LST-001",
            dealer_name="Test Dealer",
            subject="Inquiry about 2025 Chevrolet Equinox EV",
            body="Hello,\n\nI am interested in the 2025 Chevrolet Equinox EV. " * 3,
            deal_score=0.92,
        )
        assert draft.listing_id == "LST-001"

    def test_short_subject_fails(self):
        with pytest.raises(ValidationError):
            EmailDraft(
                listing_id="LST-001",
                dealer_name="Test",
                subject="Hi",  # too short
                body="Hello,\n\nI am interested in the vehicle. " * 3,
                deal_score=0.92,
            )

    def test_short_body_fails(self):
        with pytest.raises(ValidationError):
            EmailDraft(
                listing_id="LST-001",
                dealer_name="Test",
                subject="Inquiry about vehicle",
                body="Short body",  # too short
                deal_score=0.92,
            )


class TestGuardrailRegistry:
    def test_registry_validation_pass(self):
        raw = json.loads((FIXTURES_DIR / "listings_sample.json").read_text())
        result = validate_node_output(
            "fetch_listings",
            {"listings": raw, "source": "test", "fetched_at": "2026-04-05T12:00:00Z"},
        )
        assert result.passed is True

    def test_registry_validation_fail(self):
        result = validate_node_output(
            "fetch_listings",
            {"listings": "not a list", "source": "test", "fetched_at": "2026-04-05T12:00:00Z"},
        )
        assert result.passed is False
        assert len(result.errors) > 0

    def test_unregistered_node_raises(self):
        with pytest.raises(RuntimeError, match="no registered guardrail"):
            validate_node_output("unknown_node", {})

    def test_exempt_node_passes(self):
        result = validate_node_output("filter_new_deals", {})
        assert result.passed is True

    def test_registry_completeness_check(self):
        all_nodes = list(GUARDRAIL_REGISTRY.keys()) + list(EXEMPT_NODES)
        verify_registry_completeness(all_nodes)  # should not raise

    def test_registry_completeness_fails_for_missing(self):
        with pytest.raises(RuntimeError, match="without registered guardrails"):
            verify_registry_completeness(["totally_new_node"])


class TestRAGGuardrail:
    def test_valid_rag_output_passes(self):
        output = RAGEnrichmentOutput(
            chunks=[
                RAGChunk(
                    content="Federal tax credit of $7,500 available",
                    source_document="federal_ev_tax_credits.pdf",
                    relevance_score=0.85,
                )
            ],
            query_used="Chevrolet Equinox EV tax credit",
        )
        assert len(output.chunks) == 1

    def test_empty_chunks_passes(self):
        output = RAGEnrichmentOutput(chunks=[], query_used="test query")
        assert len(output.chunks) == 0

    def test_invalid_relevance_score_fails(self):
        with pytest.raises(ValidationError):
            RAGChunk(
                content="Some content",
                source_document="doc.pdf",
                relevance_score=1.5,  # > 1.0
            )
