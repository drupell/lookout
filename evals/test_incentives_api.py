"""Tests for `GET /me/incentives` — the incentive-stack API handler.

Uses moto + a seeded macro_series_store row (simulating a prior AFDC cache
fill) so we exercise the real boto3 path through the cache lookup. The
stackability YAML is read from the bundled file in src/data/ so the matrix
matches what production sees.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from moto import mock_aws

if TYPE_CHECKING:
    from collections.abc import Iterator


def _to_decimal(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_decimal(v) for v in obj]
    return obj


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MACRO_SERIES_TABLE_NAME", "lookout-test-macro-series")
    monkeypatch.setenv("USERS_TABLE_NAME", "lookout-test-users")
    # The handler calls load_preferences -> falls back to bundled YAML when
    # apply_overrides is true. We have to keep TEST_MODE *off* so the macro
    # store boto3 paths execute against moto, but the prefs loader checks
    # TEST_MODE separately to decide whether to read DynamoDB overrides.
    monkeypatch.delenv("TEST_MODE", raising=False)
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName="lookout-test-macro-series",
            AttributeDefinitions=[
                {"AttributeName": "series_key", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "series_key", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName="lookout-test-users",
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def _seed_afdc(series_key: str, programs: list[dict[str, Any]]) -> None:
    table = boto3.resource("dynamodb").Table("lookout-test-macro-series")
    ts = datetime.now(UTC).isoformat()
    item = {
        "series_key": series_key,
        "timestamp": ts,
        "value": 0.0,
        "provider_metadata": {"programs": programs},
        "fetched_at": ts,
    }
    table.put_item(Item=_to_decimal(item))


def _put_user(user_id: str, **fields: Any) -> None:
    table = boto3.resource("dynamodb").Table("lookout-test-users")
    item = {"user_id": user_id, "email": f"{user_id}@example.com", **fields}
    table.put_item(Item=_to_decimal(item))


def _federal_program(amount: int = 7500) -> dict[str, Any]:
    return {
        "id": "409",
        "title": "Section 30D",
        "type": "tax credit",
        "jurisdiction": "federal",
        "state": "US",
        "summary": f"Up to ${amount:,} federal tax credit",
        "expiration_date": None,
        "last_updated": "2026-04-15",
        "amount_usd": amount,
        "categories": ["Tax Incentives"],
        "source_url": "https://afdc.energy.gov/laws/409",
    }


def _ma_program(amount: int = 2500, expiration: str | None = None) -> dict[str, Any]:
    return {
        "id": "4378",
        "title": "MOR-EV Rebate",
        "type": "rebate",
        "jurisdiction": "state",
        "state": "MA",
        "summary": f"Rebate of up to ${amount:,}",
        "expiration_date": expiration,
        "last_updated": "2026-04-15",
        "amount_usd": amount,
        "categories": ["Rebate"],
        "source_url": "https://afdc.energy.gov/laws/4378",
    }


class TestHappyPath:
    def test_federal_plus_state_stack_totals_correctly(self, aws: None) -> None:
        from src.api.incentives import get_incentives

        # AFDC parses arbitrary amounts; canonical programs override from
        # the YAML (federal_30d=$7,500; ma_mor_ev=$3,500). The MA AFDC row
        # could parse as anything — the YAML override wins.
        _seed_afdc("afdc:US", [_federal_program(7500)])
        _seed_afdc("afdc:MA", [_ma_program(2500)])
        _put_user(
            "user-ma",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-ma")
        assert result["state"] == "MA"
        items = result["stack"]["items"]
        ids = {item["id"] for item in items}
        assert "federal_30d" in ids
        assert "ma_mor_ev" in ids
        # Both stack with each other; YAML-curated amounts: 7500 + 3500.
        assert result["stack"]["total_usd"] == 11_000
        assert result["warnings"] == []


class TestNonEvAutoHide:
    def test_non_ev_prefs_returns_hidden_reason(self, aws: None) -> None:
        from src.api.incentives import get_incentives

        _seed_afdc("afdc:US", [_federal_program()])
        _put_user(
            "user-ice",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Gasoline"]}},
        )

        result = get_incentives("user-ice")
        assert result["stack"]["items"] == []
        assert result["stack"]["total_usd"] == 0
        assert result.get("hidden_reason") == "non-ev-prefs"


class TestNoStateOnFile:
    def test_unknown_zip_returns_federal_only(self, aws: None) -> None:
        """When the zip prefix doesn't map to a state we know, we still
        surface the federal layer — partial data beats empty data."""
        from src.api.incentives import get_incentives

        _seed_afdc("afdc:US", [_federal_program()])
        _put_user(
            "user-foreign",
            prefs_overrides={
                # Prefix 999 isn't in our map -> state="UNKNOWN"
                "search": {"location_zip": "99950", "fuel_types": ["Electric"]}
            },
        )

        result = get_incentives("user-foreign")
        assert result["state"] == "UNKNOWN"
        items = result["stack"]["items"]
        # Only federal; no state programs were even attempted.
        assert len(items) == 1
        assert items[0]["id"] == "federal_30d"


class TestExpirationWarning:
    def test_expiring_program_surfaces_warning(self, aws: None) -> None:
        from src.api.incentives import get_incentives

        # Set MA program to expire in ~3 weeks → inside the 42-day state window.
        soon = (datetime.now(UTC) + timedelta(days=21)).strftime("%Y-%m-%d")
        _seed_afdc("afdc:US", [_federal_program()])
        _seed_afdc("afdc:MA", [_ma_program(amount=2500, expiration=soon)])
        _put_user(
            "user-ma2",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-ma2")
        assert any("MOR-EV" in w for w in result["warnings"])

    def test_federal_uses_ninety_day_window(self, aws: None) -> None:
        """Federal-tier programs fire warnings out to 90 days — wider than
        the original 6-week default because sunset windows are huge events."""
        from src.api.incentives import get_incentives

        # Federal program expiring in ~80 days — outside the 42-day state
        # window but inside the 90-day federal window. Override the curated
        # YAML expiration by skipping canonical mapping: use an unknown id.
        unknown_federal = {
            "id": "9001",
            "title": "Federal Pilot Credit",
            "type": "tax credit",
            "jurisdiction": "federal",
            "state": "US",
            "summary": "Up to $1,000 credit; expires in 80d.",
            "expiration_date": (datetime.now(UTC) + timedelta(days=80)).strftime("%Y-%m-%d"),
            "last_updated": "2026-04-15",
            "amount_usd": 1000,
            "categories": ["Tax Incentives"],
            "source_url": "https://afdc.energy.gov/laws/9001",
        }
        _seed_afdc("afdc:US", [unknown_federal])
        _put_user(
            "user-fed-window",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-fed-window")
        assert any("Federal Pilot Credit" in w for w in result["warnings"]), result["warnings"]

    def test_federal_outside_ninety_day_window_no_warning(self, aws: None) -> None:
        """A federal program 6 months out should NOT fire — outside even the
        widest tier window."""
        from src.api.incentives import get_incentives

        unknown_federal = {
            "id": "9002",
            "title": "Federal Distant Credit",
            "type": "tax credit",
            "jurisdiction": "federal",
            "state": "US",
            "summary": "Distant",
            "expiration_date": (datetime.now(UTC) + timedelta(days=180)).strftime("%Y-%m-%d"),
            "last_updated": "2026-04-15",
            "amount_usd": 1000,
            "categories": ["Tax Incentives"],
            "source_url": "https://afdc.energy.gov/laws/9002",
        }
        _seed_afdc("afdc:US", [unknown_federal])
        _put_user(
            "user-fed-no-warn",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-fed-no-warn")
        assert not any("Federal Distant Credit" in w for w in result["warnings"])

    def test_state_window_is_forty_two_days(self, aws: None) -> None:
        """State programs fire inside the 42-day window, not at 60 days out."""
        from src.api.incentives import get_incentives

        # 50 days out — outside 42-day state window.
        far_state = {
            **_ma_program(
                amount=2500,
                expiration=(datetime.now(UTC) + timedelta(days=50)).strftime("%Y-%m-%d"),
            ),
        }
        _seed_afdc("afdc:US", [])
        _seed_afdc("afdc:MA", [far_state])
        _put_user(
            "user-state-far",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-state-far")
        assert not any("MOR-EV" in w for w in result["warnings"])

    def test_utility_window_is_twenty_one_days(self, aws: None) -> None:
        """Utility programs fire inside the 21-day window only.

        A 30-day-out utility program should NOT warn; a 10-day-out one SHOULD.
        """
        from src.api.incentives import get_incentives

        utility_far = {
            "id": "11000",
            "title": "Utility Far",
            "type": "rebate",
            "jurisdiction": "utility",
            "state": "MA",
            "summary": "Rebate",
            "expiration_date": (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d"),
            "last_updated": "2026-04-15",
            "amount_usd": 500,
            "categories": ["Rebate"],
            "source_url": "https://afdc.energy.gov/laws/11000",
        }
        utility_near = {
            "id": "11001",
            "title": "Utility Near",
            "type": "rebate",
            "jurisdiction": "utility",
            "state": "MA",
            "summary": "Rebate",
            "expiration_date": (datetime.now(UTC) + timedelta(days=10)).strftime("%Y-%m-%d"),
            "last_updated": "2026-04-15",
            "amount_usd": 500,
            "categories": ["Rebate"],
            "source_url": "https://afdc.energy.gov/laws/11001",
        }
        _seed_afdc("afdc:US", [])
        _seed_afdc("afdc:MA", [utility_far, utility_near])
        _put_user(
            "user-utility-windows",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-utility-windows")
        assert any("Utility Near" in w for w in result["warnings"])
        assert not any("Utility Far" in w for w in result["warnings"])

    def test_warnings_sorted_by_urgency(self, aws: None) -> None:
        """Most-imminent expirations sort first."""
        from src.api.incentives import get_incentives

        soon = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
        later = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")

        state_soon = {
            **_ma_program(amount=2500, expiration=soon),
            "title": "Soon State",
            "id": "70011",
        }
        state_later = {
            **_ma_program(amount=2500, expiration=later),
            "title": "Later State",
            "id": "70012",
        }
        _seed_afdc("afdc:US", [])
        _seed_afdc("afdc:MA", [state_later, state_soon])  # Out of order seed.
        _put_user(
            "user-sort",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-sort")
        warnings = result["warnings"]
        # The first warning emitted should be the most urgent.
        soon_idx = next((i for i, w in enumerate(warnings) if "Soon State" in w), -1)
        later_idx = next((i for i, w in enumerate(warnings) if "Later State" in w), -1)
        assert soon_idx >= 0
        assert later_idx >= 0
        assert soon_idx < later_idx

    def test_yaml_curated_expiration_overrides_parsed(self, aws: None) -> None:
        """Curated YAML expiration on a canonical program wins over the
        AFDC-parsed date — the YAML carries the statutory IRA horizon for
        federal credits, which we know better than the regex can derive."""
        from src.api.incentives import get_incentives

        # federal_30d (AFDC id=409) with a soon-expiring AFDC date. The
        # YAML's "2032-12-31" should take precedence and suppress the warning.
        soon = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
        broken_federal = {
            "id": "409",  # Maps to federal_30d in afdc_id_map.
            "title": "Section 30D",
            "type": "tax credit",
            "jurisdiction": "federal",
            "state": "US",
            "summary": "Body",
            "expiration_date": soon,
            "last_updated": "2026-04-15",
            "amount_usd": 7500,
            "categories": ["Tax Incentives"],
            "source_url": "https://afdc.energy.gov/laws/409",
        }
        _seed_afdc("afdc:US", [broken_federal])
        _put_user(
            "user-yaml-exp",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-yaml-exp")
        federal_item = next(
            (i for i in result["stack"]["items"] if i["id"] == "federal_30d"),
            None,
        )
        assert federal_item is not None
        # The curated YAML date (2032-12-31) wins.
        assert federal_item["expiration_date"] == "2032-12-31"
        # 2032 is way outside the 90-day federal window — no warning fires.
        assert not any("Section 30D" in w for w in result["warnings"])
        assert not any("Federal Section 30D" in w for w in result["warnings"])

    def test_warning_copy_includes_iso_date_and_days_remaining(self, aws: None) -> None:
        """New copy shape — "{title} expires in {N} days ({YYYY-MM-DD}) — only stacks if claimed before then" for stackable items."""
        from src.api.incentives import get_incentives

        soon_date = (datetime.now(UTC) + timedelta(days=10)).strftime("%Y-%m-%d")
        _seed_afdc("afdc:US", [])
        _seed_afdc("afdc:MA", [_ma_program(amount=2500, expiration=soon_date)])
        _put_user(
            "user-copy",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-copy")
        warning = next((w for w in result["warnings"] if "MOR-EV" in w), None)
        assert warning is not None
        # Copy must include the ISO date.
        assert soon_date in warning
        # And the days-remaining count.
        assert "days" in warning
        # And the "only stacks if claimed before then" clause for stackable
        # state programs.
        assert "stacks" in warning


class TestEmptyCache:
    def test_no_cached_programs_returns_empty_items(self, aws: None) -> None:
        """AFDC unavailable -> no items -> still 200, not 500."""
        from unittest.mock import patch

        from src.api.incentives import get_incentives

        _put_user(
            "user-empty",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        # Block the lazy-fetch path — we want to assert the "no data anywhere"
        # behavior, not "lazy fetch saves us." With both federal + state set
        # to return None, the handler degrades cleanly to empty items.
        with (
            patch("src.tools.external.afdc.fetch_federal_incentives", return_value=None),
            patch("src.tools.external.afdc.fetch_state_incentives", return_value=None),
        ):
            result = get_incentives("user-empty")
        assert result["stack"]["items"] == []
        assert result["stack"]["total_usd"] == 0
        assert result["warnings"] == []


class TestLazyFetch:
    """When the cache is cold, the handler fetches inline + caches."""

    def test_cold_cache_triggers_inline_fetch_and_persists(self, aws: None) -> None:
        from unittest.mock import patch

        from src.api.incentives import get_incentives
        from src.memory import macro_series_store

        _put_user(
            "user-lazy",
            prefs_overrides={
                "search": {"location_zip": "10001", "fuel_types": ["Electric"]},  # NY
            },
        )
        fed = [_federal_program(7500)]
        state = [_ma_program(amount=2500)]  # Reuse MA fixture shape for NY.

        with (
            patch("src.tools.external.afdc.fetch_federal_incentives", return_value=fed),
            patch("src.tools.external.afdc.fetch_state_incentives", return_value=state),
        ):
            result = get_incentives("user-lazy")

        # The handler got past the empty cache via the lazy-fetch path.
        assert result["stack"]["total_usd"] > 0

        # Cache should now contain both rows (federal + DE) — re-call with the
        # AFDC mocks pointing at a sentinel that would raise to confirm the
        # cache hit. (We use side_effect=Exception to detect any unexpected
        # second fetch.)
        with (
            patch(
                "src.tools.external.afdc.fetch_federal_incentives",
                side_effect=AssertionError("should not refetch federal"),
            ),
            patch(
                "src.tools.external.afdc.fetch_state_incentives",
                side_effect=AssertionError("should not refetch state"),
            ),
        ):
            second = get_incentives("user-lazy")
        assert second["stack"]["total_usd"] == result["stack"]["total_usd"]

        # And the cache row is actually there.
        assert macro_series_store.get_latest("afdc:US") is not None
        assert macro_series_store.get_latest("afdc:NY") is not None

    def test_stale_cache_triggers_refetch(self, aws: None) -> None:
        """A row older than _LAZY_REFETCH_DAYS triggers a live fetch."""
        from unittest.mock import patch

        from src.api.incentives import get_incentives

        # Pre-seed an outdated cache row (15 days old).
        table = boto3.resource("dynamodb").Table("lookout-test-macro-series")
        stale_ts = (datetime.now(UTC) - timedelta(days=15)).isoformat()
        table.put_item(
            Item=_to_decimal(
                {
                    "series_key": "afdc:US",
                    "timestamp": stale_ts,
                    "value": 0.0,
                    "fetched_at": stale_ts,
                    "provider_metadata": {"programs": [_federal_program(3000)]},
                }
            )
        )

        _put_user(
            "user-stale",
            prefs_overrides={
                "search": {"location_zip": "10001", "fuel_types": ["Electric"]},
            },
        )

        # Refetch should win and the new $7,500 amount should be used.
        with (
            patch(
                "src.tools.external.afdc.fetch_federal_incentives",
                return_value=[_federal_program(7500)],
            ),
            patch("src.tools.external.afdc.fetch_state_incentives", return_value=None),
        ):
            result = get_incentives("user-stale")

        federal_item = next((i for i in result["stack"]["items"] if i["id"] == "federal_30d"), None)
        assert federal_item is not None
        assert federal_item["amount_usd"] == 7500

    def test_lazy_fetch_failure_falls_back_to_stale_cache(self, aws: None) -> None:
        """A failed live fetch + stale cache → serve the stale data."""
        from unittest.mock import patch

        from src.api.incentives import get_incentives

        table = boto3.resource("dynamodb").Table("lookout-test-macro-series")
        stale_ts = (datetime.now(UTC) - timedelta(days=15)).isoformat()
        table.put_item(
            Item=_to_decimal(
                {
                    "series_key": "afdc:US",
                    "timestamp": stale_ts,
                    "value": 0.0,
                    "fetched_at": stale_ts,
                    "provider_metadata": {"programs": [_federal_program(7500)]},
                }
            )
        )

        _put_user(
            "user-fallback",
            prefs_overrides={
                "search": {"location_zip": "10001", "fuel_types": ["Electric"]},
            },
        )

        # AFDC is unreachable now. The stale cache should serve.
        with (
            patch("src.tools.external.afdc.fetch_federal_incentives", return_value=None),
            patch("src.tools.external.afdc.fetch_state_incentives", return_value=None),
        ):
            result = get_incentives("user-fallback")

        federal_item = next((i for i in result["stack"]["items"] if i["id"] == "federal_30d"), None)
        assert federal_item is not None
        assert federal_item["amount_usd"] == 7500


class TestYamlAmountOverride:
    """The YAML's `amount_usd` should win for canonical programs even when
    the parsed AFDC amount disagrees (or is missing)."""

    def test_canonical_program_with_zero_parsed_amount_uses_yaml(self, aws: None) -> None:
        """federal_30d row with parsed amount=0 still surfaces $7,500 from
        the YAML — the curated value is authoritative for canonical programs."""
        from src.api.incentives import get_incentives

        # AFDC row mapped to federal_30d (id=409) but with amount_usd=0 —
        # simulates a body the regex couldn't extract from.
        broken_federal = {
            "id": "409",
            "title": "Section 30D",
            "type": "tax credit",
            "jurisdiction": "federal",
            "state": "US",
            "summary": "Federal credit (amount missing from parsed body)",
            "expiration_date": None,
            "last_updated": "2026-04-15",
            "amount_usd": 0,
            "categories": ["Tax Incentives"],
            "source_url": "https://afdc.energy.gov/laws/409",
        }
        _seed_afdc("afdc:US", [broken_federal])
        _put_user(
            "user-yaml-override",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-yaml-override")
        federal_item = next((i for i in result["stack"]["items"] if i["id"] == "federal_30d"), None)
        assert federal_item is not None
        # YAML override of $7,500 wins over the parsed $0.
        assert federal_item["amount_usd"] == 7500

    def test_unknown_program_falls_through_to_parsed_amount(self, aws: None) -> None:
        """An AFDC row not in afdc_id_map (unknown canonical) keeps the
        parsed amount — no YAML override to apply."""
        from src.api.incentives import get_incentives

        # An AFDC id that's NOT in the stackability YAML's afdc_id_map.
        unknown_state_program = {
            "id": "99999",
            "title": "Some MA-only EV program",
            "type": "rebate",
            "jurisdiction": "state",
            "state": "MA",
            "summary": "Rebate of $1,234 for qualifying EV purchases",
            "expiration_date": None,
            "last_updated": "2026-04-15",
            "amount_usd": 1234,
            "categories": ["Rebate"],
            "source_url": "https://afdc.energy.gov/laws/99999",
        }
        # Empty federal so the unknown row is the only thing in items.
        _seed_afdc("afdc:US", [])
        _seed_afdc("afdc:MA", [unknown_state_program])
        _put_user(
            "user-unknown",
            prefs_overrides={"search": {"location_zip": "02139", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-unknown")
        match = next(
            (i for i in result["stack"]["items"] if "99999" in i["id"]),
            None,
        )
        assert match is not None
        # Item surfaces with the parsed amount intact (canonical=None ->
        # no YAML override path).
        assert match["amount_usd"] == 1234


class TestDedupeByTitle:
    """AFDC sometimes splits one human-facing program across several rows
    that share a title (e.g. DE's BEV / PHEV / used-BEV tiers). Dedupe
    keeps the highest-amount row only."""

    def test_dedupe_keeps_higher_amount(self, aws: None) -> None:
        from src.api.incentives import get_incentives

        # Two AFDC rows with the SAME title but different parsed amounts
        # and different ids (so neither maps to a canonical YAML entry —
        # we're exercising the parsed-amount path here).
        tier_one = {
            "id": "70001",
            "title": "NY EV/PHEV Rebate",
            "type": "rebate",
            "jurisdiction": "state",
            "state": "NY",
            "summary": "Rebate of up to $1,500",
            "expiration_date": None,
            "last_updated": "2026-04-15",
            "amount_usd": 1500,
            "categories": ["Rebate"],
            "source_url": "https://afdc.energy.gov/laws/70001",
        }
        tier_two = {
            "id": "70002",
            "title": "NY EV/PHEV Rebate",
            "type": "rebate",
            "jurisdiction": "state",
            "state": "NY",
            "summary": "Rebate of up to $2,500",
            "expiration_date": None,
            "last_updated": "2026-04-15",
            "amount_usd": 2500,
            "categories": ["Rebate"],
            "source_url": "https://afdc.energy.gov/laws/70002",
        }
        _seed_afdc("afdc:US", [])
        _seed_afdc("afdc:NY", [tier_one, tier_two])
        _put_user(
            "user-ny",
            prefs_overrides={"search": {"location_zip": "10001", "fuel_types": ["Electric"]}},
        )

        result = get_incentives("user-ny")
        de_items = [i for i in result["stack"]["items"] if i.get("title") == "NY EV/PHEV Rebate"]
        # Dedupe collapses the two tier rows to one.
        assert len(de_items) == 1
        # And it's the higher amount that survived.
        assert de_items[0]["amount_usd"] == 2500
