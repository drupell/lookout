"""DOE AFDC (Alternative Fuels Data Center) client.

Pulls the curated federal/state/utility EV-incentive *program* catalog that
backs the incentive-stack calculator. The API is free at
https://developer.nrel.gov/api/transportation-incentives-laws/.

API key resolution mirrors `src/tools/external/fred.py`:
    1. AFDC_API_KEY env var (local/dev convenience).
    2. AFDC_API_KEY_SECRET_ARN env -> Secrets Manager (Lambda).

Resilience contract: downstream callers (the `/me/incentives` handler) treat
`None` as "no fresh data, fall back to cache." We therefore never raise — any
failure is logged and returns None. The handler reads the last cached payload
from `macro_series_store` and badges the UI surface as stale per the plan's
cross-source resilience design.

Caching: AFDC's endpoint returns *list* payloads (one row per program), not
single scalars like FRED, so we stash the entire list under
`provider_metadata={"programs": [...]}` on the existing macro-series store
(saves us building a separate table for the same shape of data) and read it
back via `get_latest`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import httpx

from src.memory import macro_series_store

logger = logging.getLogger(__name__)

# The canonical NREL developer host is `developer.nlr.gov`. (Yes — `nlr`,
# not `nrel`. The agency abbreviation is non-obvious; the working host name
# is the one with no 'e'. An earlier version of this client pointed at the
# nonexistent `developer.nrel.gov` which is why DNS failed everywhere.)
# api.data.gov is the federal API umbrella, kept as a fallback for the case
# where the direct host has a transient issue.
AFDC_LAWS_URL_PRIMARY = "https://developer.nlr.gov/api/transportation-incentives-laws/v1.json"
AFDC_LAWS_URL_FALLBACK = "https://api.data.gov/nlr/transportation-incentives-laws/v1.json"
# Kept for backward-compat with callers (tests) — points at the primary.
AFDC_LAWS_URL = AFDC_LAWS_URL_PRIMARY

# AFDC's convention: state="US" returns federal entries (the same endpoint
# serves both — there's no separate /federal endpoint).
FEDERAL_STATE_CODE = "US"

# Technologies we pull. AFDC accepts a comma-separated list. The catalog
# includes many non-EV categories (LPG, CNG, biodiesel) we deliberately omit.
BEV_PHEV_TECHNOLOGIES = "BEV,PHEV"

# Regex picks up dollar amounts in program-summary text. AFDC doesn't expose a
# structured `amount_usd` field, so we extract a best-effort scalar from the
# summary for the dashboard headline. Captures values like $7,500, $2500, $1,000.
_DOLLAR_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.\d+)?")


def fetch_state_incentives(state_code: str) -> list[dict[str, Any]] | None:
    """Fetch BEV+PHEV individual-user incentive programs for a state.

    Returns a list of normalized program dicts on success, or `None` on any
    failure (network, auth, parse, empty). Honors TEST_MODE.

    Args:
        state_code: 2-letter US state code (e.g. "MA") or "US" for federal.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: skipping AFDC fetch for state=%s", state_code)
        return None

    api_key = _resolve_api_key()
    if not api_key:
        logger.warning(
            "AFDC API key not configured (set AFDC_API_KEY or "
            "AFDC_API_KEY_SECRET_ARN); skipping fetch for state=%s",
            state_code,
        )
        return None

    # The endpoint doesn't accept state/user_type/technology query params —
    # those return 422 ("not a valid argument"). It only takes api_key and
    # returns the full catalog (~2,700 entries). We filter client-side using
    # the `state`, `technologies`, and `categories` fields on each entry.
    params = {"api_key": api_key}
    payload: dict[str, Any] | None = None
    last_exc: Exception | None = None
    for url in (AFDC_LAWS_URL_PRIMARY, AFDC_LAWS_URL_FALLBACK):
        logger.info("AFDC fetch: %s (will filter to state=%s)", url, state_code)
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, params=params)
            if response.status_code != 200:
                logger.warning(
                    "AFDC state=%s via %s returned HTTP %d: %s",
                    state_code,
                    url,
                    response.status_code,
                    response.text[:200],
                )
                continue
            payload = response.json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            logger.warning("AFDC state=%s via %s fetch failed: %s", state_code, url, exc)
            continue

    if payload is None:
        logger.warning("AFDC state=%s: both URLs failed (last error: %s)", state_code, last_exc)
        return None

    raw_rows = payload.get("result") or payload.get("laws") or []
    if not isinstance(raw_rows, list):
        logger.warning("AFDC state=%s returned non-list payload", state_code)
        return None

    # Client-side filter (every clause has to pass):
    #   - state field matches the requested state (or "US" for federal)
    #   - technologies list includes ELEC or PHEV (EV-relevant)
    #   - categories has user_type=IND (Personal Vehicle Owner / Driver) so
    #     we skip fleet-only / govt-only programs that aren't actionable for
    #     a consumer dashboard
    #   - type field is "Incentives" or "State Incentives" — i.e. a benefit
    #     the user can actually claim, not a "Programs" initiative
    #     (Clean Cities, P2 Grants, etc.) or "Laws and Regulations". This
    #     is the load-bearing fix for the headline-dollar accuracy: program
    #     descriptions reference large legislative dollar figures
    #     ($50 billion in IRA appropriations, etc.) that the amount parser
    #     was incorrectly attributing to the user.
    target_state = state_code.upper()
    filtered = [
        row
        for row in raw_rows
        if isinstance(row, dict)
        and row.get("state", "").upper() == target_state
        and _row_is_ev_relevant(row)
        and _row_is_consumer_facing(row)
        and _row_is_claimable_incentive(row)
    ]
    programs = [_normalize_program(row) for row in filtered]
    programs = [p for p in programs if p is not None]
    logger.info(
        "AFDC state=%s: filtered %d/%d entries to %d programs",
        state_code,
        len(filtered),
        len(raw_rows),
        len(programs),
    )
    return programs


def _row_is_ev_relevant(row: dict[str, Any]) -> bool:
    """True iff the row's `technologies` list includes ELEC or PHEV."""
    techs = row.get("technologies") or []
    if not isinstance(techs, list):
        return False
    return any(isinstance(t, str) and t.upper() in {"ELEC", "PHEV"} for t in techs)


def _row_is_consumer_facing(row: dict[str, Any]) -> bool:
    """True iff the row's categories include IND (Personal Vehicle Owner / Driver).

    Filters out fleet-only / govt-only / commercial-only incentives that
    aren't actionable for a consumer-facing dashboard.
    """
    cats = row.get("categories") or []
    if not isinstance(cats, list):
        return False
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        if cat.get("category_type") == "user" and cat.get("code") == "IND":
            return True
    return False


# AFDC's `type` field bucket. Only the two below describe benefits the user
# can directly claim. The others (Programs / Laws and Regulations) describe
# initiatives, regulatory frameworks, or funding pools that aren't a tax
# credit or rebate to a consumer — they pollute totals badly because their
# bodies reference legislative-scale dollar figures.
_CLAIMABLE_TYPES = frozenset({"Incentives", "State Incentives"})


def _row_is_claimable_incentive(row: dict[str, Any]) -> bool:
    """True iff the row's `type` is a directly-claimable consumer incentive.

    Excludes "Programs" (initiatives like Clean Cities and Communities) and
    "Laws and Regulations" (emission standards, fleet purchase requirements)
    that aren't tax credits or rebates.
    """
    return row.get("type") in _CLAIMABLE_TYPES


def fetch_federal_incentives() -> list[dict[str, Any]] | None:
    """Fetch federal-jurisdiction BEV+PHEV programs.

    AFDC's convention is `state=US` for federal entries on the same endpoint —
    no separate /federal route exists.
    """
    return fetch_state_incentives(FEDERAL_STATE_CODE)


def refresh_for_states(states: list[str]) -> dict[str, int]:
    """Refresh AFDC caches for each unique state + the federal layer.

    Calls `fetch_state_incentives` for each unique state code in `states`
    (case-normalized), plus `fetch_federal_incentives` once. Each successful
    fetch is persisted to the macro-series store under
    `series_key="afdc:<US|state_code>"` with the program list stashed in
    `provider_metadata.programs`. The macro store rejects floats vs Decimals
    transparently — we pass `value=0.0` because the real payload is the list
    in metadata; the store interprets the scalar `value` as required and we
    don't have a single numeric to put there for a list-of-programs feed.

    Returns: dict mapping `series_key -> 1` on success, `0` on failure. The
    nightly cron reads this for alarm bookkeeping per the plan's resilience
    section.
    """
    results: dict[str, int] = {}

    # Federal pass first so it's always in the cache for non-state-aware
    # callers.
    fed_programs = fetch_federal_incentives()
    results[f"afdc:{FEDERAL_STATE_CODE}"] = _cache_programs(
        f"afdc:{FEDERAL_STATE_CODE}", fed_programs
    )

    seen: set[str] = set()
    for raw in states:
        if not isinstance(raw, str):
            continue
        code = raw.strip().upper()
        if not code or code == FEDERAL_STATE_CODE or code in seen:
            continue
        seen.add(code)

        programs = fetch_state_incentives(code)
        results[f"afdc:{code}"] = _cache_programs(f"afdc:{code}", programs)

    return results


# ---- helpers ----


def _cache_programs(series_key: str, programs: list[dict[str, Any]] | None) -> int:
    """Persist a programs list to macro_series_store under `series_key`.

    Returns 1 on success, 0 if there's nothing to cache or the write fails.
    Never raises — caller treats 0 as a soft failure and falls back to the
    last cached row.
    """
    if programs is None:
        return 0
    timestamp = datetime.now(UTC).isoformat()
    try:
        macro_series_store.write_observation(
            series_key=series_key,
            timestamp=timestamp,
            value=0.0,
            provider_metadata={"programs": programs},
        )
    except Exception as exc:
        logger.warning("AFDC cache write failed for %s: %s", series_key, exc)
        return 0
    return 1


def _normalize_program(row: dict[str, Any]) -> dict[str, Any] | None:
    """Project an AFDC raw row to our normalized shape.

    Returns None if the row is missing an `id` — we can't dedupe without one.

    Maps real AFDC fields:
      - `plaintext` (markdown body) -> summary; falls back to text/HTML
      - state="US" -> jurisdiction="federal"; non-null utility_id -> "utility";
        else "state"
      - last_updated derives from amended_date, status_date, then
        significant_update_date (most reliable to least)
      - source_url from the first `references[].url` if present
      - dollar amount best-effort parsed from the plaintext
    """
    program_id = row.get("id")
    if program_id is None:
        return None

    plaintext = row.get("plaintext") or ""
    if not plaintext:
        # Fall back to the HTML field if plaintext is missing.
        plaintext = row.get("text") or ""
    amount = _parse_amount_usd(plaintext)

    state = (row.get("state") or "").upper()
    if state == "US":
        jurisdiction = "federal"
    elif row.get("utility_id") is not None:
        jurisdiction = "utility"
    else:
        jurisdiction = "state"

    last_updated = (
        row.get("amended_date") or row.get("status_date") or row.get("significant_update_date")
    )

    source_url = ""
    refs = row.get("references") or []
    if isinstance(refs, list) and refs:
        first = refs[0]
        if isinstance(first, dict):
            source_url = first.get("url") or ""

    # Summary: keep it short for the UI; the full body lives in `text`.
    summary = plaintext[:500].strip()

    # Structured `expiration_date` is essentially never populated in the AFDC
    # catalog — expiration language lives in the plaintext body. Prefer the
    # structured field if it's there (defensive), otherwise lift a date out
    # of the body via the regex chain.
    structured_exp = row.get("expiration_date")
    if isinstance(structured_exp, str) and structured_exp.strip():
        expiration_date: str | None = structured_exp
    else:
        expiration_date = _parse_expiration_date(plaintext)

    return {
        "id": str(program_id),
        "title": row.get("title") or "",
        "type": row.get("type") or "",
        "jurisdiction": jurisdiction,
        "state": state,
        "summary": summary,
        "expiration_date": expiration_date,
        "last_updated": last_updated,
        "amount_usd": amount,
        "categories": row.get("categories") or [],
        "source_url": source_url,
    }


def _parse_amount_usd(text: str) -> int | None:
    """Best-effort dollar-amount extraction from a program summary.

    AFDC doesn't expose `amount_usd` structurally. The summary almost always
    names the cap (e.g. "...rebate of up to $2,500..."). We grab the *largest*
    dollar figure under a consumer-incentive ceiling and treat that as the
    headline value. The ceiling tightens cleanly around the noise the parser
    was picking up in production:

      - $25,000 MSRP caps in Section 25E's body (Pre-Owned EV)
      - $50,000-ish income caps on state rebates
      - $50B-scale legislative-appropriation figures in Programs bodies
        (already filtered upstream by the type filter, but defense in depth)

    No legitimate consumer-claimable amount exceeds the ceiling: Section
    30D maxes at $7,500; the largest state EV charger rebate we've seen is
    $6,000. If a future incentive does exceed it (some commercial credits
    do, but we filter those out via the IND user category upstream), bump
    here and add a regression test.

    Returns int on success, None when no recognizable amount is found.
    """
    if not text:
        return None
    candidates: list[int] = []
    for match in _DOLLAR_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > _CONSUMER_AMOUNT_CEILING:
            continue
        candidates.append(value)
    if not candidates:
        return None
    return max(candidates)


# Maximum plausible single-program consumer incentive amount. See the
# rationale in `_parse_amount_usd`.
_CONSUMER_AMOUNT_CEILING = 10_000


# ---- expiration date parsing ----

# AFDC bodies almost never populate a structured `expiration_date`. Programs
# instead embed expiration language in prose: "through May 31, 2026",
# "expires December 31, 2026", "ends 12/31/26", etc. This regex set walks
# the body for those common phrasings and lifts a normalized ISO date out.
#
# Conservative by design — when the language is ambiguous (e.g. only a year
# is mentioned) we either default to a reasonable terminal day (Dec 31 for
# bare year, Sep 30 for "fiscal year YYYY") or return None entirely.
# Anything more than five years in the future is treated as a statutory
# reference, not a real expiration.

_MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_MONTH_NAMES = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December"
)

# "through May 31, 2026" / "through 2026" — leaves both month-day-year and
# bare-year branches up to the parser to interpret. The optional non-capturing
# group makes the month-day prefix optional.
_THROUGH_RE = re.compile(
    rf"through\s+(?:({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+)?(\d{{4}})",
    re.IGNORECASE,
)

# "expires May 31, 2026" / "expires on December 31, 2026" / "expires 2026"
_EXPIRES_RE = re.compile(
    rf"expires?\s+(?:on\s+)?(?:({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+)?(\d{{4}})",
    re.IGNORECASE,
)

# "ends May 31, 2026" / "ends on 12/31/26"
_ENDS_RE = re.compile(
    rf"ends?\s+(?:on\s+)?(?:({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+)?(\d{{4}})",
    re.IGNORECASE,
)

# "sunsets 2026" / "sunset on 2026"
_SUNSET_RE = re.compile(
    rf"sunsets?\s+(?:on\s+)?(?:({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+)?(\d{{4}})",
    re.IGNORECASE,
)

# "until 2026" — year only is the common case (we never see a month here).
_UNTIL_RE = re.compile(r"until\s+(\d{4})", re.IGNORECASE)

# "fiscal year 2026" — federal fiscal year ends Sep 30 of the named year.
_FISCAL_YEAR_RE = re.compile(r"fiscal\s+year\s+(\d{4})", re.IGNORECASE)

# Numeric forms: "12/31/26", "12/31/2026", "9/30/2026"
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

# ISO form: "2026-05-31"
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# Anything further out than this is interpreted as statutory reference rather
# than a near-term expiration — keeps "authorized through 2050" from polluting
# the warning chip.
_MAX_PARSE_FUTURE_YEARS = 5


def _safe_date(year: int, month: int, day: int) -> str | None:
    """Validate (year, month, day) and emit ISO. None on any invalid combo."""
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _within_future_window(iso_date: str) -> bool:
    """True if `iso_date` falls between now and `_MAX_PARSE_FUTURE_YEARS` out.

    Past dates are still returned — the handler treats them as "expired" and
    we want that signal to surface so the warning copy can say so.
    """
    try:
        dt = datetime.fromisoformat(iso_date)
    except ValueError:
        return False
    now = datetime.now(UTC).replace(tzinfo=None)
    if dt < now.replace(year=now.year - 1):
        # More than a year in the past — almost certainly noise.
        return False
    max_dt = now.replace(year=now.year + _MAX_PARSE_FUTURE_YEARS)
    return dt <= max_dt


def _parse_expiration_date(text: str) -> str | None:
    """Best-effort expiration-date extraction from a program plaintext body.

    Walks the common AFDC phrasings in order of specificity (explicit month-
    day-year first; bare year last) and returns the first parseable ISO date
    that lands within our 5-year forward window. Returns None when the body
    is ambiguous — false positives on the warning chip are worse than no
    warning at all.

    The handler in `src/api/incentives.py` can layer a curated YAML override
    on top of this; this function only handles the AFDC-derived path.
    """
    if not text:
        return None

    # 1) Explicit ISO date — always wins when present.
    iso_match = _ISO_DATE_RE.search(text)
    if iso_match:
        y, m, d = (int(g) for g in iso_match.groups())
        iso = _safe_date(y, m, d)
        if iso and _within_future_window(iso):
            return iso

    # 2) "through {Month DD,} YYYY"
    iso = _try_named_date(_THROUGH_RE.search(text), default_md=(12, 31))
    if iso:
        return iso

    # 3) "expires {Month DD,} YYYY"
    iso = _try_named_date(_EXPIRES_RE.search(text), default_md=(12, 31))
    if iso:
        return iso

    # 4) "ends {Month DD,} YYYY"
    iso = _try_named_date(_ENDS_RE.search(text), default_md=(12, 31))
    if iso:
        return iso

    # 5) "sunsets {Month DD,} YYYY"
    iso = _try_named_date(_SUNSET_RE.search(text), default_md=(12, 31))
    if iso:
        return iso

    # 6) Numeric date: M/D/YY or M/D/YYYY anywhere in the body.
    for match in _NUMERIC_DATE_RE.finditer(text):
        month = int(match.group(1))
        day = int(match.group(2))
        year_raw = int(match.group(3))
        year = 2000 + year_raw if year_raw < 100 else year_raw
        iso = _safe_date(year, month, day)
        if iso and _within_future_window(iso):
            return iso

    # 7) "fiscal year YYYY" -> Sep 30 of that year.
    fy_match = _FISCAL_YEAR_RE.search(text)
    if fy_match:
        year = int(fy_match.group(1))
        iso = _safe_date(year, 9, 30)
        if iso and _within_future_window(iso):
            return iso

    # 8) "until YYYY" -> Dec 31 of that year (least specific; lowest priority).
    until_match = _UNTIL_RE.search(text)
    if until_match:
        year = int(until_match.group(1))
        iso = _safe_date(year, 12, 31)
        if iso and _within_future_window(iso):
            return iso

    return None


def _try_named_date(
    match: re.Match[str] | None,
    *,
    default_md: tuple[int, int],
) -> str | None:
    """Resolve a "{Month DD,} YYYY" / bare-year regex match to an ISO date.

    `default_md` (month, day) is used when only the year captured group is
    present — most callers pass (12, 31) so a bare year terminates at the
    calendar year-end.
    """
    if match is None:
        return None
    month_name = match.group(1)
    day_str = match.group(2)
    year_str = match.group(3)
    if not year_str:
        return None
    year = int(year_str)
    if month_name and day_str:
        month = _MONTHS.get(month_name.lower())
        if month is None:
            return None
        day = int(day_str)
        iso = _safe_date(year, month, day)
    else:
        iso = _safe_date(year, default_md[0], default_md[1])
    if iso and _within_future_window(iso):
        return iso
    return None


# ---- API key resolution ----


def _resolve_api_key() -> str:
    """Resolve the AFDC API key.

    Precedence (mirrors `fred._resolve_api_key`):
      1. AFDC_API_KEY env var (local/dev convenience).
      2. AFDC_API_KEY_SECRET_ARN env -> Secrets Manager (Lambda).
    """
    direct = os.environ.get("AFDC_API_KEY", "").strip()
    if direct:
        return direct

    secret_arn = os.environ.get("AFDC_API_KEY_SECRET_ARN", "").strip()
    if secret_arn:
        return _get_secret(secret_arn).get("api_key", "")

    return ""


@lru_cache(maxsize=4)
def _get_secret(secret_arn: str) -> dict[str, str]:
    """Fetch a JSON secret from Secrets Manager (cached per cold start).

    Returns `{}` on any failure — missing secret, missing SecretString,
    malformed JSON. The caller's `.get("api_key", "")` then yields `""`,
    which `fetch_state_incentives` treats as a configuration failure (logs
    + returns None). This preserves the cross-source resilience contract:
    a not-yet-seeded placeholder secret must not raise out of the cron.
    """
    import boto3

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_arn)
    except Exception:
        logger.warning("AFDC secret unreachable; treating as unconfigured", exc_info=True)
        return {}

    secret_string = response.get("SecretString") or ""
    if not secret_string.strip():
        return {}
    try:
        return json.loads(secret_string)
    except json.JSONDecodeError:
        logger.warning("AFDC secret value isn't JSON; treating as unconfigured")
        return {}
