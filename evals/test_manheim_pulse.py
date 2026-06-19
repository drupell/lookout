"""Tests for src/tools/external/manheim_pulse.py.

Mocks httpx + the LLM provider + the macro-series store so no real network,
LLM, or DynamoDB is hit. The autouse `set_test_mode` fixture in conftest pins
TEST_MODE=true; tests that exercise the real path explicitly unset it.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_MODE", raising=False)


def _html_response(body: str = "<html>x</html>") -> httpx.Response:
    return httpx.Response(
        status_code=200,
        request=httpx.Request("GET", "https://publish.manheim.com/x"),
        content=body.encode(),
    )


def _llm_returning(content: str) -> MagicMock:
    """Build a mock LLM whose .invoke() returns an object with the given string content."""
    llm = MagicMock()
    message = MagicMock()
    message.content = content
    llm.invoke.return_value = message
    return llm


_FIXTURE_EXTRACTION = {
    "headline_index": 205.3,
    "month_year": "May 2026",
    "mom_change_pct": 0.4,
    "yoy_change_pct": -1.7,
    "segments": {
        "compact car": 198.4,
        "midsize car": 191.2,
        "luxury": 178.6,
        "pickup": 215.7,
        "SUV": 207.1,
    },
}


class TestFetchAndCacheIndex:
    def test_happy_path_writes_headline_and_segments(self, live_env: None) -> None:
        from src.tools.external import manheim_pulse

        llm = _llm_returning(json.dumps(_FIXTURE_EXTRACTION))
        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", return_value=llm),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is not None
        assert result["headline_index"] == 205.3

        # Headline write + 5 segments = 6 calls
        assert write_mock.call_count == 6
        ts = "2026-05-01T00:00:00+00:00"

        seen = {
            call.kwargs["series_key"]: (call.kwargs["timestamp"], call.kwargs["value"])
            for call in write_mock.call_args_list
        }
        assert seen["manheim:headline"] == (ts, 205.3)
        assert seen["manheim:compact_car"] == (ts, 198.4)
        assert seen["manheim:midsize_car"] == (ts, 191.2)
        assert seen["manheim:luxury"] == (ts, 178.6)
        assert seen["manheim:pickup"] == (ts, 215.7)
        # "SUV" → slugified to "suv"
        assert seen["manheim:suv"] == (ts, 207.1)

    def test_uses_required_model_config(self, live_env: None) -> None:
        """Per CLAUDE.md: provider, model_id, temperature, max_tokens all set explicitly."""
        from src.tools.external import manheim_pulse

        captured: dict = {}

        def fake_get_llm(config):
            captured["config"] = config
            return _llm_returning(json.dumps(_FIXTURE_EXTRACTION))

        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", side_effect=fake_get_llm),
            patch("src.tools.external.manheim_pulse.macro_series_store.write_observation"),
        ):
            manheim_pulse.fetch_and_cache_index()

        config = captured["config"]
        # Provider + model are env-driven (matches the other LLM nodes).
        # Defaults route through Bedrock + Nova Lite in deployment; tests
        # exercise the same defaults since `live_env` doesn't override them.
        assert config.provider == "bedrock"
        assert config.model_id == "amazon.nova-lite-v1:0"
        # Temperature + max_tokens are hardcoded — extraction is
        # deterministic and the response is small.
        assert config.temperature == 0.0
        assert config.max_tokens == 1024

    def test_strips_code_fences_around_llm_json(self, live_env: None) -> None:
        """LLMs sometimes wrap JSON in ```json fences — we must still parse."""
        from src.tools.external import manheim_pulse

        wrapped = "```json\n" + json.dumps(_FIXTURE_EXTRACTION) + "\n```"
        llm = _llm_returning(wrapped)
        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", return_value=llm),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is not None
        assert write_mock.call_count >= 1

    def test_http_error_returns_none_without_raising(self, live_env: None) -> None:
        from src.tools.external import manheim_pulse

        with (
            patch("httpx.Client.get", side_effect=httpx.ConnectError("boom")),
            patch("src.tools.external.manheim_pulse.get_llm") as llm_mock,
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is None
        llm_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_non_200_returns_none(self, live_env: None) -> None:
        from src.tools.external import manheim_pulse

        not_found = httpx.Response(
            status_code=404,
            request=httpx.Request("GET", "https://publish.manheim.com/x"),
            content=b"missing",
        )
        with (
            patch("httpx.Client.get", return_value=not_found),
            patch("src.tools.external.manheim_pulse.get_llm") as llm_mock,
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is None
        llm_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_llm_failure_returns_none(self, live_env: None) -> None:
        from src.tools.external import manheim_pulse

        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("provider exploded")
        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", return_value=llm),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is None
        write_mock.assert_not_called()

    def test_malformed_llm_json_returns_none(self, live_env: None) -> None:
        from src.tools.external import manheim_pulse

        llm = _llm_returning("not really json")
        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", return_value=llm),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is None
        write_mock.assert_not_called()

    def test_missing_month_year_returns_none(self, live_env: None) -> None:
        from src.tools.external import manheim_pulse

        bad = {**_FIXTURE_EXTRACTION, "month_year": None}
        llm = _llm_returning(json.dumps(bad))
        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", return_value=llm),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is None
        write_mock.assert_not_called()

    def test_missing_headline_returns_none(self, live_env: None) -> None:
        from src.tools.external import manheim_pulse

        bad = {**_FIXTURE_EXTRACTION, "headline_index": None}
        llm = _llm_returning(json.dumps(bad))
        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", return_value=llm),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is None
        write_mock.assert_not_called()

    def test_non_numeric_segment_skipped_but_headline_persists(self, live_env: None) -> None:
        """A bad segment value must not blow up the whole write — headline still lands."""
        from src.tools.external import manheim_pulse

        partial = {
            **_FIXTURE_EXTRACTION,
            "segments": {"pickup": "not a number", "suv": 207.1},
        }
        llm = _llm_returning(json.dumps(partial))
        with (
            patch("httpx.Client.get", return_value=_html_response()),
            patch("src.tools.external.manheim_pulse.get_llm", return_value=llm),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is not None
        written_keys = {call.kwargs["series_key"] for call in write_mock.call_args_list}
        assert "manheim:headline" in written_keys
        assert "manheim:suv" in written_keys
        assert "manheim:pickup" not in written_keys

    def test_test_mode_short_circuits(self) -> None:
        """conftest pins TEST_MODE=true; no HTTP, no LLM, no store calls."""
        from src.tools.external import manheim_pulse

        get_mock = MagicMock()
        llm_mock = MagicMock()
        with (
            patch("httpx.Client.get", get_mock),
            patch("src.tools.external.manheim_pulse.get_llm", llm_mock),
            patch(
                "src.tools.external.manheim_pulse.macro_series_store.write_observation"
            ) as write_mock,
        ):
            result = manheim_pulse.fetch_and_cache_index()

        assert result is None
        get_mock.assert_not_called()
        llm_mock.assert_not_called()
        write_mock.assert_not_called()
