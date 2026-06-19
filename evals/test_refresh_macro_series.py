"""Tests for the refresh_macro_series Lambda entry point."""

from __future__ import annotations

from unittest.mock import patch


def test_refresh_invokes_all_three_providers() -> None:
    """One handler call → FRED, Manheim, AFDC each invoked once."""
    from src.handler import refresh_macro_series

    with (
        patch("src.tools.external.fred.refresh_all", return_value={"TERMCBAUTO48NS": 1}) as fred_m,
        patch(
            "src.tools.external.manheim_pulse.fetch_and_cache_index",
            return_value={"headline_index": 205.3},
        ) as manheim_m,
        patch(
            "src.tools.external.afdc.refresh_for_states", return_value={"CA": 1, "MA": 1}
        ) as afdc_m,
    ):
        result = refresh_macro_series({}, None)

    fred_m.assert_called_once()
    manheim_m.assert_called_once()
    afdc_m.assert_called_once()
    assert result["statusCode"] == 200
    assert result["body"]["fred"] == {"TERMCBAUTO48NS": 1}
    assert result["body"]["manheim"] == 1
    assert result["body"]["afdc"] == {"CA": 1, "MA": 1}


def test_refresh_records_manheim_failure_as_zero() -> None:
    """When the Manheim puller returns None, the summary counts it as 0."""
    from src.handler import refresh_macro_series

    with (
        patch("src.tools.external.fred.refresh_all", return_value={}),
        patch("src.tools.external.manheim_pulse.fetch_and_cache_index", return_value=None),
        patch("src.tools.external.afdc.refresh_for_states", return_value={}),
    ):
        result = refresh_macro_series({}, None)

    assert result["statusCode"] == 200
    assert result["body"]["manheim"] == 0


def test_refresh_isolates_provider_failures() -> None:
    """A bug in one provider must not take down the other two.

    Updated invariant: per the cross-source resilience design, every
    provider call is wrapped so an unexpected exception (e.g. an
    unconfigured secret crashing the AFDC client before its own
    never-raise wrapper catches it) is logged and recorded as a 0/None
    failure for that provider only — FRED and Manheim still run and
    write their caches.
    """
    from src.handler import refresh_macro_series

    with (
        patch("src.tools.external.fred.refresh_all", return_value={"TERMCBAUTO48NS": 1}),
        patch(
            "src.tools.external.manheim_pulse.fetch_and_cache_index",
            return_value={"headline_index": 205.3},
        ),
        patch(
            "src.tools.external.afdc.refresh_for_states",
            side_effect=RuntimeError("AFDC blew up"),
        ),
    ):
        result = refresh_macro_series({}, None)

    # FRED + Manheim still recorded successfully; AFDC degrades to empty.
    assert result["statusCode"] == 200
    assert result["body"]["fred"] == {"TERMCBAUTO48NS": 1}
    assert result["body"]["manheim"] == 1
    assert result["body"]["afdc"] == {}
