"""Tests for src/memory/api_usage_store.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
import pytest
from moto import mock_aws

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("API_USAGE_TABLE_NAME", "lookout-test-api-usage")
    monkeypatch.delenv("TEST_MODE", raising=False)
    with mock_aws():
        ddb = boto3.client("dynamodb")
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
        yield


def test_increment_starts_at_one_then_two(aws: None) -> None:
    from src.memory.api_usage_store import increment_calls, shared_key_id

    pk = shared_key_id()
    assert increment_calls(pk) == 1
    assert increment_calls(pk) == 2


def test_byok_and_shared_buckets_are_independent(aws: None) -> None:
    from src.memory.api_usage_store import byok_key_id, increment_calls, shared_key_id

    for _ in range(3):
        increment_calls(shared_key_id())
    for _ in range(5):
        increment_calls(byok_key_id("user-1"))

    from src.memory.api_usage_store import get_usage

    assert get_usage(shared_key_id())["calls"] == 3
    assert get_usage(byok_key_id("user-1"))["calls"] == 5


def test_get_usage_returns_zero_when_no_row(aws: None) -> None:
    from src.memory.api_usage_store import byok_key_id, get_usage

    result = get_usage(byok_key_id("never-ran"))
    assert result["calls"] == 0
    assert "yyyy_mm" in result


def test_increment_with_delta(aws: None) -> None:
    from src.memory.api_usage_store import get_usage, increment_calls, shared_key_id

    increment_calls(shared_key_id(), delta=5)
    assert get_usage(shared_key_id())["calls"] == 5


def test_test_mode_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MODE", "true")
    from src.memory.api_usage_store import get_usage, increment_calls, shared_key_id

    assert increment_calls(shared_key_id()) == 0
    assert get_usage(shared_key_id())["calls"] == 0
