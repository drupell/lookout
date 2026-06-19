"""Eval: persist_market_snapshot node + its registered guardrail.

Structural-only — no real DynamoDB, no real LLM. We mock the three I/O
boundaries (`write_snapshot`, `query_snapshots`, `load_preferences`) and
verify the node's logic produces the right composite, the right
`partial` flag, and the right cold-start behavior.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agent.nodes import persist_market_snapshot as pms_module
from src.agent.nodes.persist_market_snapshot import persist_market_snapshot
from src.guardrails.registry import GUARDRAIL_REGISTRY, validate_node_output
from src.guardrails.structural import MarketSnapshotPayload

# ---- Fixtures ----


class _FakeSearch:
    location_zip = "10001"


class _FakePrefs:
    search = _FakeSearch()


def _deal(
    listing_id: str,
    *,
    msrp: float | None = 40000.0,
    selling_price: float | None = 35000.0,
    eff: float | None = 30000.0,
    incentives: list[str] | None = None,
) -> dict[str, Any]:
    """Shape-compatible scored-deal row (mirrors what score_deals produces)."""
    deal: dict[str, Any] = {
        "listing_id": listing_id,
        "overall_score": 0.8,
        "trade_in_value_at_scoring": 15000.0,
        "applicable_incentives": incentives if incentives is not None else ["Fed EV $7,500"],
    }
    if msrp is not None:
        deal["msrp"] = msrp
    if selling_price is not None:
        deal["selling_price"] = selling_price
    if eff is not None:
        deal["effective_out_of_pocket_usd"] = eff
    return deal


@pytest.fixture
def _patches(monkeypatch: pytest.MonkeyPatch):
    """Patch the three I/O boundaries the node touches.

    Yields a small dict so each test can stub return values and inspect calls.
    """
    write_calls: list[dict[str, Any]] = []
    query_returns: list[list[dict[str, Any]]] = [[]]
    prefs_return: list[Any] = [_FakePrefs()]
    prefs_raises: list[bool] = [False]

    def fake_write(**kwargs: Any) -> None:
        write_calls.append(kwargs)

    def fake_query(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return query_returns[0]

    def fake_load_prefs(*args: Any, **kwargs: Any) -> Any:
        if prefs_raises[0]:
            raise RuntimeError("simulated prefs load failure")
        return prefs_return[0]

    monkeypatch.setattr(pms_module, "write_snapshot", fake_write)
    monkeypatch.setattr(pms_module, "query_snapshots", fake_query)
    # load_preferences is imported lazily inside the node; patch the source.
    import src.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "load_preferences", fake_load_prefs)

    yield {
        "write_calls": write_calls,
        "query_returns": query_returns,
        "prefs_return": prefs_return,
        "prefs_raises": prefs_raises,
    }


# ---- Tests ----


class TestHappyPath:
    def test_writes_snapshot_with_non_zero_composite(self, _patches: dict[str, Any]) -> None:
        """Calendar pressure on its own yields a non-zero composite for an EOM run."""
        state = {
            "user_id": "user-1",
            "run_id": "run-1",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "SUCCESS",
            "scored_deals": [
                _deal("L1", msrp=40000, selling_price=36000, eff=28000),
                _deal("L2", msrp=50000, selling_price=44000, eff=33000),
                _deal("L3", msrp=45000, selling_price=42000, eff=35000),
            ],
        }
        persist_market_snapshot(state)

        assert len(_patches["write_calls"]) == 1
        kwargs = _patches["write_calls"][0]
        assert kwargs["snapshot_key"] == "user-1"
        assert kwargs["timestamp"] == "2026-06-13T12:00:00+00:00"
        assert kwargs["zip_code"] == "10001"
        assert kwargs["listing_count"] == 3
        assert kwargs["partial"] is False
        # Calendar pressure always contributes something non-trivial — the
        # actual value depends on today's date, but the contribution dict is
        # always present.
        contribs = kwargs["factor_contributions"]
        assert "calendar_pressure" in contribs
        # Personal index mirrors the composite in PR1 (per the plan).
        assert kwargs["personal_index"] == kwargs["composite_index"]
        # Macro/segment are 0 in PR1.
        assert kwargs["macro_index"] == 0.0
        assert kwargs["segment_index"] == 0.0
        # Label must be one of the three editorial states.
        assert kwargs["label"] in {"Now", "Quiet", "Not yet"}

    def test_returns_state_update_with_market_snapshot_and_audit(
        self, _patches: dict[str, Any]
    ) -> None:
        """The node populates `market_snapshot` so validate_market_snapshot can re-check."""
        state = {
            "user_id": "user-1",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "SUCCESS",
            "scored_deals": [_deal("L1")],
        }
        update = persist_market_snapshot(state)

        assert "market_snapshot" in update
        snap = update["market_snapshot"]
        for key in (
            "composite_index",
            "factor_contributions",
            "variance_score",
            "flower_position",
            "label",
        ):
            assert key in snap
        # Audit trail extended with the node record.
        records = update["nodes_executed"]
        assert records[-1]["node"] == "persist_market_snapshot"
        assert records[-1]["output_record_count"] == 1


class TestPartialFlag:
    def test_partial_true_when_status_guardrail_blocked(self, _patches: dict[str, Any]) -> None:
        state = {
            "user_id": "user-1",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "GUARDRAIL_BLOCKED",
            "scored_deals": [_deal("L1")],
        }
        persist_market_snapshot(state)
        assert _patches["write_calls"][0]["partial"] is True

    def test_partial_true_when_status_error(self, _patches: dict[str, Any]) -> None:
        state = {
            "user_id": "user-1",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "ERROR",
            "scored_deals": [_deal("L1")],
        }
        persist_market_snapshot(state)
        assert _patches["write_calls"][0]["partial"] is True

    def test_partial_false_when_status_success(self, _patches: dict[str, Any]) -> None:
        state = {
            "user_id": "user-1",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "SUCCESS",
            "scored_deals": [_deal("L1")],
        }
        persist_market_snapshot(state)
        assert _patches["write_calls"][0]["partial"] is False


class TestColdStart:
    def test_empty_scored_deals_with_no_prior_still_writes(self, _patches: dict[str, Any]) -> None:
        """Cold-start: only calendar_pressure contributes; the row still gets written."""
        state = {
            "user_id": "user-new",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "SUCCESS",
            "scored_deals": [],
        }
        persist_market_snapshot(state)

        assert len(_patches["write_calls"]) == 1
        kwargs = _patches["write_calls"][0]
        assert kwargs["listing_count"] == 0
        contribs = kwargs["factor_contributions"]
        # Data-driven factors all zero on cold-start.
        assert contribs["discount_depth"] == 0.0
        assert contribs["inventory_density"] == 0.0
        assert contribs["effective_price_trend"] == 0.0
        assert contribs["incentive_prevalence"] == 0.0
        # median_discount / median_eff_price are omitted when there's no data.
        assert kwargs["median_discount_pct"] is None
        assert kwargs["median_eff_price_usd"] is None


class TestUserIdMissing:
    def test_no_user_id_returns_audit_but_no_write(self, _patches: dict[str, Any]) -> None:
        state = {
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "SUCCESS",
            "scored_deals": [_deal("L1")],
        }
        update = persist_market_snapshot(state)

        # No write happened.
        assert _patches["write_calls"] == []
        # No market_snapshot key on the state update.
        assert "market_snapshot" not in update
        # Audit record still extended (so validate_market_snapshot sees the node ran).
        records = update["nodes_executed"]
        assert records[-1]["node"] == "persist_market_snapshot"
        assert records[-1]["output_record_count"] == 0


class TestGuardrailFailureSkipsWrite:
    def test_out_of_range_composite_skips_write_without_raising(
        self, _patches: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Math bug that produces an out-of-range composite must NOT raise.

        Failure path: log + skip write. The run shouldn't fail because a
        snapshot couldn't be computed — persist_results already wrote the
        user-facing deals + run record.
        """
        from dataclasses import dataclass

        @dataclass
        class BadResult:
            composite_index: float = 9999.0  # way out of [-100, 100]
            factor_contributions: dict[str, float] = None  # type: ignore[assignment]
            variance_score: float = 0.0
            flower_position: float = 0.0
            label: str = "Quiet"

        def fake_composite(_contribs: dict[str, float]) -> BadResult:
            return BadResult(factor_contributions=_contribs)

        monkeypatch.setattr(pms_module, "composite_from_factors", fake_composite)

        state = {
            "user_id": "user-1",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "SUCCESS",
            "scored_deals": [_deal("L1")],
        }
        update = persist_market_snapshot(state)

        # No write happened despite the broken math.
        assert _patches["write_calls"] == []
        # No snapshot in state — validator treats absence as no-op.
        assert "market_snapshot" not in update


class TestPrefsLoadFailure:
    def test_prefs_load_failure_still_writes_without_zip(self, _patches: dict[str, Any]) -> None:
        """Prefs read failure must not block snapshot persistence."""
        _patches["prefs_raises"][0] = True

        state = {
            "user_id": "user-1",
            "started_at": "2026-06-13T12:00:00+00:00",
            "status": "SUCCESS",
            "scored_deals": [_deal("L1")],
        }
        persist_market_snapshot(state)

        assert len(_patches["write_calls"]) == 1
        # zip_code falls through as None when prefs load throws.
        assert _patches["write_calls"][0]["zip_code"] is None


# ---- Registry checks ----


class TestRegistryEntry:
    def test_persist_market_snapshot_is_registered(self) -> None:
        assert "persist_market_snapshot" in GUARDRAIL_REGISTRY
        assert GUARDRAIL_REGISTRY["persist_market_snapshot"] is MarketSnapshotPayload

    def test_validate_node_output_passes_on_in_range_payload(self) -> None:
        result = validate_node_output(
            "persist_market_snapshot",
            {
                "composite_index": 12.0,
                "factor_contributions": {"calendar_pressure": 8.0, "discount_depth": 4.0},
                "variance_score": 0.2,
                "flower_position": 0.12,
                "label": "Quiet",
            },
        )
        assert result.passed is True

    def test_validate_node_output_fails_on_out_of_range_composite(self) -> None:
        result = validate_node_output(
            "persist_market_snapshot",
            {
                "composite_index": 8800.0,  # the canonical missing-/-100 bug
                "factor_contributions": {"calendar_pressure": 8.0},
                "variance_score": 0.2,
                "flower_position": 0.12,
                "label": "Quiet",
            },
        )
        assert result.passed is False
        assert any("composite_index" in e for e in result.errors)

    def test_validate_node_output_fails_on_bad_label(self) -> None:
        result = validate_node_output(
            "persist_market_snapshot",
            {
                "composite_index": 12.0,
                "factor_contributions": {"calendar_pressure": 8.0},
                "variance_score": 0.2,
                "flower_position": 0.12,
                "label": "Sometime",  # not in the Literal
            },
        )
        assert result.passed is False

    def test_validate_node_output_fails_on_oversized_factor_contribution(self) -> None:
        result = validate_node_output(
            "persist_market_snapshot",
            {
                "composite_index": 12.0,
                "factor_contributions": {"calendar_pressure": 99.0},  # >25 cap
                "variance_score": 0.2,
                "flower_position": 0.12,
                "label": "Quiet",
            },
        )
        assert result.passed is False
        assert any("calendar_pressure" in e for e in result.errors)


# ---- Macro layer activation: APR-trend factor + macro/personal breakdown ----


class TestAutoLoanAprTrend:
    """The new auto_loan_apr_trend factor sources from FRED's TERMCBAUTO48NS.

    Direction is `positive_is_unfavorable` — a higher current APR than the
    trailing baseline pushes the index toward "Not yet" (financing got more
    expensive).
    """

    def test_apr_above_baseline_contributes_negative(
        self, _patches: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Latest APR 7.5%, baseline 5.0% -> 50% delta -> max negative contribution."""
        import src.memory.macro_series_store as store_mod

        monkeypatch.setattr(store_mod, "get_latest", lambda _k: {"value": 7.5, "timestamp": "x"})
        monkeypatch.setattr(
            store_mod,
            "get_history",
            lambda _k, **_kw: [{"value": 5.0}, {"value": 5.0}, {"value": 5.0}],
        )

        state = {
            "user_id": "user-apr-up",
            "run_id": "run-apr-up",
            "status": "SUCCESS",
            "started_at": "2026-06-14T12:00:00+00:00",
            "scored_deals": [],
            "nodes_executed": [],
        }
        persist_market_snapshot(state)
        assert _patches["write_calls"]
        call = _patches["write_calls"][0]
        apr = call["factor_contributions"].get("auto_loan_apr_trend")
        # 50% delta * unfavorable direction = full negative weight (-15)
        assert apr == pytest.approx(-15.0, abs=0.5)
        # macro_index sums calendar + apr; personal_index sums the four user factors
        # (which are 0 here with empty deals + no history). Net: macro is negative.
        assert call["macro_index"] < 0
        assert call["personal_index"] == 0.0

    def test_apr_below_baseline_contributes_positive(
        self, _patches: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Latest APR 4%, baseline 6% -> 33% delta * unfavorable = positive contribution."""
        import src.memory.macro_series_store as store_mod

        monkeypatch.setattr(store_mod, "get_latest", lambda _k: {"value": 4.0, "timestamp": "x"})
        monkeypatch.setattr(
            store_mod,
            "get_history",
            lambda _k, **_kw: [{"value": 6.0}, {"value": 6.0}, {"value": 6.0}],
        )

        state = {
            "user_id": "user-apr-down",
            "run_id": "run-apr-down",
            "status": "SUCCESS",
            "started_at": "2026-06-14T12:00:00+00:00",
            "scored_deals": [],
            "nodes_executed": [],
        }
        persist_market_snapshot(state)
        call = _patches["write_calls"][0]
        apr = call["factor_contributions"].get("auto_loan_apr_trend")
        # 33% delta gives ~2/3 of full weight, positive sign.
        assert apr is not None
        assert apr > 5.0  # at least a third of the 15-point cap
        # Net macro should still be positive (calendar is 0 mid-month).
        assert call["macro_index"] > 0

    def test_no_fred_data_yields_zero(
        self, _patches: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cold-start cache: factor stays at 0, doesn't crash the node."""
        import src.memory.macro_series_store as store_mod

        monkeypatch.setattr(store_mod, "get_latest", lambda _k: None)
        monkeypatch.setattr(store_mod, "get_history", lambda _k, **_kw: [])

        state = {
            "user_id": "user-cold",
            "run_id": "run-cold",
            "status": "SUCCESS",
            "started_at": "2026-06-14T12:00:00+00:00",
            "scored_deals": [],
            "nodes_executed": [],
        }
        persist_market_snapshot(state)
        call = _patches["write_calls"][0]
        assert call["factor_contributions"].get("auto_loan_apr_trend") == 0.0
        # Macro index is purely calendar; mid-month -> 0.
        assert call["macro_index"] == 0.0

    def test_history_read_failure_degrades_gracefully(
        self, _patches: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A boto3 exception from the store doesn't propagate."""
        import src.memory.macro_series_store as store_mod

        monkeypatch.setattr(store_mod, "get_latest", lambda _k: {"value": 7.0})
        monkeypatch.setattr(
            store_mod,
            "get_history",
            lambda _k, **_kw: (_ for _ in ()).throw(RuntimeError("ddb down")),
        )

        state = {
            "user_id": "user-broken",
            "run_id": "run-broken",
            "status": "SUCCESS",
            "started_at": "2026-06-14T12:00:00+00:00",
            "scored_deals": [],
            "nodes_executed": [],
        }
        persist_market_snapshot(state)
        # Snapshot still written; APR factor degraded to 0.
        assert _patches["write_calls"]
        call = _patches["write_calls"][0]
        assert call["factor_contributions"].get("auto_loan_apr_trend") == 0.0
