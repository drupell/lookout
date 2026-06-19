"""FRED (Federal Reserve Economic Data) client.

Pulls the macro series that feed the Macro layer of the market-signal feature:
auto-loan rate, used-car CPI, and total vehicle sales. Free, official, no ToS
friction — see https://fred.stlouisfed.org/docs/api/fred/.

API key resolution mirrors `src/tools/listings/marketcheck._resolve_api_key`:
    1. FRED_API_KEY env var (local/dev convenience).
    2. FRED_API_KEY_SECRET_ARN env -> Secrets Manager (Lambda).

Resilience contract: callers downstream (the persist_market_snapshot node)
treat `None` as "no fresh data, fall back to cache." We therefore never raise
— any failure is logged and returns None.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

import httpx

from src.memory import macro_series_store

logger = logging.getLogger(__name__)

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

# The three series the Macro layer composes from.
#   TERMCBAUTO48NS — 48-mo new car loan rate
#   CUUR0000SETA02 — CPI used cars
#   TOTALSA        — total vehicle sales
DEFAULT_SERIES: tuple[str, ...] = (
    "TERMCBAUTO48NS",
    "CUUR0000SETA02",
    "TOTALSA",
)


def fetch_and_cache_series(series_id: str) -> dict | None:
    """Fetch the most recent observation for a FRED series and cache it.

    Returns the cached observation dict `{series_key, timestamp, value}` on
    success; `None` on any failure (network, auth, parse, empty result).

    Honors `TEST_MODE=true`: short-circuits before any HTTP or AWS work, per
    the project's non-negotiable rule. Eval suites set this in conftest.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: skipping FRED fetch for %s", series_id)
        return None

    api_key = _resolve_api_key()
    if not api_key:
        logger.warning(
            "FRED API key not configured (set FRED_API_KEY or "
            "FRED_API_KEY_SECRET_ARN); skipping fetch for %s",
            series_id,
        )
        return None

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 12,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(FRED_OBSERVATIONS_URL, params=params)
        if response.status_code != 200:
            logger.warning(
                "FRED %s returned HTTP %d: %s",
                series_id,
                response.status_code,
                response.text[:200],
            )
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("FRED %s fetch failed: %s", series_id, exc)
        return None

    # FRED encodes missing values as the literal string ".". Skip those.
    # The list arrives in desc order. Persist ALL usable observations so the
    # consumer (persist_market_snapshot) can compute a trailing baseline —
    # without ~12mo of history a single point can't drive a relative-delta
    # factor. Each observation has its own date, so they write as distinct
    # (series_key, timestamp) rows; re-running the cron just overwrites
    # idempotently.
    observations = payload.get("observations") or []
    parsed: list[tuple[str, float]] = []
    for obs in observations:
        raw_value = obs.get("value")
        if raw_value is None or raw_value == ".":
            continue
        try:
            value = float(raw_value)
            date = obs["date"]
            timestamp = f"{date}T00:00:00+00:00"
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("FRED %s observation parse failed: %s", series_id, exc)
            continue
        parsed.append((timestamp, value))

    if not parsed:
        logger.warning("FRED %s returned no usable observations", series_id)
        return None

    series_key = f"fred:{series_id}"
    written = 0
    for timestamp, value in parsed:
        try:
            macro_series_store.write_observation(
                series_key=series_key,
                timestamp=timestamp,
                value=value,
            )
            written += 1
        except Exception:
            logger.exception("FRED %s write failed for ts=%s; continuing", series_id, timestamp)

    logger.info("FRED %s: persisted %d observations", series_id, written)

    if written == 0:
        return None

    # Newest observation is what we report to the caller — same shape the
    # macro_snapshot handler expects (latest value + its observation date).
    timestamp, value = parsed[0]
    return {
        "series_key": series_key,
        "timestamp": timestamp,
        "value": value,
    }


def refresh_all() -> dict[str, int]:
    """Refresh every series the market-signal Macro layer depends on.

    Returns a dict mapping `series_id -> 1` on success, `0` on failure.
    The nightly cron handler (out of scope here) reads this for alarm
    bookkeeping; consecutive-failure alarms per the plan's resilience
    section live one layer up.
    """
    results: dict[str, int] = {}
    for series_id in DEFAULT_SERIES:
        result = fetch_and_cache_series(series_id)
        results[series_id] = 1 if result is not None else 0
    return results


# ---- API key resolution ----


def _resolve_api_key() -> str:
    """Resolve the FRED API key.

    Precedence (mirrors `marketcheck._resolve_api_key`):
      1. FRED_API_KEY env var (local/dev convenience).
      2. FRED_API_KEY_SECRET_ARN env -> Secrets Manager (Lambda).
    """
    direct = os.environ.get("FRED_API_KEY", "").strip()
    if direct:
        return direct

    secret_arn = os.environ.get("FRED_API_KEY_SECRET_ARN", "").strip()
    if secret_arn:
        return _get_secret(secret_arn).get("api_key", "")

    return ""


@lru_cache(maxsize=4)
def _get_secret(secret_arn: str) -> dict[str, str]:
    """Fetch a JSON secret from Secrets Manager (cached per cold start)."""
    import boto3

    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])
