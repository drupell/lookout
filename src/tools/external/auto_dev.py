"""auto.dev API client.

Hits the public auto.dev `/listings` endpoint to pull retail listings and
derive comparison medians. The new-vs-used arbitrage callout uses this
client to compute the median price spread between current-year new and 1-2
year-old used examples of the user's primary make/model — the supporting
metric described in the plan ("1-2 yr used examples are selling within $3k
of new — buying new captures unusual value this month").

API key resolution mirrors `src/tools/external/fred.py` and
`src/tools/external/afdc.py`:
    1. AUTODEV_API_KEY env var (local/dev convenience).
    2. AUTODEV_API_KEY_SECRET_ARN env -> Secrets Manager (Lambda).

Resilience contract: callers downstream (the `/me/used-vs-new-arbitrage`
handler) treat `None` as "no fresh data". We therefore never raise — any
failure is logged and returns None. Honors `TEST_MODE=true`: short-circuits
before any HTTP or AWS work, per the project's non-negotiable rule.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from functools import lru_cache
from statistics import median
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AUTODEV_LISTINGS_URL = "https://api.auto.dev/listings"

# Cap each side of the comparison query at 50 listings. We only need a
# median, not the whole catalog; pulling more would burn the 1000/mo free
# tier without changing the headline. 50 also keeps the response under
# auto.dev's default page size so we don't need pagination.
_MAX_LISTINGS_PER_QUERY = 50

# Sanity bounds on parsed prices. Anything under is almost certainly a
# placeholder ("$1"), anything over is a misformatted MSRP / total-cost
# field that would poison the median.
_MIN_REASONABLE_PRICE_USD = 1_000
_MAX_REASONABLE_PRICE_USD = 500_000


def fetch_make_model_comps(
    *, zip_code: str, radius_miles: int, make: str, model: str
) -> dict[str, Any] | None:
    """Pull recent-year new + 1-2yr-old used comps for a specific make/model.

    Two queries against `/listings` — one for current-year new examples,
    one for 1-2 year old used examples — both scoped to the user's zip +
    radius. Returns the median selling price for each side plus the listing
    counts so the handler can decide whether the sample is large enough
    to draw a verdict from.

    Returns:
      {
        "new_median_usd": float | None,
        "used_1_2yr_median_usd": float | None,
        "new_count": int,
        "used_count": int,
        "as_of": ISO-8601 timestamp,
      }
    or None on any failure (no API key, network error, malformed payload,
    both sides empty).

    Honors TEST_MODE.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info(
            "TEST_MODE: skipping auto.dev fetch for make=%s model=%s zip=%s",
            make,
            model,
            zip_code,
        )
        return None

    api_key = _resolve_api_key()
    if not api_key:
        logger.warning(
            "auto.dev API key not configured (set AUTODEV_API_KEY or "
            "AUTODEV_API_KEY_SECRET_ARN); skipping fetch for %s %s",
            make,
            model,
        )
        return None

    if not make or not model or not zip_code:
        logger.info("auto.dev: missing make/model/zip — skipping fetch")
        return None

    current_year = datetime.now(UTC).year

    # "New" side — current and prior model-year examples flagged retail-new.
    new_median, new_count = _query_side(
        api_key=api_key,
        zip_code=zip_code,
        radius_miles=radius_miles,
        make=make,
        model=model,
        year_min=current_year - 1,
        year_max=current_year,
        used=False,
    )

    # "Used 1-2yr" side — examples 1-2 model years older flagged retail-used.
    used_median, used_count = _query_side(
        api_key=api_key,
        zip_code=zip_code,
        radius_miles=radius_miles,
        make=make,
        model=model,
        year_min=current_year - 2,
        year_max=current_year - 1,
        used=True,
    )

    # Both sides empty (e.g. obscure make/model + small radius) = nothing
    # to compute. Return None so the handler suppresses the callout entirely
    # rather than render a half-populated payload.
    if new_median is None and used_median is None:
        logger.info(
            "auto.dev: both new and used sides empty for %s %s zip=%s",
            make,
            model,
            zip_code,
        )
        return None

    return {
        "new_median_usd": new_median,
        "used_1_2yr_median_usd": used_median,
        "new_count": new_count,
        "used_count": used_count,
        "as_of": datetime.now(UTC).isoformat(),
    }


# ---- per-side query ----


def _query_side(
    *,
    api_key: str,
    zip_code: str,
    radius_miles: int,
    make: str,
    model: str,
    year_min: int,
    year_max: int,
    used: bool,
) -> tuple[float | None, int]:
    """Pull one side of the comparison (new OR used).

    Returns `(median_price, count)`. On any failure returns `(None, 0)` so
    the caller can still surface the other side if it succeeded.
    """
    params: dict[str, Any] = {
        "apikey": api_key,
        "vehicle.make": make,
        "vehicle.model": model,
        "vehicle.year": f"{year_min}-{year_max}",
        "retailListing.used": "true" if used else "false",
        "zip": zip_code,
        "distance": radius_miles,
        "limit": _MAX_LISTINGS_PER_QUERY,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(AUTODEV_LISTINGS_URL, params=params)
    except httpx.HTTPError as exc:
        logger.warning(
            "auto.dev %s %s (used=%s) fetch failed: %s",
            make,
            model,
            used,
            exc,
        )
        return None, 0

    if response.status_code != 200:
        logger.warning(
            "auto.dev %s %s (used=%s) returned HTTP %d: %s",
            make,
            model,
            used,
            response.status_code,
            response.text[:200],
        )
        return None, 0

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning(
            "auto.dev %s %s (used=%s) JSON parse failed: %s",
            make,
            model,
            used,
            exc,
        )
        return None, 0

    prices = _extract_prices(payload)
    if not prices:
        return None, 0

    return float(median(prices)), len(prices)


def _extract_prices(payload: Any) -> list[float]:
    """Pluck reasonable USD prices from an auto.dev `/listings` payload.

    auto.dev returns `{records: [...], hits: N}` shape; each record holds a
    `priceUnformatted` or `price` field. We accept either, coerce to float,
    and drop anything outside the sanity bounds (a `$1` placeholder or a
    `$2,000,000` mis-mapped MSRP would poison the median).
    """
    if not isinstance(payload, dict):
        return []
    records = payload.get("records") or payload.get("listings") or []
    if not isinstance(records, list):
        return []

    out: list[float] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        price = _coerce_price(
            rec.get("priceUnformatted") or rec.get("price") or rec.get("selling_price")
        )
        if price is None:
            continue
        if price < _MIN_REASONABLE_PRICE_USD or price > _MAX_REASONABLE_PRICE_USD:
            continue
        out.append(price)
    return out


def _coerce_price(raw: Any) -> float | None:
    """Best-effort coerce an auto.dev price field to a float USD value.

    `priceUnformatted` is typically a numeric; `price` is a string like
    `"$32,450"`. Returns None if no plausible number can be extracted.
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        cleaned = raw.replace("$", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


# ---- API key resolution ----


def _resolve_api_key() -> str:
    """Resolve the auto.dev API key.

    Precedence (mirrors `fred._resolve_api_key` / `afdc._resolve_api_key`):
      1. AUTODEV_API_KEY env var (local/dev convenience).
      2. AUTODEV_API_KEY_SECRET_ARN env -> Secrets Manager (Lambda).
    """
    direct = os.environ.get("AUTODEV_API_KEY", "").strip()
    if direct:
        return direct

    secret_arn = os.environ.get("AUTODEV_API_KEY_SECRET_ARN", "").strip()
    if secret_arn:
        return _get_secret(secret_arn).get("api_key", "")

    return ""


@lru_cache(maxsize=4)
def _get_secret(secret_arn: str) -> dict[str, str]:
    """Fetch a JSON secret from Secrets Manager (cached per cold start).

    Returns `{}` on any failure — missing secret, missing SecretString,
    malformed JSON. Mirrors the resilience pattern in
    `src.tools.external.afdc._get_secret`: a not-yet-seeded placeholder
    must not raise out of the client.
    """
    import boto3

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_arn)
    except Exception:
        logger.warning("auto.dev secret unreachable; treating as unconfigured", exc_info=True)
        return {}

    secret_string = response.get("SecretString") or ""
    if not secret_string.strip():
        return {}
    try:
        return json.loads(secret_string)
    except json.JSONDecodeError:
        logger.warning("auto.dev secret value isn't JSON; treating as unconfigured")
        return {}
