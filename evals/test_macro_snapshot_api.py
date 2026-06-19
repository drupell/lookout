"""Tests for `GET /me/macro` — the macro-series snapshot API handler.

We don't need moto for these — the handler reads from
`macro_series_store.{get_latest,get_history}` and we patch those directly
to keep the suite fast (the store has its own moto-backed tests in
`test_macro_series_store.py`). This keeps each scenario one-table-write
removed and makes the boundary-case scenarios (near 24mo max, near 12mo min)
read clearly without seeding a believable history into DynamoDB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest


def _ts_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _row(value: float, days_ago: int) -> dict[str, Any]:
    """One macro-series row matching `get_history` output shape."""
    return {"value": value, "timestamp": _ts_days_ago(days_ago)}


# -------------------- empty-cache cold start --------------------


class TestEmptyCache:
    def test_both_fields_null_when_no_cache(self) -> None:
        from src.api.macro_snapshot import get_macro_snapshot

        with (
            patch("src.api.macro_snapshot.macro_series_store.get_latest", return_value=None),
            patch("src.api.macro_snapshot.macro_series_store.get_history", return_value=[]),
        ):
            result = get_macro_snapshot("user-1")
        assert result == {"auto_loan_apr": None, "cpi_used_cars": None}

    def test_handler_never_raises_on_store_error(self) -> None:
        """A boto3 exception during `get_latest` must degrade to null, not 500."""
        from src.api.macro_snapshot import get_macro_snapshot

        def boom(_key: str) -> dict[str, Any]:
            raise RuntimeError("dynamo down")

        with (
            patch("src.api.macro_snapshot.macro_series_store.get_latest", side_effect=boom),
            patch("src.api.macro_snapshot.macro_series_store.get_history", return_value=[]),
        ):
            result = get_macro_snapshot("user-1")
        assert result == {"auto_loan_apr": None, "cpi_used_cars": None}


# -------------------- APR --------------------


class TestAutoLoanApr:
    def test_returns_value_and_as_of_when_cached(self) -> None:
        from src.api.macro_snapshot import get_macro_snapshot

        as_of = "2026-05-01T00:00:00+00:00"

        def fake_get_latest(key: str) -> dict[str, Any] | None:
            if key == "fred:TERMCBAUTO48NS":
                return {"value": 7.36, "timestamp": as_of}
            return None

        with (
            patch(
                "src.api.macro_snapshot.macro_series_store.get_latest",
                side_effect=fake_get_latest,
            ),
            patch("src.api.macro_snapshot.macro_series_store.get_history", return_value=[]),
        ):
            result = get_macro_snapshot("user-1")

        apr = result["auto_loan_apr"]
        assert apr is not None
        assert apr["value_pct"] == 7.36
        assert apr["as_of"] == as_of
        assert apr["label_short"] == "48-mo new car APR"

    def test_context_24_month_high(self) -> None:
        """Current within 5% of the trailing 24mo max → '24-month high'."""
        from src.api.macro_snapshot import get_macro_snapshot

        current = 7.36

        def fake_get_latest(key: str) -> dict[str, Any] | None:
            if key == "fred:TERMCBAUTO48NS":
                return {"value": current, "timestamp": _ts_days_ago(15)}
            return None

        # Historical max ~7.40 within the 24mo window, current 7.36 → 0.54% below max.
        history = [
            _row(current, 15),
            _row(7.40, 90),  # the 24mo peak
            _row(6.20, 500),
            _row(5.80, 700),
        ]

        def fake_get_history(key: str, *, days: int) -> list[dict[str, Any]]:
            if key == "fred:TERMCBAUTO48NS":
                return history
            return []

        with (
            patch(
                "src.api.macro_snapshot.macro_series_store.get_latest",
                side_effect=fake_get_latest,
            ),
            patch(
                "src.api.macro_snapshot.macro_series_store.get_history",
                side_effect=fake_get_history,
            ),
        ):
            result = get_macro_snapshot("user-1")

        apr = result["auto_loan_apr"]
        assert apr is not None
        assert apr["context"] == "24-month high"

    def test_context_near_12mo_low(self) -> None:
        """Current within 5% of the trailing 12mo min → 'near 12-mo low'."""
        from src.api.macro_snapshot import get_macro_snapshot

        current = 5.10

        def fake_get_latest(key: str) -> dict[str, Any] | None:
            if key == "fred:TERMCBAUTO48NS":
                return {"value": current, "timestamp": _ts_days_ago(10)}
            return None

        # 12mo min = 5.00 (within 365d); 24mo max = 7.40 (well above current,
        # so we don't trip the 24-month-high branch). Current 5.10 vs min 5.00
        # → 2% above min, comfortably inside the 5% threshold.
        history = [
            _row(current, 10),
            _row(5.00, 200),  # 12mo min
            _row(7.40, 600),  # 24mo max, outside 12mo window
        ]

        def fake_get_history(key: str, *, days: int) -> list[dict[str, Any]]:
            if key == "fred:TERMCBAUTO48NS":
                return history
            return []

        with (
            patch(
                "src.api.macro_snapshot.macro_series_store.get_latest",
                side_effect=fake_get_latest,
            ),
            patch(
                "src.api.macro_snapshot.macro_series_store.get_history",
                side_effect=fake_get_history,
            ),
        ):
            result = get_macro_snapshot("user-1")

        apr = result["auto_loan_apr"]
        assert apr is not None
        assert apr["context"] == "near 12-mo low"

    def test_context_null_in_the_middle(self) -> None:
        """Current value sitting comfortably between extremes → no label."""
        from src.api.macro_snapshot import get_macro_snapshot

        current = 6.20  # well below 7.40 max, well above 5.00 min

        def fake_get_latest(key: str) -> dict[str, Any] | None:
            if key == "fred:TERMCBAUTO48NS":
                return {"value": current, "timestamp": _ts_days_ago(10)}
            return None

        history = [
            _row(current, 10),
            _row(7.40, 200),
            _row(5.00, 300),
        ]

        def fake_get_history(key: str, *, days: int) -> list[dict[str, Any]]:
            if key == "fred:TERMCBAUTO48NS":
                return history
            return []

        with (
            patch(
                "src.api.macro_snapshot.macro_series_store.get_latest",
                side_effect=fake_get_latest,
            ),
            patch(
                "src.api.macro_snapshot.macro_series_store.get_history",
                side_effect=fake_get_history,
            ),
        ):
            result = get_macro_snapshot("user-1")

        apr = result["auto_loan_apr"]
        assert apr is not None
        assert apr["context"] is None


# -------------------- CPI --------------------


class TestCpiUsedCars:
    def test_mom_yoy_computed_from_history(self) -> None:
        """30d-ago and 365d-ago rows drive month-over-month and year-over-year."""
        from src.api.macro_snapshot import get_macro_snapshot

        current = 160.2

        def fake_get_latest(key: str) -> dict[str, Any] | None:
            if key == "fred:CUUR0000SETA02":
                return {"value": current, "timestamp": _ts_days_ago(0)}
            return None

        # 30d ago value 160.84 → mom = -0.4%, 365d ago value 156.9 → yoy ≈ +2.1%.
        history = [
            _row(current, 0),
            _row(160.84, 30),
            _row(156.9, 365),
        ]

        def fake_get_history(key: str, *, days: int) -> list[dict[str, Any]]:
            if key == "fred:CUUR0000SETA02":
                return history
            return []

        with (
            patch(
                "src.api.macro_snapshot.macro_series_store.get_latest",
                side_effect=fake_get_latest,
            ),
            patch(
                "src.api.macro_snapshot.macro_series_store.get_history",
                side_effect=fake_get_history,
            ),
        ):
            result = get_macro_snapshot("user-1")

        cpi = result["cpi_used_cars"]
        assert cpi is not None
        assert cpi["value"] == 160.2
        assert cpi["mom_change_pct"] == pytest.approx(-0.4, abs=0.05)
        assert cpi["yoy_change_pct"] == pytest.approx(2.1, abs=0.1)

    def test_mom_yoy_null_when_no_history(self) -> None:
        """Single-point cache means we can't compute deltas — both null."""
        from src.api.macro_snapshot import get_macro_snapshot

        current = 160.0

        def fake_get_latest(key: str) -> dict[str, Any] | None:
            if key == "fred:CUUR0000SETA02":
                return {"value": current, "timestamp": _ts_days_ago(0)}
            return None

        # Only today's row in history; no prior 30d / 365d points.
        history = [_row(current, 0)]

        def fake_get_history(key: str, *, days: int) -> list[dict[str, Any]]:
            if key == "fred:CUUR0000SETA02":
                return history
            return []

        with (
            patch(
                "src.api.macro_snapshot.macro_series_store.get_latest",
                side_effect=fake_get_latest,
            ),
            patch(
                "src.api.macro_snapshot.macro_series_store.get_history",
                side_effect=fake_get_history,
            ),
        ):
            result = get_macro_snapshot("user-1")

        cpi = result["cpi_used_cars"]
        assert cpi is not None
        assert cpi["mom_change_pct"] is None
        assert cpi["yoy_change_pct"] is None

    def test_cpi_independent_of_apr(self) -> None:
        """CPI cached, APR not → APR null, CPI present."""
        from src.api.macro_snapshot import get_macro_snapshot

        def fake_get_latest(key: str) -> dict[str, Any] | None:
            if key == "fred:CUUR0000SETA02":
                return {"value": 160.2, "timestamp": _ts_days_ago(0)}
            return None

        with (
            patch(
                "src.api.macro_snapshot.macro_series_store.get_latest",
                side_effect=fake_get_latest,
            ),
            patch("src.api.macro_snapshot.macro_series_store.get_history", return_value=[]),
        ):
            result = get_macro_snapshot("user-1")

        assert result["auto_loan_apr"] is None
        assert result["cpi_used_cars"] is not None
        assert result["cpi_used_cars"]["value"] == 160.2
