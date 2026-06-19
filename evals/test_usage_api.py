"""Tests for `GET /me/usage` — the per-user API quota handler."""

from __future__ import annotations

from datetime import UTC, datetime
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
    monkeypatch.setenv("USERS_TABLE_NAME", "lookout-test-users")
    monkeypatch.setenv("API_USAGE_TABLE_NAME", "lookout-test-api-usage")
    monkeypatch.delenv("TEST_MODE", raising=False)
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
        TableName="lookout-test-api-usage",
        AttributeDefinitions=[
            {"AttributeName": "api_key_id", "AttributeType": "S"},
            {"AttributeName": "yyyy_mm", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "api_key_id", "KeyType": "HASH"},
            {"AttributeName": "yyyy_mm", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _current_yyyy_mm() -> str:
    now = datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


def _put_user(user_id: str, **fields: Any) -> dict[str, Any]:
    table = boto3.resource("dynamodb").Table("lookout-test-users")
    item = {"user_id": user_id, "email": f"{user_id}@example.com", **fields}
    table.put_item(Item=_to_decimal(item))
    return item


def _put_usage(api_key_id: str, calls: int, yyyy_mm: str | None = None) -> None:
    table = boto3.resource("dynamodb").Table("lookout-test-api-usage")
    table.put_item(
        Item=_to_decimal(
            {
                "api_key_id": api_key_id,
                "yyyy_mm": yyyy_mm or _current_yyyy_mm(),
                "calls": calls,
            }
        )
    )


# -------------------- default tier --------------------


class TestDefaultTier:
    def test_312_calls_returns_correct_shape(self, aws: None) -> None:
        from src.api.usage import get_usage

        _put_user("u1", tier="default")
        _put_usage("shared", calls=312)

        result = get_usage("u1")
        mc = result["marketcheck"]
        assert mc["tier"] == "default"
        assert mc["api_key_id"] == "shared"
        assert mc["calls"] == 312
        assert mc["limit"] == 500
        assert mc["pct_used"] == 62.4
        assert mc["approaching_limit"] is False
        assert mc["yyyy_mm"] == _current_yyyy_mm()

    def test_412_calls_flags_approaching_limit(self, aws: None) -> None:
        """82.4% of 500 — over the 80% banner threshold."""
        from src.api.usage import get_usage

        _put_user("u2", tier="default")
        _put_usage("shared", calls=412)

        result = get_usage("u2")
        mc = result["marketcheck"]
        assert mc["calls"] == 412
        assert mc["pct_used"] == 82.4
        assert mc["approaching_limit"] is True

    def test_zero_calls_pct_used_is_zero(self, aws: None) -> None:
        """No usage row yet → calls=0, pct_used=0.0, not null."""
        from src.api.usage import get_usage

        _put_user("u3", tier="default")
        # Deliberately no usage row written.

        result = get_usage("u3")
        mc = result["marketcheck"]
        assert mc["calls"] == 0
        assert mc["pct_used"] == 0.0
        assert mc["approaching_limit"] is False
        assert mc["limit"] == 500


# -------------------- BYOK tier --------------------


class TestByokTier:
    def test_byok_limit_is_null(self, aws: None) -> None:
        """BYOK users don't expose a known limit — frontend renders 'no cap shown'."""
        from src.api.usage import get_usage

        _put_user("u4", tier="byok")
        _put_usage("byok:u4", calls=27)

        result = get_usage("u4")
        mc = result["marketcheck"]
        assert mc["tier"] == "byok"
        assert mc["api_key_id"] == "byok:u4"
        assert mc["calls"] == 27
        assert mc["limit"] is None
        assert mc["pct_used"] is None
        assert mc["approaching_limit"] is False

    def test_byok_zero_calls(self, aws: None) -> None:
        from src.api.usage import get_usage

        _put_user("u5", tier="byok")
        result = get_usage("u5")
        mc = result["marketcheck"]
        assert mc["calls"] == 0
        assert mc["limit"] is None
        assert mc["pct_used"] is None
        assert mc["approaching_limit"] is False
        assert mc["yyyy_mm"] == _current_yyyy_mm()
