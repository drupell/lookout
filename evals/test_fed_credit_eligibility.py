"""Tests for src/tools/external/fed_credit_eligibility.py."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """The eligibility loader uses lru_cache; clear between tests to make
    each test self-contained against in-process module state."""
    from src.tools.external.fed_credit_eligibility import _load

    _load.cache_clear()


def test_known_model_returns_credit() -> None:
    from src.tools.external.fed_credit_eligibility import is_eligible

    result = is_eligible("Tesla", "Model Y", 2025)
    assert result["credit_usd"] == 7500
    assert "Section 30D" in result["reason"]


def test_match_is_case_insensitive() -> None:
    from src.tools.external.fed_credit_eligibility import is_eligible

    # Lowercase make + uppercase model still matches.
    result = is_eligible("hyundai", "IONIQ 5", 2025)
    assert result["credit_usd"] == 7500


def test_unknown_model_returns_zero() -> None:
    from src.tools.external.fed_credit_eligibility import (
        NOT_IN_LIST_REASON,
        is_eligible,
    )

    result = is_eligible("Imaginary", "Phantom", 2099)
    assert result["credit_usd"] == 0
    assert result["reason"] == NOT_IN_LIST_REASON


def test_known_model_wrong_year_returns_zero() -> None:
    """Year is part of the lookup key — Tesla Model Y exists but 2019 is
    too old to be in the eligibility file."""
    from src.tools.external.fed_credit_eligibility import is_eligible

    result = is_eligible("Tesla", "Model Y", 2019)
    assert result["credit_usd"] == 0


def test_partial_credit_vehicle_returns_3750() -> None:
    """Some vehicles only qualify for half the credit (battery sourcing)."""
    from src.tools.external.fed_credit_eligibility import is_eligible

    result = is_eligible("Ford", "Mustang Mach-E", 2024)
    assert result["credit_usd"] == 3750


def test_empty_inputs_return_unknown() -> None:
    """Defensive: empty strings + zero year don't blow up."""
    from src.tools.external.fed_credit_eligibility import is_eligible

    result = is_eligible("", "", 0)
    assert result["credit_usd"] == 0
