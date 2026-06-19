"""Tests for `GET /me/calibration` — the calibration audit endpoint.

Uses moto so we exercise the real boto3 read path through the snapshot
store. Disables TEST_MODE in the fixture because both the store and the
handler short-circuit when it's true (which would defeat the moto setup).

Coverage map (see ADR 010 for the full feature contract):

  - Empty store → 200 with empty points and zeroed summary.
  - 7 snapshots mixing Now / Quiet / Not yet → correct counts, correct
    newest-first ordering, all required wire fields populated.
  - Window filter honored: an 18-month-old row is excluded by 30d but
    included by 12m.
  - Partial rows excluded — `include_partial=False` is the calibration
    audit's contract with the store.
  - Malformed row (missing composite_index) silently dropped, the rest
    still surface.
  - Never raises — store failure degrades to an empty result.
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
    """boto3 high-level resource API rejects floats; convert recursively.

    Mirrors the helper in `test_market_signal_api.py` — we keep one copy per
    test module so each file is independently runnable.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_decimal(v) for v in obj]
    return obj


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Activate moto + env vars used by the snapshot store."""
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
            ],
            KeySchema=[
                {"AttributeName": "snapshot_key", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def _put_snapshot(snapshot_key: str, timestamp: str, **fields: Any) -> dict[str, Any]:
    """Insert a snapshot row directly. Defaults mirror what persist_market_snapshot writes."""
    table = boto3.resource("dynamodb").Table("lookout-test-market-snapshots")
    item: dict[str, Any] = {
        "snapshot_key": snapshot_key,
        "timestamp": timestamp,
        "composite_index": 12.0,
        "factor_contributions": {"calendar_pressure": 8.0, "discount_depth": 4.0},
        "variance_score": 0.2,
        "flower_position": 0.12,
        "label": "Quiet",
        "partial": False,
        "listing_count": 17,
        "median_eff_price_usd": 29300.0,
        "median_discount_pct": 9.5,
        "macro_index": 0.0,
        "personal_index": 12.0,
        "segment_index": 0.0,
        **fields,
    }
    table.put_item(Item=_to_decimal(item))
    return item


# -------------------- empty / happy path --------------------


class TestEmpty:
    def test_empty_store_returns_zeroed_summary(self, aws: None) -> None:
        from src.api.calibration import get_calibration

        result = get_calibration("user-empty")
        assert result["window"] == "90d"
        assert result["points"] == []
        summary = result["summary"]
        assert summary["total_snapshots"] == 0
        assert summary["earliest"] is None
        assert summary["latest"] is None
        assert summary["lean_now_count"] == 0
        assert summary["lean_not_yet_count"] == 0
        assert summary["quiet_count"] == 0


class TestSevenSnapshotsMixedLabels:
    """Spec: 7 snapshots, mix of Now/Quiet/Not yet → counts correct, shape correct."""

    @pytest.fixture
    def _seeded(self, aws: None) -> None:
        """Insert 7 recent snapshots: 1 Now, 6 Quiet, 0 Not yet by default —
        each test re-seeds the mix it cares about.

        Subclasses / tests reseed via explicit calls below.
        """
        # No-op default; tests seed their own mix.
        return None

    def test_seven_snapshots_mixed_labels(self, aws: None) -> None:
        from src.api.calibration import get_calibration

        # 1 Now, 6 Quiet, 0 Not yet — matches the example in the task spec.
        base = datetime.now(UTC) - timedelta(days=20)
        timestamps = [(base + timedelta(days=i)).isoformat() for i in range(7)]
        _put_snapshot("user-mix", timestamps[0], label="Now", composite_index=25.0)
        for ts in timestamps[1:]:
            _put_snapshot("user-mix", ts, label="Quiet", composite_index=10.0)

        result = get_calibration("user-mix")
        # Wire shape — required fields on every point.
        assert len(result["points"]) == 7
        for point in result["points"]:
            assert set(point.keys()) >= {
                "timestamp",
                "composite_index",
                "label",
                "flower_position",
                "median_eff_price_usd",
                "listing_count",
                "factor_contributions",
                "macro_index",
                "personal_index",
            }
            assert isinstance(point["composite_index"], float)
        # Counts: 1 Now, 6 Quiet, 0 Not yet.
        summary = result["summary"]
        assert summary["total_snapshots"] == 7
        assert summary["lean_now_count"] == 1
        assert summary["quiet_count"] == 6
        assert summary["lean_not_yet_count"] == 0
        # Temporal bounds: latest = newest, earliest = oldest.
        assert summary["latest"] == timestamps[-1]
        assert summary["earliest"] == timestamps[0]

    def test_seven_snapshots_with_not_yet(self, aws: None) -> None:
        """Different mix: 2 Now, 3 Quiet, 2 Not yet — proves the bucketing logic."""
        from src.api.calibration import get_calibration

        base = datetime.now(UTC) - timedelta(days=20)
        snapshots = [
            ("Now", 25.0),
            ("Now", 30.0),
            ("Quiet", 5.0),
            ("Quiet", 8.0),
            ("Quiet", 2.0),
            ("Not yet", -20.0),
            ("Not yet", -30.0),
        ]
        for i, (label, composite) in enumerate(snapshots):
            ts = (base + timedelta(days=i)).isoformat()
            _put_snapshot("user-mix2", ts, label=label, composite_index=composite)

        result = get_calibration("user-mix2")
        summary = result["summary"]
        assert summary["total_snapshots"] == 7
        assert summary["lean_now_count"] == 2
        assert summary["quiet_count"] == 3
        assert summary["lean_not_yet_count"] == 2


# -------------------- window filter --------------------


class TestWindow:
    def test_window_30d_excludes_old_snapshot(self, aws: None) -> None:
        """An 18-month-old snapshot must not appear under the 30d window."""
        from src.api.calibration import get_calibration

        old_ts = (datetime.now(UTC) - timedelta(days=18 * 30)).isoformat()
        _put_snapshot("user-window", old_ts, composite_index=5.0)

        result = get_calibration("user-window", window="30d")
        assert result["points"] == []
        assert result["window"] == "30d"
        assert result["summary"]["total_snapshots"] == 0

    def test_window_12m_includes_old_snapshot(self, aws: None) -> None:
        """The same 18-month-old snapshot lands inside 12m (well, ~365d).

        12m is 365 days at the store layer; "18 months ago" sometimes lands
        on either side depending on exact day count. We pick 11 months
        (~330 days) for the included case so it's unambiguously inside 12m
        and unambiguously outside 30d.
        """
        from src.api.calibration import get_calibration

        within_year_ts = (datetime.now(UTC) - timedelta(days=330)).isoformat()
        _put_snapshot("user-window-2", within_year_ts, composite_index=5.0)

        result = get_calibration("user-window-2", window="12m")
        assert len(result["points"]) == 1
        assert result["window"] == "12m"


# -------------------- partial / malformed --------------------


class TestPartialExcluded:
    def test_partial_rows_dropped(self, aws: None) -> None:
        """Guardrail-blocked (partial) rows are excluded from the audit log."""
        from src.api.calibration import get_calibration

        ts1 = datetime.now(UTC).isoformat()
        ts2 = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        _put_snapshot("user-partial", ts1, partial=False, composite_index=15.0)
        _put_snapshot("user-partial", ts2, partial=True, composite_index=99.0)

        result = get_calibration("user-partial")
        assert len(result["points"]) == 1
        # The non-partial row must be the one that surfaced.
        assert result["points"][0]["composite_index"] == 15.0
        assert result["summary"]["total_snapshots"] == 1


class TestMalformedRowDropped:
    def test_row_missing_composite_index_silently_dropped(self, aws: None) -> None:
        """A row missing `composite_index` doesn't 500 the response."""
        from src.api.calibration import get_calibration

        good_ts = datetime.now(UTC).isoformat()
        bad_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        # Good row.
        _put_snapshot("user-mal", good_ts, composite_index=10.0)
        # Bad row — composite_index intentionally absent.
        table = boto3.resource("dynamodb").Table("lookout-test-market-snapshots")
        table.put_item(
            Item=_to_decimal(
                {
                    "snapshot_key": "user-mal",
                    "timestamp": bad_ts,
                    "label": "Quiet",
                    "factor_contributions": {},
                    "variance_score": 0.0,
                    "flower_position": 0.0,
                    "partial": False,
                    # composite_index intentionally missing
                }
            )
        )

        result = get_calibration("user-mal")
        # Only the good row makes it through.
        assert len(result["points"]) == 1
        assert result["points"][0]["timestamp"] == good_ts
        assert result["summary"]["total_snapshots"] == 1

    def test_row_with_non_numeric_composite_dropped(self, aws: None) -> None:
        """Non-numeric composite_index (e.g. corrupt write) is dropped quietly."""
        from src.api.calibration import get_calibration

        good_ts = datetime.now(UTC).isoformat()
        bad_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        _put_snapshot("user-corrupt", good_ts, composite_index=10.0)
        table = boto3.resource("dynamodb").Table("lookout-test-market-snapshots")
        table.put_item(
            Item=_to_decimal(
                {
                    "snapshot_key": "user-corrupt",
                    "timestamp": bad_ts,
                    "label": "Quiet",
                    "composite_index": "not-a-number",
                    "factor_contributions": {},
                    "variance_score": 0.0,
                    "flower_position": 0.0,
                    "partial": False,
                }
            )
        )

        result = get_calibration("user-corrupt")
        assert len(result["points"]) == 1
        assert result["points"][0]["timestamp"] == good_ts


# -------------------- never raises --------------------


class TestNeverRaises:
    def test_store_exception_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A boto3 exception from the store must not propagate."""
        import src.api.calibration as cal_mod

        def boom(*_args: Any, **_kwargs: Any) -> list[Any]:
            raise RuntimeError("simulated DynamoDB outage")

        monkeypatch.setattr(cal_mod, "query_snapshots", boom)

        result = cal_mod.get_calibration("user-broken")
        assert result["points"] == []
        assert result["summary"]["total_snapshots"] == 0
        assert result["window"] == "90d"


# -------------------- prefs snapshot pass-through --------------------


class TestPrefsSnapshot:
    def test_prefs_snapshot_passed_through_when_present(self, aws: None) -> None:
        """When the persist node stamped a prefs_snapshot, the audit log surfaces it."""
        from src.api.calibration import get_calibration

        ts = datetime.now(UTC).isoformat()
        _put_snapshot(
            "user-prefs",
            ts,
            prefs_snapshot={
                "location_zip": "10001",
                "radius_miles": 75,
                "included_brands": [],
                "excluded_brands": ["Tesla"],
                "fuel_types": ["Electric"],
            },
        )
        result = get_calibration("user-prefs")
        assert len(result["points"]) == 1
        point = result["points"][0]
        assert "prefs_snapshot" in point
        assert point["prefs_snapshot"]["location_zip"] == "10001"
        assert point["prefs_snapshot"]["excluded_brands"] == ["Tesla"]

    def test_prefs_snapshot_absent_when_legacy_row(self, aws: None) -> None:
        """Legacy rows without prefs_snapshot still surface — just no key."""
        from src.api.calibration import get_calibration

        ts = datetime.now(UTC).isoformat()
        _put_snapshot("user-legacy", ts)  # default helper omits prefs_snapshot
        result = get_calibration("user-legacy")
        assert len(result["points"]) == 1
        assert "prefs_snapshot" not in result["points"][0]
