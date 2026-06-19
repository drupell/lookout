"""Tests for `GET /me/signal` — the market-signal API handler.

Uses moto so we exercise the real boto3 paths through the snapshot store
without hitting AWS. We disable TEST_MODE in the fixture because both the
store and the handler short-circuit when it's true (which would defeat the
point of the moto setup).
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
    """Activate moto + env vars used by the snapshot store and user repo."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("USERS_TABLE_NAME", "lookout-test-users")
    monkeypatch.setenv("MARKET_SNAPSHOTS_TABLE_NAME", "lookout-test-market-snapshots")
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
        TableName="lookout-test-market-snapshots",
        AttributeDefinitions=[
            {"AttributeName": "snapshot_key", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "snapshot_key", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _put_user(user_id: str, **fields: Any) -> dict[str, Any]:
    table = boto3.resource("dynamodb").Table("lookout-test-users")
    item = {"user_id": user_id, "email": f"{user_id}@example.com", **fields}
    table.put_item(Item=_to_decimal(item))
    return item


def _put_snapshot(snapshot_key: str, timestamp: str, **fields: Any) -> dict[str, Any]:
    """Insert a snapshot row directly. Times are picked recent enough to
    fall inside the default 90d window unless a test overrides."""
    table = boto3.resource("dynamodb").Table("lookout-test-market-snapshots")
    item: dict[str, Any] = {
        "snapshot_key": snapshot_key,
        "timestamp": timestamp,
        "composite_index": 14.0,
        "factor_contributions": {"calendar_pressure": 8.0, "discount_depth": 6.0},
        "variance_score": 0.2,
        "flower_position": 0.14,
        "label": "Quiet",
        "partial": False,
        **fields,
    }
    table.put_item(Item=_to_decimal(item))
    return item


# -------------------- empty / happy path --------------------


class TestEmpty:
    def test_empty_store_returns_empty_points_and_null_latest(self, aws: None) -> None:
        from src.api.market_signal import get_market_signal

        result = get_market_signal("user-empty")
        assert result["window"] == "90d"
        assert result["view"] == "personalized"
        assert result["points"] == []
        assert result["latest"] is None


class TestPersonalized:
    def test_three_snapshots_returned_newest_first(self, aws: None) -> None:
        from src.api.market_signal import get_market_signal

        _put_snapshot("user-1", "2026-05-01T00:00:00+00:00", composite_index=10.0)
        _put_snapshot("user-1", "2026-05-15T00:00:00+00:00", composite_index=15.0)
        _put_snapshot("user-1", "2026-06-01T00:00:00+00:00", composite_index=20.0)

        result = get_market_signal("user-1")
        assert len(result["points"]) == 3
        # Newest first.
        assert result["points"][0]["timestamp"] == "2026-06-01T00:00:00+00:00"
        assert result["points"][2]["timestamp"] == "2026-05-01T00:00:00+00:00"

        # All required fields present and well-typed.
        p0 = result["points"][0]
        assert p0["composite_index"] == 20.0
        assert p0["factor_contribs"] == {"calendar_pressure": 8.0, "discount_depth": 6.0}
        assert p0["variance_score"] == 0.2
        assert p0["flower_position"] == 0.14
        assert p0["label"] == "Quiet"

        # Latest === first.
        assert result["latest"] == p0

    def test_partial_rows_excluded(self, aws: None) -> None:
        from src.api.market_signal import get_market_signal

        _put_snapshot("user-2", "2026-06-01T00:00:00+00:00", partial=False)
        _put_snapshot(
            "user-2",
            "2026-06-02T00:00:00+00:00",
            partial=True,
            composite_index=99.0,
        )

        result = get_market_signal("user-2")
        # Only the non-partial row should surface.
        assert len(result["points"]) == 1
        assert result["points"][0]["timestamp"] == "2026-06-01T00:00:00+00:00"

    def test_malformed_row_dropped_silently(self, aws: None) -> None:
        """A row missing composite_index is dropped; other rows still surface."""
        from src.api.market_signal import get_market_signal

        # Good row.
        _put_snapshot("user-3", "2026-06-01T00:00:00+00:00")
        # Bad row: composite_index removed by writing without it.
        table = boto3.resource("dynamodb").Table("lookout-test-market-snapshots")
        table.put_item(
            Item=_to_decimal(
                {
                    "snapshot_key": "user-3",
                    "timestamp": "2026-06-02T00:00:00+00:00",
                    "factor_contributions": {},
                    "variance_score": 0.0,
                    "flower_position": 0.0,
                    "label": "Quiet",
                    "partial": False,
                    # composite_index intentionally missing
                }
            )
        )

        result = get_market_signal("user-3")
        # Only the good row survives — the malformed row is dropped silently.
        assert len(result["points"]) == 1
        assert result["points"][0]["timestamp"] == "2026-06-01T00:00:00+00:00"


# -------------------- market view --------------------


class TestMarketView:
    def test_market_view_reads_from_zip_key(self, aws: None) -> None:
        """A user with a zip override gets the market:zip:<zip> series."""
        from src.api.market_signal import get_market_signal

        _put_user("user-z", prefs_overrides={"search": {"location_zip": "02139"}})
        _put_snapshot("market:zip:02139", "2026-06-01T00:00:00+00:00", composite_index=42.0)
        # Personalized row for the same user shouldn't leak into the market view.
        _put_snapshot("user-z", "2026-06-01T00:00:00+00:00", composite_index=-30.0)

        result = get_market_signal("user-z", view="market")
        assert result["view"] == "market"
        assert len(result["points"]) == 1
        assert result["points"][0]["composite_index"] == 42.0

    def test_market_view_no_zip_raises_400(self, aws: None) -> None:
        """Defensive: an absent zip on the user row + no merged-prefs fallback
        triggers a 400 rather than a silent empty series."""
        # No user row, and we patch the loader fallback to return no zip too.
        # The handler resolves prefs via load_preferences — but in this test
        # environment there's no Cognito context, so the loader just returns
        # the bundled defaults. We force the failure path by patching it.
        import src.api.market_signal as mod
        from src.api._errors import HttpError
        from src.api.market_signal import get_market_signal

        original = mod._resolve_user_zip
        try:
            mod._resolve_user_zip = lambda _uid: None  # type: ignore[assignment]
            with pytest.raises(HttpError) as exc:
                get_market_signal("user-no-zip", view="market")
            assert exc.value.status == 400
            assert "zip" in exc.value.message.lower()
        finally:
            mod._resolve_user_zip = original  # type: ignore[assignment]


# -------------------- window handling --------------------


class TestWindow:
    def test_window_24m_honored(self, aws: None) -> None:
        """A row from ~18 months ago is excluded by 90d but included by 24m."""
        from src.api.market_signal import get_market_signal

        # ~18 months ago — well inside 24m, well outside 90d.
        _put_snapshot("user-w", "2024-12-01T00:00:00+00:00", composite_index=5.0)

        short = get_market_signal("user-w", window="90d")
        assert short["points"] == []

        long_ = get_market_signal("user-w", window="24m")
        assert len(long_["points"]) == 1
        assert long_["window"] == "24m"

    def test_unknown_window_falls_back_to_90d_not_500(self, aws: None) -> None:
        """Unknown window strings shouldn't 500 — store falls back to 90d."""
        from src.api.market_signal import get_market_signal

        _put_snapshot("user-uw", "2026-06-01T00:00:00+00:00")

        # Should not raise; window string is preserved in the response per
        # the contract (the store made the fallback decision, not us).
        result = get_market_signal("user-uw", window="bogus")
        assert result["window"] == "bogus"
        assert len(result["points"]) == 1
