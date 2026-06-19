"""Unit tests for MarketCheckClient — mocks the API, verifies parsing and error handling."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from src.tools.listings.base import ListingSource
from src.tools.listings.marketcheck import (
    MarketCheckClient,
    MarketCheckPaginationExceeded,
    MarketCheckQuotaExhausted,
    _canonical_fuel_type,
    _parse_listing,
)


class TestMarketCheckClient:
    def test_satisfies_listing_source_protocol(self):
        """MarketCheckClient must be usable as a ListingSource."""
        client = MarketCheckClient(api_key="fake")
        assert isinstance(client, ListingSource)

    def test_missing_api_key_raises(self):
        client = MarketCheckClient(api_key="")
        with pytest.raises(RuntimeError, match="MarketCheck API key not configured"):
            client.fetch_listings("10001", 75, 3)

    def test_auth_error_raises_clear_message(self):
        client = MarketCheckClient(api_key="bad")
        mock_response = httpx.Response(status_code=401, request=httpx.Request("GET", "x"))
        with (
            patch("httpx.Client.get", return_value=mock_response),
            pytest.raises(RuntimeError, match="MarketCheck auth failed"),
        ):
            client.fetch_listings("10001", 75, 3)

    def test_422_on_page_2_raises_pagination_exceeded(self):
        """Free-tier 500-row pagination ceiling — distinct from generic 4xx.

        Page 1 (start=0) returns a full 500 rows; the client tries page 2
        (start=500); MarketCheck returns 422. We map that specifically to
        `MarketCheckPaginationExceeded` so the source_errors row tells the
        user to drop max_pages instead of "service unavailable, try again."
        """
        client = MarketCheckClient(api_key="fake")
        # Page 1 fills the bucket with valid (but excluded-brand) listings
        # so num_found stays >= 500 and the loop tries page 2.
        page1 = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "4"},
            json={
                "num_found": 1500,
                "listings": [
                    {
                        "vin": f"V{i:017d}",
                        "price": 30000,
                        "msrp": 35000,
                        "dist": 10,
                        "vdp_url": f"https://example.com/{i}",
                        "build": {
                            "make": "Bentley",  # excluded by default-tier filter
                            "model": "Continental",
                            "year": 2024,
                            "fuel_type": "Electric",
                        },
                        "dealer": {"name": "D", "distance_miles": 10},
                        "media": {"photo_links": []},
                    }
                    for i in range(500)
                ],
            },
        )
        page2_422 = httpx.Response(
            status_code=422,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "4"},
            content=b"Unprocessable",
        )
        with (
            patch("httpx.Client.get", side_effect=[page1, page2_422]),
            patch("time.sleep"),
            pytest.raises(
                MarketCheckPaginationExceeded,
                match="500-row pagination ceiling",
            ),
        ):
            client.fetch_listings(
                "10001",
                75,
                3,
                excluded_brands=["Bentley"],  # forces page 1 filter to drop everything
                target_count=200,
                max_pages=3,
            )

    def test_422_on_page_1_is_generic_4xx(self):
        """422 with start=0 isn't the pagination ceiling — re-raise generically."""
        client = MarketCheckClient(api_key="fake")
        mock_response = httpx.Response(
            status_code=422, request=httpx.Request("GET", "x"), content=b"bad params"
        )
        with (
            patch("httpx.Client.get", return_value=mock_response),
            # Should bubble as httpx.HTTPStatusError, NOT MarketCheckPaginationExceeded
            pytest.raises(httpx.HTTPStatusError),
        ):
            client.fetch_listings("10001", 75, 3)

    def test_rate_limit_raises_clear_message_after_retry(self):
        """When MarketCheck 429s twice in a row, raise (don't loop forever)."""
        client = MarketCheckClient(api_key="fake")
        mock_response = httpx.Response(
            status_code=429,
            request=httpx.Request("GET", "x"),
            headers={"retry-after": "1"},
        )
        with (
            patch("httpx.Client.get", return_value=mock_response),
            patch("time.sleep"),  # don't actually wait in tests
            pytest.raises(RuntimeError, match="rate limit"),
        ):
            client.fetch_listings("10001", 75, 3)

    def test_preflight_quota_check_raises_when_shared_bucket_full(self):
        """Default-tier shared key with calls >= 500 → QuotaExhausted before any HTTP call."""
        client = MarketCheckClient(api_key="fake", api_key_id="shared")
        with (
            patch(
                "src.memory.api_usage_store.get_usage",
                return_value={"calls": 500, "yyyy_mm": "2026-06"},
            ),
            pytest.raises(MarketCheckQuotaExhausted, match="monthly quota reached"),
        ):
            client.fetch_listings("10001", 75, 3)

    def test_preflight_skipped_for_byok_keys(self):
        """BYOK users have unknown plan quotas — pre-flight should not gate them."""
        client = MarketCheckClient(api_key="fake", api_key_id="byok:user-1")
        success = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "4"},
            json={"num_found": 0, "listings": []},
        )
        # If pre-flight WERE running for BYOK, this 9999 would trip it.
        with (
            patch(
                "src.memory.api_usage_store.get_usage",
                return_value={"calls": 9999, "yyyy_mm": "2026-06"},
            ),
            patch("httpx.Client.get", return_value=success),
            patch("src.memory.api_usage_store.increment_calls", return_value=1),
        ):
            # Should not raise — quota check is skipped for byok.
            client.fetch_listings("10001", 75, 3)

    def test_record_call_increments_counter_per_request(self):
        """Every API attempt bumps the per-key monthly counter."""
        client = MarketCheckClient(api_key="fake", api_key_id="shared")
        success = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "4"},
            json={"num_found": 0, "listings": []},
        )
        with (
            patch(
                "src.memory.api_usage_store.get_usage",
                return_value={"calls": 0, "yyyy_mm": "2026-06"},
            ),
            patch("httpx.Client.get", return_value=success),
            patch("src.memory.api_usage_store.increment_calls") as inc,
        ):
            client.fetch_listings("10001", 75, 3)
        inc.assert_called_once_with("shared")

    def test_record_call_swallows_store_failure(self):
        """An exception from the counter must never break the run."""
        client = MarketCheckClient(api_key="fake", api_key_id="shared")
        success = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "4"},
            json={"num_found": 0, "listings": []},
        )
        with (
            patch(
                "src.memory.api_usage_store.get_usage",
                return_value={"calls": 0, "yyyy_mm": "2026-06"},
            ),
            patch("httpx.Client.get", return_value=success),
            patch(
                "src.memory.api_usage_store.increment_calls",
                side_effect=RuntimeError("boom"),
            ),
        ):
            # Must not raise.
            result = client.fetch_listings("10001", 75, 3)
        assert result == []

    def test_429_then_success_recovers_via_retry(self):
        """Single 429 followed by a 200 should succeed (one transparent retry)."""
        client = MarketCheckClient(api_key="fake")
        rate_limited = httpx.Response(
            status_code=429,
            request=httpx.Request("GET", "x"),
            headers={"retry-after": "1"},
        )
        success = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "4"},
            json={"num_found": 0, "listings": []},
        )
        with (
            patch("httpx.Client.get", side_effect=[rate_limited, success]),
            patch("time.sleep"),
        ):
            result = client.fetch_listings("10001", 75, 3)
        assert result == []  # 0 listings, but no exception

    def test_throttles_when_remaining_zero(self):
        """A 200 response with remaining=0 arms cool-off; next call sleeps."""
        client = MarketCheckClient(api_key="fake")
        page1 = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "0", "retry-after": "2"},
            json={
                # num_found > PAGE_SIZE forces the client to attempt page 2,
                # which is what triggers the cool-off sleep we're asserting on.
                "num_found": 1500,
                "listings": [
                    {
                        "vin": f"V{i:017d}",
                        "price": 30000,
                        "msrp": 35000,
                        "dist": 10,
                        "vdp_url": f"https://example.com/{i}",
                        "build": {
                            "make": "Tesla",
                            "model": "Model 3",
                            "year": 2024,
                            "fuel_type": "Electric",
                        },
                        "dealer": {"name": "D", "distance_miles": 10},
                        "media": {"photo_links": []},
                    }
                    for i in range(50)
                ],
            },
        )
        page2 = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            headers={"ratelimit-remaining": "4"},
            json={"num_found": 1500, "listings": []},
        )
        sleep_calls: list[float] = []
        with (
            patch("httpx.Client.get", side_effect=[page1, page2]),
            patch("time.sleep", side_effect=sleep_calls.append),
        ):
            client.fetch_listings("10001", 75, 3, target_count=200, max_pages=3)
        # The cool-off armed by page1 must have caused at least one sleep
        # before page2's request fired.
        assert any(s > 0 for s in sleep_calls), (
            f"expected a throttle sleep before page2; saw sleeps={sleep_calls}"
        )

    def test_parses_successful_response(self):
        payload = {
            "num_found": 2,
            "listings": [
                {
                    "vin": "1FADP3K22JL123456",
                    "price": 34500,
                    "msrp": 38000,
                    "dist": 12.5,
                    "vdp_url": "https://example.com/listing/1",
                    "build": {
                        "make": "Chevrolet",
                        "model": "Equinox EV",
                        "year": 2024,
                        "fuel_type": "Electric",
                    },
                    "dealer": {"name": "Dealer A", "distance_miles": 12.5},
                },
                {
                    "vin": "5YJ3E1EA4JF123456",
                    "price": 42000,
                    "dist": 25.0,
                    "build": {
                        "make": "Tesla",
                        "model": "Model 3",
                        "year": 2023,
                        "fuel_type": "Electric",
                    },
                    "dealer": {"name": "Dealer B"},
                },
            ],
        }
        mock_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            content=json.dumps(payload).encode(),
        )

        client = MarketCheckClient(api_key="fake")
        with patch("httpx.Client.get", return_value=mock_response):
            listings = client.fetch_listings("10001", 75, 3)

        assert len(listings) == 2
        assert listings[0]["make"] == "Chevrolet"
        assert listings[0]["selling_price"] == 34500.0
        assert listings[0]["msrp"] == 38000.0
        assert listings[0]["listing_id"].startswith("mc:")
        assert listings[0]["source"] == "marketcheck"
        assert listings[1]["msrp"] is None  # not provided on this one

    def test_skips_malformed_listings(self):
        payload = {
            "listings": [
                {"build": {"make": "Ford"}},  # missing year/model/price — should skip
                {
                    "price": 30000,
                    "build": {
                        "make": "Kia",
                        "model": "EV6",
                        "year": 2024,
                        "fuel_type": "Electric",
                    },
                    "dealer": {"name": "Test"},
                },
            ]
        }
        mock_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            content=json.dumps(payload).encode(),
        )
        client = MarketCheckClient(api_key="fake")
        with patch("httpx.Client.get", return_value=mock_response):
            listings = client.fetch_listings("10001", 75, 3)
        assert len(listings) == 1
        assert listings[0]["make"] == "Kia"

    def test_post_filters_year_range(self):
        """Listings outside requested year range are dropped."""
        payload = {
            "listings": [
                {
                    "vin": "IN_RANGE",
                    "price": 30000,
                    "build": {
                        "make": "Tesla",
                        "model": "Model 3",
                        "year": 2024,
                        "fuel_type": "Electric",
                        "powertrain_type": "BEV",
                    },
                    "dealer": {"name": "X"},
                },
                {
                    "vin": "TOO_OLD",
                    "price": 15000,
                    "build": {
                        "make": "Nissan",
                        "model": "Leaf",
                        "year": 2015,  # outside max_vehicle_age_years=3 from 2026
                        "fuel_type": "Electric",
                        "powertrain_type": "BEV",
                    },
                    "dealer": {"name": "X"},
                },
            ]
        }
        mock_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            content=json.dumps(payload).encode(),
        )
        client = MarketCheckClient(api_key="fake")
        with patch("httpx.Client.get", return_value=mock_response):
            listings = client.fetch_listings("10001", 75, 3, fuel_types=["Electric"])
        assert len(listings) == 1
        assert listings[0]["year"] == 2024

    def test_blank_fuel_type_dropped_by_filter(self):
        """If MarketCheck doesn't report fuel_type, we can't confirm — drop it."""
        payload = {
            "listings": [
                {
                    "vin": "NOFUEL",
                    "price": 30000,
                    "build": {"make": "Kia", "model": "EV6", "year": 2024},
                    "dealer": {"name": "X"},
                },
            ]
        }
        mock_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            content=json.dumps(payload).encode(),
        )
        client = MarketCheckClient(api_key="fake")
        with patch("httpx.Client.get", return_value=mock_response):
            listings = client.fetch_listings("10001", 75, 3, fuel_types=["Electric"])
        assert len(listings) == 0

    def test_post_filters_phev_when_only_electric_requested(self):
        """MarketCheck sometimes returns PHEVs (mislabeled as Electric) — we drop them."""
        payload = {
            "listings": [
                {
                    "vin": "EV1",
                    "price": 35000,
                    "build": {
                        "make": "Tesla",
                        "model": "Model Y",
                        "year": 2024,
                        "fuel_type": "Electric",
                        "powertrain_type": "BEV",
                        "cylinders": 0,
                    },
                    "dealer": {"name": "X"},
                },
                {
                    # Real-world Jeep 4xe case: fuel_type lies, powertrain_type tells truth
                    "vin": "PHEV1",
                    "price": 40000,
                    "build": {
                        "make": "Jeep",
                        "model": "Grand Cherokee",
                        "trim": "4xe",
                        "year": 2024,
                        "fuel_type": "Electric",
                        "powertrain_type": "PHEV",
                        "cylinders": 4,
                    },
                    "dealer": {"name": "X"},
                },
            ]
        }
        mock_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            content=json.dumps(payload).encode(),
        )
        client = MarketCheckClient(api_key="fake")
        with patch("httpx.Client.get", return_value=mock_response):
            listings = client.fetch_listings("10001", 75, 3, fuel_types=["Electric"])
        assert len(listings) == 1
        assert listings[0]["fuel_type"] == "Electric"
        assert listings[0]["is_ev"] is True

    def test_empty_fuel_types_disables_filter(self):
        payload = {
            "listings": [
                {
                    "vin": "GAS1",
                    "price": 25000,
                    "build": {
                        "make": "Honda",
                        "model": "Civic",
                        "year": 2024,
                        "fuel_type": "Gasoline",
                    },
                    "dealer": {"name": "X"},
                },
            ]
        }
        mock_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            content=json.dumps(payload).encode(),
        )
        client = MarketCheckClient(api_key="fake")
        with patch("httpx.Client.get", return_value=mock_response):
            listings = client.fetch_listings("10001", 75, 3, fuel_types=[])
        assert len(listings) == 1
        assert listings[0]["is_ev"] is False

    def test_empty_results(self):
        mock_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "x"),
            content=b'{"num_found": 0, "listings": []}',
        )
        client = MarketCheckClient(api_key="fake")
        with patch("httpx.Client.get", return_value=mock_response):
            listings = client.fetch_listings("10001", 75, 3)
        assert listings == []


class TestCanonicalFuelType:
    def test_powertrain_bev_overrides_fuel_type(self):
        # BEV powertrain → Electric, regardless of misleading fuel_type
        assert _canonical_fuel_type("Gasoline", "BEV", 0) == "Electric"

    def test_powertrain_phev_overrides_electric_fuel_type(self):
        # The Jeep 4xe case: fuel_type=Electric is wrong, powertrain=PHEV is right
        assert _canonical_fuel_type("Electric", "PHEV", 4) == "Plug-in Hybrid"

    def test_powertrain_hev(self):
        assert _canonical_fuel_type("Gasoline", "HEV", 4) == "Hybrid"

    def test_powertrain_ice_keeps_raw_fuel_type(self):
        # ICE — trust the raw fuel_type for Gasoline/Diesel
        assert _canonical_fuel_type("Diesel", "ICE", 6) == "Diesel"
        assert _canonical_fuel_type("Gasoline", "ICE", 4) == "Gasoline"

    def test_no_powertrain_electric_with_cylinders_demoted(self):
        # No powertrain_type — but cylinders > 0 means it's not a real BEV
        assert _canonical_fuel_type("Electric", "", 4) == "Plug-in Hybrid"

    def test_no_powertrain_electric_no_cylinders_stays(self):
        # Real BEV: no powertrain_type, fuel_type=Electric, 0 cylinders
        assert _canonical_fuel_type("Electric", "", 0) == "Electric"

    def test_no_powertrain_keeps_raw_fuel_type(self):
        assert _canonical_fuel_type("Gasoline", "", 4) == "Gasoline"


class TestParseListing:
    def test_vin_used_as_listing_id(self):
        result = _parse_listing(
            {
                "vin": "TESTVIN123",
                "price": 30000,
                "build": {"make": "Ford", "model": "Mach-E", "year": 2024},
                "dealer": {"name": "X"},
            }
        )
        assert result is not None
        assert result["listing_id"] == "mc:TESTVIN123"

    def test_missing_vin_falls_back_to_hash(self):
        result = _parse_listing(
            {
                "price": 30000,
                "build": {"make": "Ford", "model": "Mach-E", "year": 2024},
                "dealer": {"name": "X"},
            }
        )
        assert result is not None
        assert result["listing_id"].startswith("mc:")
        assert len(result["listing_id"]) > len("mc:")

    def test_missing_required_returns_none(self):
        # Missing price
        assert (
            _parse_listing(
                {"build": {"make": "Ford", "model": "Mach-E", "year": 2024}, "dealer": {}}
            )
            is None
        )
        # Missing model
        assert (
            _parse_listing({"price": 30000, "build": {"make": "Ford", "year": 2024}, "dealer": {}})
            is None
        )
