"""Structural eval: federal Section 30D direct-purchase eligibility logic.

No LLM calls, no DynamoDB — pure deterministic mapping from income_tier +
filing status to {qualifies, reason, alt_path_via_lease}. Covers all five
IncomeTier values and the lease (Section 45W) alt-path flag.
"""

from __future__ import annotations

import pytest

from src.agent.federal_credit import qualifies_for_30d
from src.config.loader import IncomeTier


class TestFederalCreditQualification:
    def test_under_75k_qualifies_outright(self) -> None:
        result = qualifies_for_30d(IncomeTier.UNDER_75K)
        assert result["qualifies"] is True
        assert result["alt_path_via_lease"] is False
        assert "$75k" in result["reason"] or "75k" in result["reason"]

    def test_under_150k_qualifies_for_all_statuses(self) -> None:
        for status in ("single", "hoh", "mfj"):
            result = qualifies_for_30d(IncomeTier.UNDER_150K, filing_status=status)
            assert result["qualifies"] is True, f"under_150k should qualify for {status}"
            assert result["alt_path_via_lease"] is False

    def test_under_300k_qualifies_when_mfj(self) -> None:
        result = qualifies_for_30d(IncomeTier.UNDER_300K, filing_status="mfj")
        assert result["qualifies"] is True
        assert result["alt_path_via_lease"] is False

    def test_under_300k_does_not_qualify_when_single(self) -> None:
        result = qualifies_for_30d(IncomeTier.UNDER_300K, filing_status="single")
        assert result["qualifies"] is False
        # Lease path remains available for everyone in this bracket
        assert result["alt_path_via_lease"] is True
        assert "45W" in result["reason"] or "lease" in result["reason"].lower()

    def test_under_300k_conservative_for_hoh(self) -> None:
        """HOH at $150k-$300k may exceed the $225k cap — conservative deny."""
        result = qualifies_for_30d(IncomeTier.UNDER_300K, filing_status="hoh")
        assert result["qualifies"] is False
        assert result["alt_path_via_lease"] is True

    def test_above_300k_does_not_qualify_for_direct(self) -> None:
        result = qualifies_for_30d(IncomeTier.ABOVE_300K, filing_status="mfj")
        assert result["qualifies"] is False
        # But Section 45W lease pass-through has no income limit
        assert result["alt_path_via_lease"] is True
        assert "lease" in result["reason"].lower() or "45W" in result["reason"]

    def test_prefer_not_to_say_treated_as_worst_case(self) -> None:
        result = qualifies_for_30d(IncomeTier.PREFER_NOT_TO_SAY)
        assert result["qualifies"] is False
        assert result["alt_path_via_lease"] is True

    def test_default_filing_status_is_single(self) -> None:
        """Omitting filing_status should not change the verdict for unambiguous tiers."""
        explicit = qualifies_for_30d(IncomeTier.UNDER_75K, filing_status="single")
        defaulted = qualifies_for_30d(IncomeTier.UNDER_75K)
        assert explicit == defaulted

    def test_returns_dict_with_required_keys(self) -> None:
        """Every code path must return the three documented keys."""
        for tier in IncomeTier:
            result = qualifies_for_30d(tier)
            assert set(result.keys()) >= {"qualifies", "reason", "alt_path_via_lease"}
            assert isinstance(result["qualifies"], bool)
            assert isinstance(result["alt_path_via_lease"], bool)
            assert isinstance(result["reason"], str)
            assert result["reason"]  # non-empty

    @pytest.mark.parametrize(
        ("tier", "expected_alt_lease"),
        [
            (IncomeTier.UNDER_75K, False),
            (IncomeTier.UNDER_150K, False),
            (IncomeTier.ABOVE_300K, True),
            (IncomeTier.PREFER_NOT_TO_SAY, True),
        ],
    )
    def test_alt_path_via_lease_per_tier(self, tier: IncomeTier, expected_alt_lease: bool) -> None:
        """alt_path_via_lease is False only when direct purchase already qualifies."""
        result = qualifies_for_30d(tier)
        assert result["alt_path_via_lease"] is expected_alt_lease
