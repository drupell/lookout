"""Tests for the multi-tenant API Lambda handlers.

Uses moto to mock DynamoDB so the tests don't need real AWS access. Each test
creates fresh tables to keep state isolation simple.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from moto import mock_aws

if TYPE_CHECKING:
    from collections.abc import Iterator


def _to_decimal(obj: Any) -> Any:
    """boto3 high-level resource API rejects floats; convert recursively."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_decimal(v) for v in obj]
    return obj


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Activate moto + set the env vars our handlers expect."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("USERS_TABLE_NAME", "lookout-test-users")
    monkeypatch.setenv("RUNS_TABLE_NAME", "lookout-test-runs")
    monkeypatch.setenv("DEALS_TABLE_NAME", "lookout-test-deals")
    monkeypatch.setenv("FAVORITES_TABLE_NAME", "lookout-test-favorites")
    monkeypatch.setenv("CONFIG_TABLE_NAME", "lookout-test-config")
    monkeypatch.delenv("TEST_MODE", raising=False)  # we want real DynamoDB calls
    with mock_aws():
        _create_tables()
        yield


def _create_tables() -> None:
    ddb = boto3.client("dynamodb")
    ddb.create_table(
        TableName="lookout-test-users",
        AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="lookout-test-runs",
        AttributeDefinitions=[
            {"AttributeName": "run_id", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "run_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "user-id-index",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="lookout-test-deals",
        AttributeDefinitions=[
            {"AttributeName": "listing_id", "AttributeType": "S"},
            {"AttributeName": "first_seen", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "listing_id", "KeyType": "HASH"},
            {"AttributeName": "first_seen", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "user-id-index",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "first_seen", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="lookout-test-favorites",
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "listing_id", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "listing_id", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _put_user(user_id: str, **fields: Any) -> dict[str, Any]:
    table = boto3.resource("dynamodb").Table("lookout-test-users")
    item = {"user_id": user_id, "email": f"{user_id}@example.com", **fields}
    table.put_item(Item=_to_decimal(item))
    return item


def _put_deal(user_id: str, listing_id: str, **fields: Any) -> dict[str, Any]:
    table = boto3.resource("dynamodb").Table("lookout-test-deals")
    item = _to_decimal(
        {
            "listing_id": listing_id,
            "first_seen": "2026-04-28T00:00:00",
            "user_id": user_id,
            "status": "NEW",
            "make": "Tesla",
            "model": "Model 3",
            "year": 2024,
            "selling_price": 30000,
            "overall_score": 0.5,
            **fields,
        }
    )
    table.put_item(Item=item)
    return item


def _put_run(user_id: str, run_id: str, **fields: Any) -> dict[str, Any]:
    table = boto3.resource("dynamodb").Table("lookout-test-runs")
    item = {
        "run_id": run_id,
        "timestamp": "2026-04-28T00:00:00",
        "user_id": user_id,
        "status": "SUCCESS",
        "deals_found": 0,
        **fields,
    }
    table.put_item(Item=item)
    return item


# -------------------- /me --------------------


class TestGetMe:
    def test_auto_provisions_default_tier_user(self, aws: None) -> None:
        from src.api.users import get_me

        result = get_me("user-1", "alice@example.com")
        assert result["user_id"] == "user-1"
        assert result["tier"] == "default"
        assert result["email"] == "alice@example.com"
        assert result["tier_caps"]["pagination_editable"] is False

    def test_returns_existing_user(self, aws: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.users import get_me

        upsert_user("user-2", email="bob@example.com", tier="byok")
        result = get_me("user-2", "ignored@example.com")
        assert result["tier"] == "byok"
        assert result["email"] == "bob@example.com"
        assert result["tier_caps"]["pagination_editable"] is True

    def test_provisions_and_persists_next_run_at(self, aws: None) -> None:
        from datetime import datetime

        from src.api.users import get_me

        result = get_me("user-nr", "nr@example.com")
        assert result["next_run_at"], "next_run_at should be set on first /me"
        # Default schedule anchors to Thursday (weekday 3).
        assert datetime.fromisoformat(result["next_run_at"]).weekday() == 3
        # Persisted: a second call returns the same stored value.
        again = get_me("user-nr", "nr@example.com")
        assert again["next_run_at"] == result["next_run_at"]


# -------------------- /me/prefs --------------------


class TestPutPrefs:
    def test_default_tier_can_edit_zip(self, aws: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        upsert_user("user-3", email="x@y.com", tier="default")
        result = put_prefs("user-3", {"search": {"location_zip": "10001"}})
        assert result["effective"]["search"]["location_zip"] == "10001"
        assert result["user_overrides"]["search"]["location_zip"] == "10001"

    def test_default_tier_cannot_edit_max_pages(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        upsert_user("user-4", email="x@y.com", tier="default")
        with pytest.raises(HttpError) as exc:
            put_prefs("user-4", {"search": {"max_pages": 50}})
        assert exc.value.status == 403
        assert "search.max_pages" in exc.value.details["rejected_paths"]

    def test_byok_tier_can_edit_max_pages(self, aws: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        upsert_user("user-5", email="x@y.com", tier="byok")
        result = put_prefs("user-5", {"search": {"max_pages": 30}})
        assert result["effective"]["search"]["max_pages"] == 30

    def test_invalid_value_rejected(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        upsert_user("user-6", email="x@y.com", tier="byok")
        # radius_miles requires gt=0 — zero is rejected. (Out-of-bounds-high
        # values are silently clamped to 100 by the validator now, not rejected;
        # see test_radius_clamps_to_marketcheck_max below.)
        with pytest.raises(HttpError) as exc:
            put_prefs("user-6", {"search": {"radius_miles": 0}})
        assert exc.value.status == 400

    def test_radius_clamps_to_marketcheck_max(self, aws: None) -> None:
        """radius_miles > 100 is clamped to MarketCheck's free-tier max, not rejected."""
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        upsert_user("user-6b", email="x@y.com", tier="byok")
        result = put_prefs("user-6b", {"search": {"radius_miles": 250}})
        assert result["effective"]["search"]["radius_miles"] == 100

    def test_put_prefs_income_tier(self, aws: None) -> None:
        """income_tier is a personal field — writable on both default and BYOK
        tiers, and rejected with 400 when the value isn't one of the enum members."""
        from src.api._errors import HttpError
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        # Default-tier user can write income_tier.
        upsert_user("user-it-default", email="x@y.com", tier="default")
        result = put_prefs("user-it-default", {"income_tier": "under_150k"})
        assert result["effective"]["income_tier"] == "under_150k"
        assert result["user_overrides"]["income_tier"] == "under_150k"

        # BYOK user can also write income_tier.
        upsert_user("user-it-byok", email="x@y.com", tier="byok")
        result = put_prefs("user-it-byok", {"income_tier": "above_300k"})
        assert result["effective"]["income_tier"] == "above_300k"

        # Invalid enum value returns 400 (Pydantic validation failure).
        with pytest.raises(HttpError) as exc:
            put_prefs("user-it-byok", {"income_tier": "rich"})
        assert exc.value.status == 400


# -------------------- /me/runs --------------------


class TestListRuns:
    def test_returns_only_my_runs(self, aws: None) -> None:
        from src.api.runs import list_runs

        _put_run("user-7", "run-a")
        _put_run("user-8", "run-b")
        result = list_runs("user-7")
        assert len(result) == 1
        assert result[0]["run_id"] == "run-a"

    def test_strips_config_snapshot(self, aws: None) -> None:
        from src.api.runs import list_runs

        _put_run("user-9", "run-c", config_snapshot={"big": "object"})
        result = list_runs("user-9")
        assert "config_snapshot" not in result[0]


# -------------------- /me/deals --------------------


class TestListDeals:
    def test_filters_by_user(self, aws: None) -> None:
        from src.api.deals import list_deals

        _put_user("user-10", last_run_id="run-x")
        _put_deal("user-10", "lst-1", run_id="run-x")
        _put_deal("user-11", "lst-2", run_id="run-x")
        result = list_deals("user-10")
        assert len(result) == 1
        assert result[0]["listing_id"] == "lst-1"

    def test_min_score_filter(self, aws: None) -> None:
        from src.api.deals import list_deals

        _put_user("user-12", last_run_id="run-y")
        _put_deal("user-12", "lst-low", overall_score=0.3, run_id="run-y")
        _put_deal("user-12", "lst-high", overall_score=0.8, run_id="run-y")
        result = list_deals("user-12", query={"min_score": "0.5"})
        assert len(result) == 1
        assert result[0]["listing_id"] == "lst-high"


class TestListDealsSnapshot:
    def test_scoped_to_last_run_id(self, aws: None) -> None:
        """Only the latest run's rows surface; old runs are retained but hidden."""
        from src.api.deals import list_deals

        _put_user("user-snap", last_run_id="run-2")
        _put_deal("user-snap", "lst-old", run_id="run-1")
        _put_deal("user-snap", "lst-cur", run_id="run-2")
        result = list_deals("user-snap")
        assert [d["listing_id"] for d in result] == ["lst-cur"]

    def test_no_last_run_id_returns_empty(self, aws: None) -> None:
        """No completed run yet → empty, never a fallback to 'show everything'."""
        from src.api.deals import list_deals

        _put_deal("user-norun", "lst-1", run_id="run-1")  # no user row
        assert list_deals("user-norun") == []

    def test_sets_is_favorite(self, aws: None) -> None:
        from src.api.deals import list_deals
        from src.api.favorites import add_favorite

        _put_user("user-fav", last_run_id="r")
        _put_deal("user-fav", "lst-1", run_id="r")
        _put_deal("user-fav", "lst-2", run_id="r")
        add_favorite("user-fav", "lst-1")
        by_id = {d["listing_id"]: d for d in list_deals("user-fav")}
        assert by_id["lst-1"]["is_favorite"] is True
        assert by_id["lst-2"]["is_favorite"] is False


class TestFavorites:
    def test_add_then_list(self, aws: None) -> None:
        from src.api.favorites import add_favorite, list_favorites

        _put_deal("user-f1", "lst-1")
        resp = add_favorite("user-f1", "lst-1")
        assert resp == {"listing_id": "lst-1", "signal": "FAVORITE"}
        favs = list_favorites("user-f1")
        assert len(favs) == 1
        assert favs[0]["signal"] == "FAVORITE"
        assert favs[0]["deal"]["listing_id"] == "lst-1"

    def test_add_is_idempotent(self, aws: None) -> None:
        from src.api.favorites import add_favorite, list_favorites

        _put_deal("user-f2", "lst-1")
        add_favorite("user-f2", "lst-1")
        add_favorite("user-f2", "lst-1")
        assert len(list_favorites("user-f2")) == 1

    def test_add_404_for_missing_deal(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api.favorites import add_favorite

        with pytest.raises(HttpError) as exc:
            add_favorite("user-f3", "nope")
        assert exc.value.status == 404

    def test_add_403_for_other_users_deal(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api.favorites import add_favorite

        _put_deal("owner", "lst-x")
        with pytest.raises(HttpError) as exc:
            add_favorite("intruder", "lst-x")
        assert exc.value.status == 403

    def test_remove_is_idempotent(self, aws: None) -> None:
        from src.api.favorites import add_favorite, list_favorites, remove_favorite

        # Removing a non-existent favorite is a no-op success.
        assert remove_favorite("user-f4", "lst-1") == {
            "listing_id": "lst-1",
            "removed": True,
        }
        _put_deal("user-f4", "lst-1")
        add_favorite("user-f4", "lst-1")
        remove_favorite("user-f4", "lst-1")
        assert list_favorites("user-f4") == []


class TestActOnDeal:
    def test_marks_acted(self, aws: None) -> None:
        from src.api.deals import act_on_deal

        _put_deal("user-13", "lst-act-1")
        result = act_on_deal("user-13", "lst-act-1")
        assert result["status"] == "ACTED"

    def test_rejects_other_users_deal(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api.deals import act_on_deal

        _put_deal("user-14", "lst-private")
        with pytest.raises(HttpError) as exc:
            act_on_deal("user-other", "lst-private")
        assert exc.value.status == 403

    def test_404_for_missing_deal(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api.deals import act_on_deal

        with pytest.raises(HttpError) as exc:
            act_on_deal("user-15", "lst-nonexistent")
        assert exc.value.status == 404


class TestKnownListingIds:
    """get_known_listing_ids must scope per user so one user's history never
    suppresses another user's 'new deal' / email-drafting detection."""

    def test_scoped_per_user(self, aws: None) -> None:
        from src.memory.deal_store import get_known_listing_ids

        _put_deal("user-A", "lst-a")
        _put_deal("user-B", "lst-b")

        assert get_known_listing_ids("user-A") == {"lst-a"}
        assert get_known_listing_ids("user-B") == {"lst-b"}

    def test_none_scans_all_legacy(self, aws: None) -> None:
        from src.memory.deal_store import get_known_listing_ids

        _put_deal("user-A", "lst-a")
        _put_deal("user-B", "lst-b")

        assert get_known_listing_ids(None) == {"lst-a", "lst-b"}
