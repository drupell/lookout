"""Tests for the used-vs-new arbitrage pipeline.

Two surfaces under test:
  1. `src.tools.external.auto_dev.fetch_make_model_comps` — the auto.dev
     client. Mock httpx so no real network is hit; the autouse TEST_MODE
     fixture in conftest forces the short-circuit path, and a `live_env`
     fixture flips it off when we want to exercise the real HTTP code path.
  2. `src.api.used_vs_new.get_used_vs_new_arbitrage` — the API handler.
     Mock both the auto.dev client and the macro-series store so each
     scenario is one decision boundary at a time.

Mirrors the layout in `test_afdc_client.py` + `test_macro_snapshot_api.py`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

# --------------------------------------------------------------------------
# auto.dev client
# --------------------------------------------------------------------------


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable TEST_MODE so the client actually exercises the HTTP path."""
    monkeypatch.delenv("TEST_MODE", raising=False)
    monkeypatch.setenv("AUTODEV_API_KEY", "fake-key")


def _mock_response(*, status_code: int = 200, payload: Any = None) -> httpx.Response:
    content = json.dumps(payload if payload is not None else {}).encode()
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "https://api.auto.dev/listings"),
        content=content,
    )


def _records_payload(prices: list[float]) -> dict[str, Any]:
    """Mirror the auto.dev `/listings` shape: `{records: [{priceUnformatted}, ...]}`."""
    return {
        "records": [{"priceUnformatted": p} for p in prices],
        "hits": len(prices),
    }


class TestFetchMakeModelComps:
    def test_test_mode_short_circuits(self) -> None:
        """conftest pins TEST_MODE=true; the client must not touch HTTP."""
        from src.tools.external import auto_dev

        get_mock = MagicMock()
        with patch("httpx.Client.get", get_mock):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )
        assert result is None
        get_mock.assert_not_called()

    def test_missing_api_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_MODE", raising=False)
        monkeypatch.delenv("AUTODEV_API_KEY", raising=False)
        monkeypatch.delenv("AUTODEV_API_KEY_SECRET_ARN", raising=False)
        from src.tools.external import auto_dev

        get_mock = MagicMock()
        with patch("httpx.Client.get", get_mock):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )
        assert result is None
        get_mock.assert_not_called()

    def test_missing_inputs_return_none(self, live_env: None) -> None:
        from src.tools.external import auto_dev

        with patch("httpx.Client.get") as get_mock:
            result = auto_dev.fetch_make_model_comps(
                zip_code="",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )
        assert result is None
        get_mock.assert_not_called()

    def test_happy_path_returns_medians_and_counts(self, live_env: None) -> None:
        """Two HTTP calls — one new, one used — each producing a median."""
        from src.tools.external import auto_dev

        new_prices = [33000, 33500, 34000, 33200, 33800]
        used_prices = [31000, 31500, 31300, 31200, 31350]

        def fake_get(self, url, params=None, **kwargs):
            used_flag = (params or {}).get("retailListing.used")
            if used_flag == "false":
                return _mock_response(payload=_records_payload(new_prices))
            return _mock_response(payload=_records_payload(used_prices))

        with patch("httpx.Client.get", new=fake_get):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )

        assert result is not None
        # Statistical median of 5 evenly-spread prices is the middle value.
        assert result["new_median_usd"] == pytest.approx(33500.0)
        assert result["used_1_2yr_median_usd"] == pytest.approx(31300.0)
        assert result["new_count"] == 5
        assert result["used_count"] == 5
        assert "as_of" in result and isinstance(result["as_of"], str)

    def test_http_error_returns_none(self, live_env: None) -> None:
        from src.tools.external import auto_dev

        with patch("httpx.Client.get", side_effect=httpx.ConnectError("boom")):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )
        assert result is None

    def test_non_200_returns_none(self, live_env: None) -> None:
        from src.tools.external import auto_dev

        with patch(
            "httpx.Client.get",
            return_value=_mock_response(status_code=429, payload={"error": "rate"}),
        ):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )
        assert result is None

    def test_both_sides_empty_returns_none(self, live_env: None) -> None:
        """Both queries returning zero records collapses to None."""
        from src.tools.external import auto_dev

        with patch(
            "httpx.Client.get",
            return_value=_mock_response(payload={"records": [], "hits": 0}),
        ):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )
        assert result is None

    def test_drops_implausible_prices_from_median(self, live_env: None) -> None:
        """A `$1` placeholder or `$2,000,000` mis-mapped MSRP must not poison the median."""
        from src.tools.external import auto_dev

        # Mix in noise: $1 placeholder + a $2M MSRP-shaped value.
        new_prices = [1, 33000, 33500, 34000, 33200, 33800, 2_000_000]
        used_prices = [31000, 31500, 31300, 31200, 31350]

        def fake_get(self, url, params=None, **kwargs):
            used_flag = (params or {}).get("retailListing.used")
            if used_flag == "false":
                return _mock_response(payload=_records_payload(new_prices))
            return _mock_response(payload=_records_payload(used_prices))

        with patch("httpx.Client.get", new=fake_get):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )

        assert result is not None
        # Median of the 5 surviving new prices remains 33500.
        assert result["new_median_usd"] == pytest.approx(33500.0)
        assert result["new_count"] == 5

    def test_accepts_string_price_field(self, live_env: None) -> None:
        """Records that ship `price` as `"$33,500"` strings are coerced."""
        from src.tools.external import auto_dev

        payload = {
            "records": [
                {"price": "$33,000"},
                {"price": "$33,500"},
                {"price": "$34,000"},
                {"price": "$33,200"},
                {"price": "$33,800"},
            ]
        }
        with patch("httpx.Client.get", return_value=_mock_response(payload=payload)):
            result = auto_dev.fetch_make_model_comps(
                zip_code="02139",
                radius_miles=50,
                make="Hyundai",
                model="Ioniq 5",
            )
        # Both sides share the same fake response (we don't differentiate
        # here), so both medians compute and the function returns.
        assert result is not None
        assert result["new_median_usd"] == pytest.approx(33500.0)


# --------------------------------------------------------------------------
# `/me/used-vs-new-arbitrage` handler
# --------------------------------------------------------------------------


class _FakePrefs:
    """Minimal duck-typed prefs object matching the attrs the handler reads."""

    class _Search:
        def __init__(self, *, location_zip: str, radius_miles: int) -> None:
            self.location_zip = location_zip
            self.radius_miles = radius_miles

    def __init__(
        self,
        *,
        included_brands: list[str],
        location_zip: str = "02139",
        radius_miles: int = 50,
    ) -> None:
        self.included_brands = included_brands
        self.search = self._Search(location_zip=location_zip, radius_miles=radius_miles)


def _patch_prefs(prefs: _FakePrefs | None):
    """Patch the lazy `load_preferences` import inside the handler."""
    if prefs is None:
        return patch("src.config.loader.load_preferences", side_effect=RuntimeError("boom"))
    return patch("src.config.loader.load_preferences", return_value=prefs)


class TestGetUsedVsNewArbitrage:
    def test_tight_verdict_happy_path(self) -> None:
        """Small spread → tight verdict surfaces the editorial pill."""
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        comps = {
            "new_median_usd": 33500.0,
            "used_1_2yr_median_usd": 31350.0,  # $2,150 below new → tight ($3k threshold)
            "new_count": 12,
            "used_count": 9,
            "as_of": "2026-05-25T06:00:00+00:00",
        }
        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch(
                "src.api.used_vs_new.auto_dev.fetch_make_model_comps",
                return_value=comps,
            ),
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=None,
            ),
            patch("src.api.used_vs_new.macro_series_store.write_observation") as write_mock,
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is not None
        assert result["verdict"] == "tight"
        assert result["spread_usd"] == 2150
        assert result["median_new_usd"] == 33500
        assert result["median_used_usd"] == 31350
        # spread_pct = 2150 / 33500 * 100 ≈ 6.4
        assert 6.0 <= result["spread_pct"] <= 6.8
        assert result["label_short"] == "Used 1-2yr"
        # Fresh fetch should land in cache for next call.
        write_mock.assert_called_once()

    def test_wide_verdict_happy_path(self) -> None:
        """Used 1-2yr is sharply below new → wide verdict."""
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        comps = {
            "new_median_usd": 41000.0,
            "used_1_2yr_median_usd": 31800.0,  # $9,200 below new → wide
            "new_count": 14,
            "used_count": 11,
            "as_of": "2026-05-25T06:00:00+00:00",
        }
        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch(
                "src.api.used_vs_new.auto_dev.fetch_make_model_comps",
                return_value=comps,
            ),
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=None,
            ),
            patch("src.api.used_vs_new.macro_series_store.write_observation"),
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is not None
        assert result["verdict"] == "wide"
        assert result["spread_usd"] == 9200
        # 9200 / 41000 * 100 ≈ 22.4
        assert 22.0 <= result["spread_pct"] <= 23.0

    def test_normal_verdict_suppressed_to_none(self) -> None:
        """Mid-range spread → return None so the pill stays hidden."""
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        # $5,000 below new on a $35k base = 14.3% → between 8% and 18%,
        # absolute is also between $3k and $8k. Normal.
        comps = {
            "new_median_usd": 35000.0,
            "used_1_2yr_median_usd": 30000.0,
            "new_count": 10,
            "used_count": 10,
            "as_of": "2026-05-25T06:00:00+00:00",
        }
        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch(
                "src.api.used_vs_new.auto_dev.fetch_make_model_comps",
                return_value=comps,
            ),
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=None,
            ),
            patch("src.api.used_vs_new.macro_series_store.write_observation"),
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is None

    def test_no_primary_brand_returns_none(self) -> None:
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        prefs = _FakePrefs(included_brands=[])

        with (
            _patch_prefs(prefs),
            patch("src.api.used_vs_new.auto_dev.fetch_make_model_comps") as fetch_mock,
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is None
        # We should never burn an auto.dev call when we can't pick a model.
        fetch_mock.assert_not_called()

    def test_unknown_brand_returns_none(self) -> None:
        """A brand without a default-model entry skips the fetch."""
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        prefs = _FakePrefs(included_brands=["BrandThatDoesntExist"])

        with (
            _patch_prefs(prefs),
            patch("src.api.used_vs_new.auto_dev.fetch_make_model_comps") as fetch_mock,
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is None
        fetch_mock.assert_not_called()

    def test_autodev_failure_returns_none(self) -> None:
        """auto.dev returning None must degrade silently, never raise."""
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch(
                "src.api.used_vs_new.auto_dev.fetch_make_model_comps",
                return_value=None,
            ),
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=None,
            ),
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is None

    def test_autodev_raising_returns_none(self) -> None:
        """An unexpected exception from auto.dev must not 500 the handler."""
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch(
                "src.api.used_vs_new.auto_dev.fetch_make_model_comps",
                side_effect=RuntimeError("network down"),
            ),
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=None,
            ),
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is None

    def test_prefs_load_failure_returns_none(self) -> None:
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        with (
            _patch_prefs(None),
            patch("src.api.used_vs_new.auto_dev.fetch_make_model_comps") as fetch_mock,
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is None
        fetch_mock.assert_not_called()

    def test_insufficient_comps_returns_none(self) -> None:
        """Below `_MIN_COMP_COUNT` listings on either side → suppress."""
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        comps = {
            "new_median_usd": 33500.0,
            "used_1_2yr_median_usd": 31350.0,
            "new_count": 2,  # too few to trust
            "used_count": 9,
            "as_of": "2026-05-25T06:00:00+00:00",
        }
        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch(
                "src.api.used_vs_new.auto_dev.fetch_make_model_comps",
                return_value=comps,
            ),
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=None,
            ),
            patch("src.api.used_vs_new.macro_series_store.write_observation"),
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is None

    def test_cache_hit_skips_autodev_call(self) -> None:
        """A fresh cached row inside the 24h TTL must bypass the auto.dev call."""
        from datetime import UTC, datetime

        from src.api.used_vs_new import get_used_vs_new_arbitrage

        comps = {
            "new_median_usd": 33500.0,
            "used_1_2yr_median_usd": 31350.0,
            "new_count": 12,
            "used_count": 9,
            "as_of": "2026-05-25T06:00:00+00:00",
        }
        cached_row = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "provider_metadata": {"comps": comps},
        }
        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch("src.api.used_vs_new.auto_dev.fetch_make_model_comps") as fetch_mock,
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=cached_row,
            ),
            patch("src.api.used_vs_new.macro_series_store.write_observation") as write_mock,
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is not None
        assert result["verdict"] == "tight"
        fetch_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_stale_cache_falls_through_to_live_fetch(self) -> None:
        """A cache row older than 24h is ignored; we re-fetch from auto.dev."""
        from datetime import UTC, datetime, timedelta

        from src.api.used_vs_new import get_used_vs_new_arbitrage

        live_comps = {
            "new_median_usd": 33500.0,
            "used_1_2yr_median_usd": 31350.0,
            "new_count": 12,
            "used_count": 9,
            "as_of": "2026-05-25T06:00:00+00:00",
        }
        stale_row = {
            "fetched_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
            "provider_metadata": {"comps": {"new_median_usd": 99999.0}},
        }
        prefs = _FakePrefs(included_brands=["Hyundai"])

        with (
            _patch_prefs(prefs),
            patch(
                "src.api.used_vs_new.auto_dev.fetch_make_model_comps",
                return_value=live_comps,
            ) as fetch_mock,
            patch(
                "src.api.used_vs_new.macro_series_store.get_latest",
                return_value=stale_row,
            ),
            patch("src.api.used_vs_new.macro_series_store.write_observation"),
        ):
            result = get_used_vs_new_arbitrage("user-1")

        assert result is not None
        # We used the live comps, not the stale row's bogus $99,999 median.
        assert result["median_new_usd"] == 33500
        fetch_mock.assert_called_once()
