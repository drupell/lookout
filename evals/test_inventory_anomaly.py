"""Tests for the inventory-anomaly handler.

Covers `src.api.inventory_anomaly.get_inventory_anomaly`:
  - empty / under-floor history -> None (cold start, no pill)
  - normal-middle ratio -> None (boring middle, no pill)
  - thick ratio (>=1.8x) -> verdict="thick" payload
  - thin ratio (<=0.55x) -> verdict="thin" payload
  - zero-baseline guard -> None
  - malformed listing_count rows degrade gracefully
  - snapshot store raise -> None (resilience contract)

We mock `query_snapshots` directly so each scenario is one decision boundary
at a time — no DynamoDB stand-up, no flaky fixtures.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


def _snap(timestamp: str, listing_count: Any) -> dict[str, Any]:
    """Build the minimal snapshot row shape `inventory_anomaly` cares about."""
    return {
        "snapshot_key": "user-1",
        "timestamp": timestamp,
        "listing_count": listing_count,
        # The handler doesn't touch these but real rows always have them and
        # including realistic siblings catches accidental key mix-ups during
        # refactors.
        "composite_index": 0.0,
        "label": "Quiet",
        "partial": False,
    }


class TestGetInventoryAnomaly:
    def test_empty_history_returns_none(self) -> None:
        from src.api.inventory_anomaly import get_inventory_anomaly

        with patch("src.api.inventory_anomaly.query_snapshots", return_value=[]):
            assert get_inventory_anomaly("user-1") is None

    def test_under_floor_history_returns_none(self) -> None:
        """Fewer than 4 rows total = not enough for a confident baseline."""
        from src.api.inventory_anomaly import get_inventory_anomaly

        rows = [
            _snap("2026-05-25T06:00:00+00:00", 12),
            _snap("2026-05-18T06:00:00+00:00", 10),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            assert get_inventory_anomaly("user-1") is None

    def test_normal_ratio_returns_none(self) -> None:
        from src.api.inventory_anomaly import get_inventory_anomaly

        # Current 14 vs baseline median 12 → ratio 1.17, the boring middle.
        rows = [
            _snap("2026-05-25T06:00:00+00:00", 14),
            _snap("2026-05-18T06:00:00+00:00", 13),
            _snap("2026-05-11T06:00:00+00:00", 12),
            _snap("2026-05-04T06:00:00+00:00", 11),
            _snap("2026-04-27T06:00:00+00:00", 12),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            assert get_inventory_anomaly("user-1") is None

    def test_thick_ratio_returns_payload(self) -> None:
        from src.api.inventory_anomaly import get_inventory_anomaly

        # Current 42 vs baseline median 18 → ratio 2.33 → thick.
        rows = [
            _snap("2026-05-25T06:00:00+00:00", 42),
            _snap("2026-05-18T06:00:00+00:00", 19),
            _snap("2026-05-11T06:00:00+00:00", 18),
            _snap("2026-05-04T06:00:00+00:00", 17),
            _snap("2026-04-27T06:00:00+00:00", 18),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            payload = get_inventory_anomaly("user-1")

        assert payload is not None
        assert payload["verdict"] == "thick"
        assert payload["current_count"] == 42
        assert payload["baseline_count"] == 18
        # 42 / 18 ≈ 2.333 — rounded to two decimals server-side.
        assert payload["ratio"] == 2.33
        assert payload["as_of"] == "2026-05-25T06:00:00+00:00"

    def test_thin_ratio_returns_payload(self) -> None:
        from src.api.inventory_anomaly import get_inventory_anomaly

        # Current 5 vs baseline median 22 → ratio 0.23 → thin.
        rows = [
            _snap("2026-05-25T06:00:00+00:00", 5),
            _snap("2026-05-18T06:00:00+00:00", 21),
            _snap("2026-05-11T06:00:00+00:00", 22),
            _snap("2026-05-04T06:00:00+00:00", 23),
            _snap("2026-04-27T06:00:00+00:00", 22),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            payload = get_inventory_anomaly("user-1")

        assert payload is not None
        assert payload["verdict"] == "thin"
        assert payload["current_count"] == 5
        assert payload["baseline_count"] == 22
        # 5 / 22 ≈ 0.2273 — rounded to two decimals server-side.
        assert payload["ratio"] == 0.23

    def test_zero_baseline_returns_none(self) -> None:
        """Zero baseline = infinite ratio, suppress rather than overshare."""
        from src.api.inventory_anomaly import get_inventory_anomaly

        rows = [
            _snap("2026-05-25T06:00:00+00:00", 5),
            _snap("2026-05-18T06:00:00+00:00", 0),
            _snap("2026-05-11T06:00:00+00:00", 0),
            _snap("2026-05-04T06:00:00+00:00", 0),
            _snap("2026-04-27T06:00:00+00:00", 0),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            assert get_inventory_anomaly("user-1") is None

    def test_malformed_history_rows_are_dropped(self) -> None:
        """Non-numeric / negative / None listing_count values are ignored.

        Falling back to None on these rather than crashing matches the
        per-row tolerance pattern the chart endpoint uses for the rest of
        the snapshot history.
        """
        from src.api.inventory_anomaly import get_inventory_anomaly

        # Current 42, baseline among the valid history rows is median([19, 18, 17]) = 18.
        rows = [
            _snap("2026-05-25T06:00:00+00:00", 42),
            _snap("2026-05-18T06:00:00+00:00", 19),
            _snap("2026-05-11T06:00:00+00:00", None),  # dropped
            _snap("2026-05-04T06:00:00+00:00", "weird"),  # dropped
            _snap("2026-04-27T06:00:00+00:00", 18),
            _snap("2026-04-20T06:00:00+00:00", 17),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            payload = get_inventory_anomaly("user-1")

        # Three valid history rows clears `_MIN_HISTORY - 1 = 3`; payload fires.
        assert payload is not None
        assert payload["verdict"] == "thick"
        assert payload["baseline_count"] == 18

    def test_malformed_current_row_returns_none(self) -> None:
        from src.api.inventory_anomaly import get_inventory_anomaly

        rows = [
            _snap("2026-05-25T06:00:00+00:00", None),
            _snap("2026-05-18T06:00:00+00:00", 19),
            _snap("2026-05-11T06:00:00+00:00", 18),
            _snap("2026-05-04T06:00:00+00:00", 17),
            _snap("2026-04-27T06:00:00+00:00", 18),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            assert get_inventory_anomaly("user-1") is None

    def test_negative_current_row_returns_none(self) -> None:
        """A negative listing_count is malformed data — drop it."""
        from src.api.inventory_anomaly import get_inventory_anomaly

        rows = [
            _snap("2026-05-25T06:00:00+00:00", -3),
            _snap("2026-05-18T06:00:00+00:00", 19),
            _snap("2026-05-11T06:00:00+00:00", 18),
            _snap("2026-05-04T06:00:00+00:00", 17),
            _snap("2026-04-27T06:00:00+00:00", 18),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            assert get_inventory_anomaly("user-1") is None

    def test_store_raise_returns_none(self) -> None:
        from src.api.inventory_anomaly import get_inventory_anomaly

        with patch(
            "src.api.inventory_anomaly.query_snapshots",
            side_effect=RuntimeError("dynamodb timed out"),
        ):
            assert get_inventory_anomaly("user-1") is None

    def test_excludes_partial_runs(self) -> None:
        """The handler must pass `include_partial=False` to the store.

        Partial runs have half-populated `listing_count` from guardrail-
        blocked deals, which would steer the verdict in the wrong direction.
        """
        from src.api.inventory_anomaly import get_inventory_anomaly

        with patch("src.api.inventory_anomaly.query_snapshots", return_value=[]) as mock_query:
            get_inventory_anomaly("user-1")
            mock_query.assert_called_once()
            assert mock_query.call_args.kwargs["include_partial"] is False
            # Window default is 90d so the trailing baseline gets enough samples
            # to median through one bad week.
            assert mock_query.call_args.kwargs["window"] == "90d"

    def test_missing_timestamp_returns_none(self) -> None:
        """A current row without a stamp would render badly — suppress."""
        from src.api.inventory_anomaly import get_inventory_anomaly

        rows = [
            {
                "snapshot_key": "user-1",
                "listing_count": 42,
                "composite_index": 0.0,
                "label": "Quiet",
                "partial": False,
            },
            _snap("2026-05-18T06:00:00+00:00", 19),
            _snap("2026-05-11T06:00:00+00:00", 18),
            _snap("2026-05-04T06:00:00+00:00", 17),
            _snap("2026-04-27T06:00:00+00:00", 18),
        ]
        with patch("src.api.inventory_anomaly.query_snapshots", return_value=rows):
            assert get_inventory_anomaly("user-1") is None
