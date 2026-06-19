"""Tests for src/memory/macro_series_store.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    monkeypatch.setenv("MACRO_SERIES_TABLE_NAME", "lookout-test-macro-series")
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
        yield


def test_get_latest_returns_newest(aws: None) -> None:
    from src.memory.macro_series_store import get_latest, write_observation

    write_observation(
        series_key="fred:TERMCBAUTO48NS",
        timestamp="2026-03-01T00:00:00+00:00",
        value=7.1,
        units="percent",
    )
    write_observation(
        series_key="fred:TERMCBAUTO48NS",
        timestamp="2026-05-01T00:00:00+00:00",
        value=7.4,
        units="percent",
    )
    write_observation(
        series_key="fred:TERMCBAUTO48NS",
        timestamp="2026-04-01T00:00:00+00:00",
        value=7.2,
        units="percent",
    )
    latest = get_latest("fred:TERMCBAUTO48NS")
    assert latest is not None
    assert latest["value"] == 7.4
    assert latest["timestamp"] == "2026-05-01T00:00:00+00:00"


def test_get_latest_returns_none_for_unknown_series(aws: None) -> None:
    from src.memory.macro_series_store import get_latest

    assert get_latest("fred:NOTHING") is None


def test_write_is_idempotent_on_same_pk_sk(aws: None) -> None:
    from src.memory.macro_series_store import get_latest, write_observation

    ts = "2026-05-01T00:00:00+00:00"
    write_observation(series_key="manheim:headline", timestamp=ts, value=205.3)
    write_observation(series_key="manheim:headline", timestamp=ts, value=207.1)
    latest = get_latest("manheim:headline")
    assert latest is not None
    assert latest["value"] == 207.1


def test_get_history_filters_by_window(aws: None) -> None:
    from src.memory.macro_series_store import get_history, write_observation

    write_observation(
        series_key="fred:CUUR0000SETA02",
        timestamp=datetime.now(UTC).isoformat(),
        value=160.0,
    )
    old_ts = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    write_observation(series_key="fred:CUUR0000SETA02", timestamp=old_ts, value=140.0)

    recent = get_history("fred:CUUR0000SETA02", days=30)
    assert len(recent) == 1
    assert recent[0]["value"] == 160.0

    all_history = get_history("fred:CUUR0000SETA02", days=500)
    assert len(all_history) == 2


def test_test_mode_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MODE", "true")
    from src.memory.macro_series_store import get_history, get_latest, write_observation

    write_observation(series_key="fred:NOTHING", timestamp="x", value=1.0)
    assert get_latest("fred:NOTHING") is None
    assert get_history("fred:NOTHING") == []
