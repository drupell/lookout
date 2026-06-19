"""Tests for the BYOK MarketCheck key submission API.

Covers:
  - PUT /me/byok-key validates the key, creates the secret, sets tier=byok.
  - PUT a second time updates the secret value (rotate).
  - DELETE /me/byok-key removes the secret, demotes tier, scrubs BYOK paths.
  - Validation rejection (401) returns 400 without writing anything.
  - get_me surfaces marketcheck_secret_configured as a boolean.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import boto3
import pytest
from moto import mock_aws

if TYPE_CHECKING:
    from collections.abc import Iterator


USERS_TABLE = "lookout-test-users"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Activate moto + create the Users table."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("USERS_TABLE_NAME", USERS_TABLE)
    monkeypatch.delenv("TEST_MODE", raising=False)
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName=USERS_TABLE,
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


@pytest.fixture
def good_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub MarketCheck validation as 'key works'."""
    from src.api import byok

    monkeypatch.setattr(byok, "_validate_marketcheck_key", lambda _k: True)


@pytest.fixture
def bad_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub MarketCheck validation as 'key rejected'."""
    from src.api import byok

    monkeypatch.setattr(byok, "_validate_marketcheck_key", lambda _k: False)


def _get_user(user_id: str) -> dict:
    table = boto3.resource("dynamodb").Table(USERS_TABLE)
    return table.get_item(Key={"user_id": user_id}).get("Item") or {}


def _get_secret_value(name: str) -> dict:
    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=name)
    return json.loads(resp["SecretString"])


# -------------------- PUT --------------------


class TestPutByokKey:
    def test_valid_key_creates_secret_and_upgrades_tier(self, aws: None, good_key: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.byok import put_byok_key

        upsert_user("u-1", email="u1@example.com")  # default tier
        result = put_byok_key("u-1", {"api_key": "fresh-key-123"})

        assert result == {"tier": "byok", "marketcheck_secret_configured": True}
        user = _get_user("u-1")
        assert user["tier"] == "byok"
        assert user["marketcheck_secret_arn"].startswith("arn:aws:secretsmanager:us-east-1:")
        assert _get_secret_value("lookout/byok/u-1") == {"api_key": "fresh-key-123"}

    def test_second_put_rotates_the_key(self, aws: None, good_key: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.byok import put_byok_key

        upsert_user("u-2", email="u2@example.com")
        put_byok_key("u-2", {"api_key": "old"})
        put_byok_key("u-2", {"api_key": "new"})

        assert _get_secret_value("lookout/byok/u-2") == {"api_key": "new"}

    def test_missing_api_key_returns_400(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api.byok import put_byok_key

        with pytest.raises(HttpError) as exc:
            put_byok_key("u-3", {})
        assert exc.value.status == 400

    def test_invalid_key_rejected_without_writing(self, aws: None, bad_key: None) -> None:
        from src.api._errors import HttpError
        from src.api._user_repo import upsert_user
        from src.api.byok import put_byok_key

        upsert_user("u-4", email="u4@example.com")
        with pytest.raises(HttpError) as exc:
            put_byok_key("u-4", {"api_key": "junk"})
        assert exc.value.status == 400

        user = _get_user("u-4")
        assert user["tier"] == "default"
        assert "marketcheck_secret_arn" not in user


# -------------------- DELETE --------------------


class TestDeleteByokKey:
    def test_removes_secret_and_demotes(self, aws: None, good_key: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.byok import delete_byok_key, put_byok_key

        upsert_user("u-5", email="u5@example.com")
        put_byok_key("u-5", {"api_key": "abc"})

        result = delete_byok_key("u-5")
        assert result == {"tier": "default", "marketcheck_secret_configured": False}
        user = _get_user("u-5")
        assert user["tier"] == "default"
        assert "marketcheck_secret_arn" not in user

        sm = boto3.client("secretsmanager")
        with pytest.raises(sm.exceptions.ResourceNotFoundException):
            sm.get_secret_value(SecretId="lookout/byok/u-5")

    def test_scrubs_byok_only_overrides(self, aws: None, good_key: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.byok import delete_byok_key, put_byok_key

        upsert_user(
            "u-6",
            email="u6@example.com",
            prefs_overrides={
                "schedule": {"days_of_week": [0, 3], "time_of_day_utc": "10:00"},
                "scoring": {"threshold_notify": 0.9},
                "search": {"target_listings": 200, "location_zip": "90210"},
            },
        )
        put_byok_key("u-6", {"api_key": "abc"})
        delete_byok_key("u-6")

        user = _get_user("u-6")
        # BYOK-only paths gone, default-tier paths preserved
        assert "schedule" not in user["prefs_overrides"]
        assert "scoring" not in user["prefs_overrides"]
        assert user["prefs_overrides"]["search"] == {"location_zip": "90210"}

    def test_idempotent_when_no_key(self, aws: None) -> None:
        from src.api._user_repo import upsert_user
        from src.api.byok import delete_byok_key

        upsert_user("u-7", email="u7@example.com")  # never had a key
        result = delete_byok_key("u-7")
        assert result == {"tier": "default", "marketcheck_secret_configured": False}


# -------------------- /me surfaces flag --------------------


class TestGetMeSurfacesByokFlag:
    def test_default_user_reports_not_configured(self, aws: None) -> None:
        from src.api.users import get_me

        result = get_me("u-8", email="u8@example.com")
        assert result["marketcheck_secret_configured"] is False
        assert result["tier"] == "default"

    def test_byok_user_reports_configured(self, aws: None, good_key: None) -> None:
        from src.api.byok import put_byok_key
        from src.api.users import get_me

        # Auto-provision via get_me first
        get_me("u-9", email="u9@example.com")
        put_byok_key("u-9", {"api_key": "abc"})

        result = get_me("u-9", email="u9@example.com")
        assert result["marketcheck_secret_configured"] is True
        assert result["tier"] == "byok"
        # ARN itself must NOT be in the response
        assert "marketcheck_secret_arn" not in result
