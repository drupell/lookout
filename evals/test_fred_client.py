"""Tests for src/tools/external/fred.py.

Mocks httpx + the macro-series store so no real network or DynamoDB is hit.
The autouse `set_test_mode` fixture in conftest pins TEST_MODE=true; tests
that exercise the real HTTP path explicitly unset it via monkeypatch.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable TEST_MODE so the client actually exercises the HTTP path."""
    monkeypatch.delenv("TEST_MODE", raising=False)
    monkeypatch.setenv("FRED_API_KEY", "fake-key")


def _mock_response(*, status_code: int = 200, payload: dict | None = None) -> httpx.Response:
    content = json.dumps(payload or {}).encode()
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "https://api.stlouisfed.org/x"),
        content=content,
    )


class TestFetchAndCacheSeries:
    def test_happy_path_persists_full_history_returns_latest(self, live_env: None) -> None:
        """Every usable observation gets written so the consumer (persist node)
        can compute a trailing baseline. The return value reflects the newest
        observation, which is what the /me/macro endpoint surfaces."""
        from src.tools.external import fred

        payload = {
            "observations": [
                {"date": "2026-05-01", "value": "7.42"},
                {"date": "2026-04-01", "value": "7.30"},
                {"date": "2026-03-01", "value": "7.10"},
            ]
        }
        with (
            patch("httpx.Client.get", return_value=_mock_response(payload=payload)),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        # Caller sees the newest observation (parity with the macro_snapshot
        # handler's "latest"-style read).
        assert result == {
            "series_key": "fred:TERMCBAUTO48NS",
            "timestamp": "2026-05-01T00:00:00+00:00",
            "value": 7.42,
        }
        # All three observations land in the cache so the persist node has a
        # baseline > 1 to compute against.
        assert write_mock.call_count == 3
        written_timestamps = [c.kwargs["timestamp"] for c in write_mock.call_args_list]
        assert written_timestamps == [
            "2026-05-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
        ]
        written_values = [c.kwargs["value"] for c in write_mock.call_args_list]
        assert written_values == [7.42, 7.30, 7.10]

    def test_skips_missing_value_observations(self, live_env: None) -> None:
        """FRED encodes missing data as ".", we must skip those and take the next real value."""
        from src.tools.external import fred

        payload = {
            "observations": [
                {"date": "2026-05-01", "value": "."},
                {"date": "2026-04-01", "value": "7.30"},
            ]
        }
        with (
            patch("httpx.Client.get", return_value=_mock_response(payload=payload)),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        assert result is not None
        assert result["timestamp"] == "2026-04-01T00:00:00+00:00"
        assert result["value"] == 7.30
        write_mock.assert_called_once()

    def test_http_error_returns_none_without_raising(self, live_env: None) -> None:
        from src.tools.external import fred

        with (
            patch(
                "httpx.Client.get",
                side_effect=httpx.ConnectError("boom"),
            ),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        assert result is None
        write_mock.assert_not_called()

    def test_non_200_returns_none_without_raising(self, live_env: None) -> None:
        from src.tools.external import fred

        with (
            patch(
                "httpx.Client.get",
                return_value=_mock_response(status_code=403, payload={"error": "bad key"}),
            ),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        assert result is None
        write_mock.assert_not_called()

    def test_parse_failure_returns_none(self, live_env: None) -> None:
        """Non-JSON body must not raise."""
        from src.tools.external import fred

        bad_response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "https://api.stlouisfed.org/x"),
            content=b"not json at all",
        )
        with (
            patch("httpx.Client.get", return_value=bad_response),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        assert result is None
        write_mock.assert_not_called()

    def test_all_observations_missing_returns_none(self, live_env: None) -> None:
        from src.tools.external import fred

        payload = {"observations": [{"date": "2026-05-01", "value": "."}]}
        with (
            patch("httpx.Client.get", return_value=_mock_response(payload=payload)),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        assert result is None
        write_mock.assert_not_called()

    def test_missing_api_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_MODE", raising=False)
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        monkeypatch.delenv("FRED_API_KEY_SECRET_ARN", raising=False)
        from src.tools.external import fred

        get_mock = MagicMock()
        with (
            patch("httpx.Client.get", get_mock),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        assert result is None
        get_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_test_mode_short_circuits(self) -> None:
        """conftest pins TEST_MODE=true; the client must not touch HTTP or the store."""
        from src.tools.external import fred

        get_mock = MagicMock()
        with (
            patch("httpx.Client.get", get_mock),
            patch("src.tools.external.fred.macro_series_store.write_observation") as write_mock,
        ):
            result = fred.fetch_and_cache_series("TERMCBAUTO48NS")

        assert result is None
        get_mock.assert_not_called()
        write_mock.assert_not_called()


class TestRefreshAll:
    def test_refreshes_all_default_series(self, live_env: None) -> None:
        from src.tools.external import fred

        payload = {"observations": [{"date": "2026-05-01", "value": "1.23"}]}
        with (
            patch("httpx.Client.get", return_value=_mock_response(payload=payload)),
            patch("src.tools.external.fred.macro_series_store.write_observation"),
        ):
            results = fred.refresh_all()

        assert set(results.keys()) == {"TERMCBAUTO48NS", "CUUR0000SETA02", "TOTALSA"}
        assert all(v == 1 for v in results.values())

    def test_marks_failures_as_zero(self, live_env: None) -> None:
        from src.tools.external import fred

        with (
            patch(
                "httpx.Client.get",
                side_effect=httpx.ConnectError("network down"),
            ),
            patch("src.tools.external.fred.macro_series_store.write_observation"),
        ):
            results = fred.refresh_all()

        assert all(v == 0 for v in results.values())
        assert set(results.keys()) == {"TERMCBAUTO48NS", "CUUR0000SETA02", "TOTALSA"}
