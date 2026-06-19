"""Structural eval: ZIP -> US state code resolver.

No LLM calls, no external services — runs in <5s.

Coverage spans every US region (Northeast, Mid-Atlantic, South, Midwest,
Mountain West, West Coast, AK, HI) plus malformed inputs.
"""

from __future__ import annotations

import pytest

from src.api._zip_to_state import zip_to_state


class TestZipToStateHappyPath:
    """Spot-check ~15 well-known ZIPs across all US regions."""

    @pytest.mark.parametrize(
        ("zip_code", "expected_state"),
        [
            # Northeast / New England
            ("02108", "MA"),  # Boston
            ("10001", "NY"),  # Manhattan
            ("19103", "PA"),  # Philadelphia
            ("06510", "CT"),  # New Haven
            # Mid-Atlantic
            ("19901", "DE"),  # Dover
            ("20001", "DC"),  # Washington DC
            ("21201", "MD"),  # Baltimore
            ("22202", "VA"),  # Arlington
            # South
            ("30301", "GA"),  # Atlanta
            ("33101", "FL"),  # Miami
            ("28201", "NC"),  # Charlotte
            # Midwest
            ("60601", "IL"),  # Chicago
            ("48201", "MI"),  # Detroit
            ("55101", "MN"),  # St Paul
            # Mountain West
            ("80202", "CO"),  # Denver
            ("85001", "AZ"),  # Phoenix
            # West Coast + non-contiguous
            ("94102", "CA"),  # San Francisco
            ("90210", "CA"),  # Beverly Hills
            ("98101", "WA"),  # Seattle
            ("99501", "AK"),  # Anchorage
            ("96813", "HI"),  # Honolulu
            # Texas
            ("75201", "TX"),  # Dallas
        ],
    )
    def test_known_zip_resolves(self, zip_code: str, expected_state: str) -> None:
        assert zip_to_state(zip_code) == expected_state


class TestZipToStateMalformed:
    """Malformed input returns None instead of raising."""

    @pytest.mark.parametrize(
        "bad_input",
        [
            "",  # empty
            "abc",  # not numeric
            "123",  # too short
            "123456",  # too long (not a ZIP+4 with dash)
            "  ",  # whitespace only
            "12a45",  # mixed alphanumeric
        ],
    )
    def test_malformed_returns_none(self, bad_input: str) -> None:
        assert zip_to_state(bad_input) is None

    def test_zip_plus_four_still_works(self) -> None:
        """ZIP+4 ('12345-6789') is stripped to the 5-digit prefix and resolved."""
        # 10001 -> NY
        assert zip_to_state("10001-6789") == "NY"
        # Whitespace + ZIP+4 also tolerated
        assert zip_to_state("  10001-6789  ") == "NY"

    def test_leading_zeros_preserved(self) -> None:
        """ZIPs starting with 0 (New England / Puerto Rico region) must not lose digits."""
        # 02108 -> MA (Boston) — leading zero must survive
        assert zip_to_state("02108") == "MA"
