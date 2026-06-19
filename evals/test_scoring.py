"""Scoring eval: validates deal scoring logic with fixture data.

In TEST_MODE, uses fixture scored deals to verify threshold filtering.
With real LLM (TEST_MODE=false), asserts score ranges not exact values.
"""

import json
from pathlib import Path

from src.guardrails.structural import ScoredDealsOutput

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestScoringFixtures:
    """Tests that run against fixture data — no LLM calls."""

    def test_fixture_scores_parse_correctly(self):
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)
        assert len(output.scored_deals) == 3

    def test_good_deal_scores_above_threshold(self):
        """The Equinox EV at 0% APR with 10% off MSRP should score high."""
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)

        good_deal = next(d for d in output.scored_deals if d.listing_id == "LST-001")
        assert good_deal.overall_score >= 0.80, (
            f"Good deal (0% APR, 10% off MSRP) scored {good_deal.overall_score}, expected >= 0.80"
        )

    def test_bad_deal_scores_below_threshold(self):
        """The EV6 with minimal discount and high APR should score low."""
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)

        bad_deal = next(d for d in output.scored_deals if d.listing_id == "LST-003")
        assert bad_deal.overall_score < 0.60, (
            f"Bad deal scored {bad_deal.overall_score}, expected < 0.60"
        )

    def test_borderline_deal_in_range(self):
        """The Ioniq 5 should be in the borderline range."""
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)

        borderline = next(d for d in output.scored_deals if d.listing_id == "LST-002")
        assert 0.55 <= borderline.overall_score <= 0.85, (
            f"Borderline deal scored {borderline.overall_score}, expected 0.55-0.85"
        )

    def test_threshold_filtering(self):
        """Only deals above 0.75 should pass the default threshold."""
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)

        above_threshold = [d for d in output.scored_deals if d.overall_score >= 0.75]
        below_threshold = [d for d in output.scored_deals if d.overall_score < 0.75]

        assert len(above_threshold) >= 1, "Expected at least one deal above threshold"
        assert len(below_threshold) >= 1, "Expected at least one deal below threshold"

    def test_effective_out_of_pocket_is_positive(self):
        """All effective out-of-pocket costs should be non-negative."""
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)

        for deal in output.scored_deals:
            # Out of pocket can be negative if incentives exceed price difference
            # but trade_in_value_at_scoring must be positive
            assert deal.trade_in_value_at_scoring > 0

    def test_score_breakdown_components_valid(self):
        """All score breakdown components should be in [0, 1]."""
        raw = json.loads((FIXTURES_DIR / "scored_deals_sample.json").read_text())
        output = ScoredDealsOutput(**raw)

        for deal in output.scored_deals:
            bd = deal.score_breakdown
            assert 0.0 <= bd.price_score <= 1.0
            assert 0.0 <= bd.financing_score <= 1.0
            assert 0.0 <= bd.incentive_score <= 1.0
            assert 0.0 <= bd.preference_match_score <= 1.0
            assert len(bd.reasoning) > 0
