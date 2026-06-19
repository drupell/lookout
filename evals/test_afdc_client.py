"""Tests for src/tools/external/afdc.py.

Mirrors the test layout in test_fred_client.py: mock httpx + the macro-series
store so no real network or DynamoDB is hit. The autouse `set_test_mode`
fixture in conftest pins TEST_MODE=true; tests that exercise the real HTTP
path explicitly unset it via the `live_env` fixture.
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
    monkeypatch.setenv("AFDC_API_KEY", "fake-key")


def _mock_response(*, status_code: int = 200, payload: dict | None = None) -> httpx.Response:
    content = json.dumps(payload or {}).encode()
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "https://developer.nrel.gov/x"),
        content=content,
    )


def _sample_row(
    *,
    rid: int = 409,
    title: str = "Section 30D — New EV Credit",
    summary: str = "Up to $7,500 federal tax credit for qualifying EVs.",
    state: str = "US",
    # jurisdiction is derived from `state` + `utility_id` in the normalizer;
    # we accept it as a fixture kwarg for backward-compat with existing tests
    # but otherwise ignore it.
    jurisdiction: str = "federal",
    expiration: str | None = None,
) -> dict:
    """Mirror the real AFDC catalog row shape (per developer.nlr.gov).

    Includes the EV-relevant `technologies` codes (ELEC, PHEV) and the
    consumer-facing `IND` user category so the filter chain in
    `fetch_state_incentives` accepts the row.
    """
    return {
        "id": rid,
        "state": state,
        "title": title,
        "text": f"<p>{summary}</p>",
        "plaintext": summary,
        "enacted_date": None,
        "amended_date": "2026-04-15T00:00:00Z",
        "status_date": "2026-04-15",
        "significant_update_date": "4/15/2026",
        "type": "State Incentives" if state != "US" else "Incentives",
        "agency": "Treasury" if state == "US" else "",
        "utility_id": None,
        "technologies": ["ELEC", "PHEV"],
        "technology_titles": ["EVs", "PHEVs"],
        "categories": [
            {"code": "ELEC", "title": "EVs", "category_type": "tech"},
            {"code": "PHEV", "title": "PHEVs", "category_type": "tech"},
            {
                "code": "IND",
                "title": "Personal Vehicle Owner / Driver",
                "category_type": "user",
            },
            {"code": "TAX", "title": "Tax Incentives", "category_type": "incentive"},
        ],
        "types": [
            {
                "id": 8,
                "title": "Incentives" if state == "US" else "State Incentives",
                "code": "INC" if state == "US" else "STATEINC",
            }
        ],
        "references": [{"description": "AFDC entry", "url": "https://afdc.energy.gov/laws/409"}],
        "topics": [],
        "status": "enacted",
    }


class TestFetchStateIncentives:
    def test_happy_path_returns_normalized_programs(self, live_env: None) -> None:
        """Filters the bulk catalog to the requested state (MA) only.

        Federal entries fall out via the state filter; a separate call to
        `fetch_federal_incentives` picks them up (see TestFetchFederal).
        """
        from src.tools.external import afdc

        payload = {
            "result": [
                # Federal row — must NOT appear in a state=MA result.
                _sample_row(rid=409),
                _sample_row(
                    rid=4378,
                    title="MOR-EV Rebate",
                    summary="Rebate of up to $2,500 for qualifying EVs in MA.",
                    state="MA",
                ),
            ]
        }
        with patch("httpx.Client.get", return_value=_mock_response(payload=payload)):
            result = afdc.fetch_state_incentives("MA")

        assert result is not None
        assert len(result) == 1
        state_row = result[0]
        assert state_row["id"] == "4378"
        assert state_row["amount_usd"] == 2500
        assert state_row["state"] == "MA"
        assert state_row["jurisdiction"] == "state"

    def test_http_error_returns_none(self, live_env: None) -> None:
        from src.tools.external import afdc

        with patch("httpx.Client.get", side_effect=httpx.ConnectError("boom")):
            result = afdc.fetch_state_incentives("MA")
        assert result is None

    def test_non_200_returns_none(self, live_env: None) -> None:
        from src.tools.external import afdc

        with patch(
            "httpx.Client.get",
            return_value=_mock_response(status_code=403, payload={"error": "bad key"}),
        ):
            result = afdc.fetch_state_incentives("MA")
        assert result is None

    def test_parse_failure_returns_none(self, live_env: None) -> None:
        from src.tools.external import afdc

        bad = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "https://developer.nrel.gov/x"),
            content=b"not json",
        )
        with patch("httpx.Client.get", return_value=bad):
            result = afdc.fetch_state_incentives("MA")
        assert result is None

    def test_missing_api_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_MODE", raising=False)
        monkeypatch.delenv("AFDC_API_KEY", raising=False)
        monkeypatch.delenv("AFDC_API_KEY_SECRET_ARN", raising=False)
        from src.tools.external import afdc

        get_mock = MagicMock()
        with patch("httpx.Client.get", get_mock):
            result = afdc.fetch_state_incentives("MA")

        assert result is None
        get_mock.assert_not_called()

    def test_test_mode_short_circuits(self) -> None:
        """conftest pins TEST_MODE=true; the client must not touch HTTP."""
        from src.tools.external import afdc

        get_mock = MagicMock()
        with patch("httpx.Client.get", get_mock):
            result = afdc.fetch_state_incentives("MA")
        assert result is None
        get_mock.assert_not_called()

    def test_empty_payload_returns_empty_list(self, live_env: None) -> None:
        from src.tools.external import afdc

        with patch("httpx.Client.get", return_value=_mock_response(payload={"result": []})):
            result = afdc.fetch_state_incentives("MA")
        assert result == []

    def test_filters_out_programs_and_laws_types(self, live_env: None) -> None:
        """Only 'Incentives' / 'State Incentives' type entries pass the filter.

        AFDC's catalog also returns 'Programs' (Clean Cities, P2 Grants) and
        'Laws and Regulations' (emission standards). Those describe legislative
        frameworks rather than user-claimable benefits, and their bodies
        reference legislative-scale dollar figures that polluted totals
        before this filter landed. They must be excluded.
        """
        from src.tools.external import afdc

        payload = {
            "result": [
                # Claimable — should be kept.
                {**_sample_row(rid=409, state="US"), "type": "Incentives"},
                # Initiative / program with a $50B legislative reference in
                # its body — must be dropped.
                {
                    **_sample_row(rid=288, state="US"),
                    "type": "Programs",
                    "title": "Clean Cities and Communities",
                    "plaintext": "Authorized through fiscal year 2026. $50,000,000,000.",
                },
                # Pure regulation — must be dropped.
                {
                    **_sample_row(rid=304, state="US"),
                    "type": "Laws and Regulations",
                    "title": "Tier 3 Vehicle and Gasoline Sulfur Program",
                },
            ]
        }
        with patch("httpx.Client.get", return_value=_mock_response(payload=payload)):
            result = afdc.fetch_state_incentives("US")
        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == "409"
        assert result[0]["type"] == "Incentives"

    def test_row_missing_id_dropped(self, live_env: None) -> None:
        """A row missing `id` survives the EV/consumer filter but is dropped
        in normalization (no id => can't dedupe). Asserts on a US row so we
        exercise the federal-jurisdiction path."""
        from src.tools.external import afdc

        payload = {
            "result": [
                _sample_row(rid=409, state="US"),
                # Same shape as the sample but missing `id` — the filter
                # accepts it (EV-relevant + consumer-facing) but normalize
                # drops it.
                {
                    **_sample_row(rid=999, state="US"),
                    "id": None,
                },
            ]
        }
        with patch("httpx.Client.get", return_value=_mock_response(payload=payload)):
            result = afdc.fetch_state_incentives("US")
        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == "409"


class TestFetchFederal:
    def test_federal_filters_to_state_us(self, live_env: None) -> None:
        """The endpoint doesn't accept a state param — we filter client-side.

        Mixed catalog (US + MA rows) should be filtered to US-only when
        fetch_federal_incentives() runs.
        """
        from src.tools.external import afdc

        captured = {}

        def fake_get(self, url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _mock_response(
                payload={
                    "result": [
                        _sample_row(rid=409, state="US"),
                        _sample_row(rid=4378, state="MA"),
                    ]
                }
            )

        with patch("httpx.Client.get", new=fake_get):
            result = afdc.fetch_federal_incentives()

        assert result is not None
        # State filter happens client-side now; bare API call should not
        # include a state param.
        assert "state" not in (captured["params"] or {})
        assert len(result) == 1
        assert result[0]["state"] == "US"


class TestRefreshForStates:
    def test_dedupes_states_and_caches_each(self, live_env: None) -> None:
        from src.tools.external import afdc

        payload = {"result": [_sample_row()]}
        with (
            patch("httpx.Client.get", return_value=_mock_response(payload=payload)),
            patch("src.tools.external.afdc.macro_series_store.write_observation") as write_mock,
        ):
            results = afdc.refresh_for_states(["MA", "ma", "CA", "MA"])

        # US (federal) + MA + CA — duplicates collapsed.
        assert set(results.keys()) == {"afdc:US", "afdc:MA", "afdc:CA"}
        assert all(v == 1 for v in results.values())
        # write called 3x — once per cached key. Each call should include the
        # programs payload under provider_metadata.
        assert write_mock.call_count == 3
        for call in write_mock.call_args_list:
            kwargs = call.kwargs
            assert "provider_metadata" in kwargs
            assert "programs" in kwargs["provider_metadata"]

    def test_skips_federal_when_listed_redundantly(self, live_env: None) -> None:
        from src.tools.external import afdc

        with (
            patch(
                "httpx.Client.get",
                return_value=_mock_response(payload={"result": [_sample_row()]}),
            ),
            patch("src.tools.external.afdc.macro_series_store.write_observation"),
        ):
            results = afdc.refresh_for_states(["US", "US"])
        # US handled once (as federal), no duplicate per-state entries.
        assert "afdc:US" in results
        # No state-level entry for "US" — refresh_for_states treats it as a
        # no-op state.
        assert len([k for k in results if k != "afdc:US"]) == 0

    def test_marks_failures_as_zero(self, live_env: None) -> None:
        from src.tools.external import afdc

        with (
            patch("httpx.Client.get", side_effect=httpx.ConnectError("down")),
            patch("src.tools.external.afdc.macro_series_store.write_observation"),
        ):
            results = afdc.refresh_for_states(["MA"])

        assert results["afdc:US"] == 0
        assert results["afdc:MA"] == 0


class TestSecretResilience:
    """Per the cross-source resilience design, _get_secret must never raise.

    A placeholder secret that exists but hasn't been seeded yet (the
    common case after first deploy, before the operator pastes the API
    key) returns `{}` so the caller's `.get('api_key', '')` yields `""`
    and the AFDC call gracefully no-ops.
    """

    def test_empty_secret_string_returns_empty_dict(self) -> None:
        from src.tools.external.afdc import _get_secret

        _get_secret.cache_clear()
        with patch(
            "boto3.client",
            return_value=MagicMock(get_secret_value=MagicMock(return_value={"SecretString": ""})),
        ):
            assert _get_secret("arn:test:not-yet-seeded") == {}

    def test_missing_secret_string_returns_empty_dict(self) -> None:
        from src.tools.external.afdc import _get_secret

        _get_secret.cache_clear()
        with patch(
            "boto3.client",
            return_value=MagicMock(
                get_secret_value=MagicMock(return_value={})  # No SecretString key
            ),
        ):
            assert _get_secret("arn:test:none-seeded") == {}

    def test_malformed_json_returns_empty_dict(self) -> None:
        from src.tools.external.afdc import _get_secret

        _get_secret.cache_clear()
        with patch(
            "boto3.client",
            return_value=MagicMock(
                get_secret_value=MagicMock(return_value={"SecretString": "not-json"})
            ),
        ):
            assert _get_secret("arn:test:malformed") == {}

    def test_boto3_error_returns_empty_dict(self) -> None:
        from src.tools.external.afdc import _get_secret

        _get_secret.cache_clear()
        with patch(
            "boto3.client",
            return_value=MagicMock(
                get_secret_value=MagicMock(side_effect=RuntimeError("no perms"))
            ),
        ):
            assert _get_secret("arn:test:no-perms") == {}


class TestParseAmount:
    def test_picks_largest_under_ceiling(self) -> None:
        from src.tools.external import afdc

        # Income limit of $150,000 should be ignored as noise; pick the $7,500
        # incentive headline.
        text = "Up to $7,500 federal tax credit. Income cap of $150,000 for single filers."
        assert afdc._parse_amount_usd(text) == 7500

    def test_ignores_msrp_cap_above_ceiling(self) -> None:
        """Section 25E body: $4,000 credit for used EVs with sale price under $25,000.

        Pre-fix: parser picked $25,000 as the headline. Post-fix: ceiling drops
        the MSRP cap and the real $4,000 credit wins.
        """
        from src.tools.external import afdc

        text = (
            "Eligible used EVs with a sale price of $25,000 or less qualify for "
            "a federal tax credit of up to $4,000."
        )
        assert afdc._parse_amount_usd(text) == 4000

    def test_returns_none_when_no_dollar_sign(self) -> None:
        from src.tools.external import afdc

        assert afdc._parse_amount_usd("No amount mentioned anywhere") is None

    def test_ceiling_drops_legislative_billions(self) -> None:
        """Defense in depth — type filter already drops Programs bodies, but
        if one slips through, the ceiling catches obvious legislative noise."""
        from src.tools.external import afdc

        text = "Authorized through fiscal year 2026. $50,000,000,000 in appropriations."
        assert afdc._parse_amount_usd(text) is None


class TestParseExpiration:
    """Coverage of the AFDC plaintext expiration-date parser.

    The catalog rarely populates a structured `expiration_date`, so
    `_parse_expiration_date` lifts dates out of program bodies using a small
    family of common phrasings: "through Month DD, YYYY", "expires …",
    "ends …", numeric M/D/YY, ISO YYYY-MM-DD, "fiscal year YYYY" (→ Sep 30),
    "until YYYY" (→ Dec 31), "sunsets YYYY".
    """

    def test_through_month_day_year(self) -> None:
        from src.tools.external import afdc

        assert afdc._parse_expiration_date("Eligible through May 31, 2030.") == "2030-05-31"

    def test_expires_month_day_year(self) -> None:
        from src.tools.external import afdc

        assert (
            afdc._parse_expiration_date("The credit expires December 31, 2030 for new vehicles.")
            == "2030-12-31"
        )

    def test_ends_numeric_short_year(self) -> None:
        from src.tools.external import afdc

        # "ends 12/31/30" → 2030-12-31. Hits the numeric-date branch.
        assert afdc._parse_expiration_date("Rebate ends 12/31/30 absent extension.") == "2030-12-31"

    def test_until_year_only_defaults_to_year_end(self) -> None:
        from src.tools.external import afdc

        # Bare year → defaults to Dec 31 of that year.
        assert afdc._parse_expiration_date("Available until 2029 unless renewed.") == "2029-12-31"

    def test_sunsets_year_only(self) -> None:
        from src.tools.external import afdc

        assert (
            afdc._parse_expiration_date("Statute sunsets 2030 absent further action.")
            == "2030-12-31"
        )

    def test_fiscal_year_resolves_to_sep_30(self) -> None:
        from src.tools.external import afdc

        # Federal fiscal year ends Sep 30 of the named year.
        assert (
            afdc._parse_expiration_date("Funding authorized through fiscal year 2030.")
            == "2030-09-30"
        )

    def test_iso_date(self) -> None:
        from src.tools.external import afdc

        # Picks the embedded ISO date verbatim.
        assert (
            afdc._parse_expiration_date("Program closes on 2030-05-31 per state guidance.")
            == "2030-05-31"
        )

    def test_far_future_date_returns_none(self) -> None:
        """Dates more than ~5 years out are statutory references, not real
        expirations — the parser must not surface those as warnings."""
        from src.tools.external import afdc

        # Pick a date well past the 5-year horizon so the assertion stays
        # stable regardless of test run date.
        assert (
            afdc._parse_expiration_date("This authority extends through December 31, 2099.") is None
        )

    def test_no_match_returns_none(self) -> None:
        from src.tools.external import afdc

        assert (
            afdc._parse_expiration_date(
                "Eligible vehicles qualify for a tax credit. See guidance for limits."
            )
            is None
        )

    def test_empty_body_returns_none(self) -> None:
        from src.tools.external import afdc

        assert afdc._parse_expiration_date("") is None
        assert afdc._parse_expiration_date(None) is None  # type: ignore[arg-type]

    def test_invalid_numeric_date_skipped(self) -> None:
        """13/45/2030 isn't a valid calendar date — the parser must not
        construct an ISO string out of it."""
        from src.tools.external import afdc

        assert afdc._parse_expiration_date("Program ends 13/45/2030.") is None
