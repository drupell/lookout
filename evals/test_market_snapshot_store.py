"""Tests for src/memory/market_snapshot_store.py — uses moto for DynamoDB."""

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
    """Activate moto + create the market-snapshots table."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MARKET_SNAPSHOTS_TABLE_NAME", "lookout-test-market-snapshots")
    monkeypatch.delenv("TEST_MODE", raising=False)
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName="lookout-test-market-snapshots",
            AttributeDefinitions=[
                {"AttributeName": "snapshot_key", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
                {"AttributeName": "zip_code", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "snapshot_key", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "zip-code-index",
                    "KeySchema": [
                        {"AttributeName": "zip_code", "KeyType": "HASH"},
                        {"AttributeName": "timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


class TestWriteAndRead:
    def test_round_trip_basic_fields(self, aws: None) -> None:
        from src.memory.market_snapshot_store import query_snapshots, write_snapshot

        ts = "2026-06-13T12:00:00+00:00"
        write_snapshot(
            snapshot_key="user-1",
            timestamp=ts,
            zip_code="10001",
            listing_count=42,
            median_discount_pct=12.5,
            median_eff_price_usd=29500.0,
            factor_contributions={"calendar_pressure": 8.0, "discount_depth": 6.0},
            composite_index=14.0,
            flower_position=0.14,
            label="Quiet",
            headline_text="Conditions are quiet this week",
            variance_score=0.2,
        )
        rows = query_snapshots("user-1", window="30d")
        assert len(rows) == 1
        row = rows[0]
        assert row["snapshot_key"] == "user-1"
        assert row["timestamp"] == ts
        assert row["listing_count"] == 42
        assert row["median_discount_pct"] == 12.5
        assert row["composite_index"] == 14.0
        assert row["factor_contributions"]["calendar_pressure"] == 8.0
        assert row["label"] == "Quiet"
        assert row["zip_code"] == "10001"

    def test_idempotent_overwrite_on_same_pk_sk(self, aws: None) -> None:
        """Re-writing the same (snapshot_key, timestamp) overwrites, not appends."""
        from src.memory.market_snapshot_store import query_snapshots, write_snapshot

        ts = "2026-06-13T12:00:00+00:00"
        write_snapshot(
            snapshot_key="user-2",
            timestamp=ts,
            composite_index=10.0,
            label="Quiet",
        )
        # Second write with same ts but updated composite — should overwrite.
        write_snapshot(
            snapshot_key="user-2",
            timestamp=ts,
            composite_index=25.0,
            label="Now",
        )
        rows = query_snapshots("user-2", window="30d")
        assert len(rows) == 1
        assert rows[0]["composite_index"] == 25.0
        assert rows[0]["label"] == "Now"

    def test_window_filters_old_rows(self, aws: None) -> None:
        from src.memory.market_snapshot_store import query_snapshots, write_snapshot

        recent = datetime.now(UTC).isoformat()
        old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        write_snapshot(snapshot_key="user-3", timestamp=recent, composite_index=5.0)
        write_snapshot(snapshot_key="user-3", timestamp=old, composite_index=-5.0)

        # 30d window excludes the 200d-old row.
        rows = query_snapshots("user-3", window="30d")
        assert len(rows) == 1
        assert rows[0]["composite_index"] == 5.0

        # 12m (365d) window includes both.
        all_rows = query_snapshots("user-3", window="12m")
        assert len(all_rows) == 2

    def test_partial_filter(self, aws: None) -> None:
        from src.memory.market_snapshot_store import query_snapshots, write_snapshot

        write_snapshot(
            snapshot_key="user-4",
            timestamp="2026-06-12T12:00:00+00:00",
            composite_index=10.0,
            partial=True,
        )
        write_snapshot(
            snapshot_key="user-4",
            timestamp="2026-06-13T12:00:00+00:00",
            composite_index=15.0,
            partial=False,
        )

        # Default includes partials.
        all_rows = query_snapshots("user-4", window="30d")
        assert len(all_rows) == 2

        # Filtered excludes the partial row.
        clean_rows = query_snapshots("user-4", window="30d", include_partial=False)
        assert len(clean_rows) == 1
        assert clean_rows[0]["composite_index"] == 15.0

    def test_newest_first_ordering(self, aws: None) -> None:
        from src.memory.market_snapshot_store import query_snapshots, write_snapshot

        for i, ts in enumerate(
            [
                "2026-06-11T12:00:00+00:00",
                "2026-06-13T12:00:00+00:00",
                "2026-06-12T12:00:00+00:00",
            ]
        ):
            write_snapshot(snapshot_key="user-5", timestamp=ts, composite_index=float(i))

        rows = query_snapshots("user-5", window="30d")
        assert [r["timestamp"] for r in rows] == [
            "2026-06-13T12:00:00+00:00",
            "2026-06-12T12:00:00+00:00",
            "2026-06-11T12:00:00+00:00",
        ]

    def test_market_zip_key_pattern(self, aws: None) -> None:
        """Synthetic per-zip 'market' rows use the distinct key prefix."""
        from src.memory.market_snapshot_store import query_snapshots, write_snapshot

        write_snapshot(
            snapshot_key="market:zip:10001",
            timestamp="2026-06-13T12:00:00+00:00",
            zip_code="10001",
            composite_index=8.0,
        )
        rows = query_snapshots("market:zip:10001", window="30d")
        assert len(rows) == 1
        assert rows[0]["zip_code"] == "10001"

    def test_test_mode_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TEST_MODE=true must not touch AWS at all — used by graph evals."""
        monkeypatch.setenv("TEST_MODE", "true")
        from src.memory.market_snapshot_store import query_snapshots, write_snapshot

        # No moto setup; would error if it tried to hit AWS.
        write_snapshot(snapshot_key="anywhere", composite_index=0.0)
        assert query_snapshots("anywhere") == []
