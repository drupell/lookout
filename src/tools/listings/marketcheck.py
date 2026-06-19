"""MarketCheck API client — fetches vehicle listings from MarketCheck's inventory API.

API docs: https://apidocs.marketcheck.com/
Free tier: 500 calls/month. Single GET per fetch_listings() call.

API key is read from:
    1. MARKETCHECK_API_KEY env var (for local/dev)
    2. Fallback: MARKETCHECK_API_KEY_SECRET_ARN -> Secrets Manager (for Lambda)

Fuel types: MarketCheck uses values like "Electric", "Hybrid", "Plug-in Hybrid",
"Gasoline", "Diesel", "Flex Fuel", "Hydrogen". Pass an explicit list to filter.
Pure BEVs only = ["Electric"]. To include PHEVs = ["Electric", "Plug-in Hybrid"].
None or empty list = no fuel-type filter (all vehicles).

MarketCheck Terms of Use constraints (do not violate):
  - Do NOT train AI models or build embeddings on MarketCheck data.
  - Do NOT extract/recreate large portions of the dataset.
  - Do NOT use to identify targets for scraping.
  - Sending listings to an LLM for single-shot inference (scoring/drafting) is
    fine — that's not training. Persisting minimal metadata for operational
    dedup/audit is fine. Building a long-term dataset is not.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MARKETCHECK_BASE_URL = "https://mc-api.marketcheck.com/v2/search/car/active"


class MarketCheckQuotaExhausted(RuntimeError):  # noqa: N818 - matches the source_errors code name; renaming would cascade across handlers and tests
    """Distinct from generic rate-limiting: the monthly call budget is gone.

    Mapped to `source_errors=["marketcheck:quota_exhausted"]` so the UI says
    "monthly quota reached — resumes next month" instead of the generic
    "rate-limited" wording — which is the right call when the next user
    run this month will fail too.
    """


class MarketCheckPaginationExceeded(RuntimeError):  # noqa: N818 - matches the source_errors code name; renaming would cascade across handlers and tests
    """Free-tier hard limit: MarketCheck refuses `start >= 500`.

    The free plan only paginates within the first 500 rows total. Requesting
    page 2 (`start=500`) trips a 422. Mapped to a distinct error code so the
    UI surfaces "drop max_pages to 1 or upgrade your MarketCheck plan"
    rather than a generic "unavailable" — the user's action is specific.
    """


# MarketCheck's per-page maximum is 500 rows on the free tier. Pulling the full
# page per call means one API call returns 10x the data we used to get with
# rows=50 — same call cost, vastly more candidates to filter from. The actual
# count of rows we *retain* is still bounded by `target_count` (the caller's
# cap), so LLM scoring downstream is unaffected unless the caller raises it.
PAGE_SIZE = 500
DEFAULT_FUEL_TYPES = ["Electric"]  # pure BEVs by default

# MarketCheck free-tier monthly call limit. Applied as a pre-flight check on
# the shared default-tier bucket so users get a clean "quota reached" message
# instead of a confusing 429 once we've actually exhausted the budget.
# BYOK keys may be on a paid tier with a higher quota — we don't know their
# plan, so we don't pre-check those buckets; let MarketCheck's own limits speak.
SHARED_TIER_MONTHLY_LIMIT = 500


class MarketCheckClient:
    """Fetches vehicle listings from MarketCheck's inventory API.

    Supports adaptive pagination: keeps fetching pages until target_count useful
    listings (after filters) are collected, OR until max_pages or num_found is
    exhausted. This way blacklisted dealers/makes don't waste output slots —
    just API quota up to the page cap.
    """

    name = "marketcheck"

    # Cap how long we'll throttle on a single ratelimit window. Even if the
    # API tells us to wait an hour, Lambda has an execution budget and the
    # user is staring at a progress bar — fail fast above this and let the
    # caller surface a clear "rate-limited" error.
    _MAX_WAIT_SECONDS: float = 10.0
    _RETRY_BUFFER_SECONDS: float = 0.1

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        *,
        secret_arn_override: str | None = None,
        api_key_id: str | None = None,
    ) -> None:
        # Precedence: explicit api_key arg > BYOK per-user secret > env > shared secret.
        # Per-user secrets land via secret_arn_override; the shared default-tier
        # secret is read by _resolve_api_key() from env.
        self._api_key = api_key or _resolve_api_key(secret_arn_override=secret_arn_override)
        self._timeout = timeout
        # Epoch seconds before which the next call MUST sleep — armed when a
        # response reports `ratelimit-remaining: 0` so we don't fire the next
        # page into a guaranteed 429.
        self._next_call_after: float | None = None
        # Quota-tracking bucket identifier (e.g. "shared" or "byok:<user_id>")
        # for the api_usage_store. None = don't track (used by tests and
        # ad-hoc callers); production callers should always pass this so the
        # dashboard can surface "X/500 calls this month" honestly.
        self._api_key_id = api_key_id

    def fetch_listings(
        self,
        zip_code: str,
        radius_miles: int,
        max_vehicle_age_years: int,
        *,
        fuel_types: list[str] | None = None,
        included_brands: list[str] | None = None,
        excluded_brands: list[str] | None = None,
        min_price_usd: float = 0,
        max_price_usd: float = 0,
        target_count: int = 200,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Fetch listings with adaptive pagination + filtering.

        Args:
            zip_code: Search center.
            radius_miles: Search radius.
            max_vehicle_age_years: Vehicles with year >= (current - N).
            fuel_types: Canonical fuel types to include. Empty/None → all.
            included_brands: Whitelist (case-insensitive). Empty → all.
                Pushed to MarketCheck's `make` param to save API quota.
            excluded_brands: Blacklist (case-insensitive). Always wins over
                included_brands. Applied client-side.
            min_price_usd / max_price_usd: 0 = no bound. Pushed to API.
            target_count: Stop once we have this many useful listings.
            max_pages: Hard cap on API calls per fetch.
        """
        if not self._api_key:
            raise RuntimeError(
                "MarketCheck API key not configured. "
                "Set MARKETCHECK_API_KEY env var or MARKETCHECK_API_KEY_SECRET_ARN."
            )

        if fuel_types is None:
            fuel_types = DEFAULT_FUEL_TYPES
        included_brands = included_brands or []
        excluded_brands = excluded_brands or []

        included_lower = {b.lower() for b in included_brands}
        excluded_lower = {b.lower() for b in excluded_brands}

        current_year = datetime.now(UTC).year
        min_year = current_year - max_vehicle_age_years

        base_params: dict[str, Any] = {
            "api_key": self._api_key,
            "zip": zip_code,
            "radius": radius_miles,
            "year_min": min_year,
            "year_max": current_year,
            "rows": PAGE_SIZE,
        }
        if fuel_types:
            base_params["fuel_type"] = ",".join(fuel_types)
        if included_brands:
            base_params["make"] = ",".join(included_brands)
        if min_price_usd or max_price_usd:
            lo = int(min_price_usd) if min_price_usd else 0
            hi = int(max_price_usd) if max_price_usd else 9_999_999
            base_params["price_range"] = f"{lo}-{hi}"

        logger.info(
            "MarketCheck search: zip=%s radius=%d years=%d-%d fuel=%s "
            "include=%s exclude=%s price=%s-%s target=%d max_pages=%d",
            zip_code,
            radius_miles,
            min_year,
            current_year,
            fuel_types or "any",
            included_brands or "any",
            excluded_brands or "none",
            min_price_usd or "any",
            max_price_usd or "any",
            target_count,
            max_pages,
        )

        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        api_calls = 0
        num_found = 0

        # Pre-flight monthly-quota check (shared default tier only).
        # If we've already spent this month's bucket, raise QuotaExhausted
        # before we even hit the network — the row's source_errors will
        # surface "monthly quota reached", which is the actionable message,
        # not the misleading "rate-limited" that a 429 would produce.
        self._check_monthly_quota_or_raise()

        with httpx.Client(timeout=self._timeout) as client:
            for page in range(max_pages):
                params = {**base_params, "start": page * PAGE_SIZE}
                response = self._request_with_retry(client, params)
                api_calls += 1
                # Increment the per-key counter on every attempt (success or
                # 4xx — either way we burned a call against MarketCheck's
                # monthly budget). Safe in TEST_MODE — the store short-circuits.
                self._record_call()

                if response.status_code == 401:
                    raise RuntimeError("MarketCheck auth failed — check API key")
                if response.status_code == 429:
                    self._log_429(response)
                    raise RuntimeError(
                        "MarketCheck returned 429 (rate limit) — try again shortly "
                        "or check your account's quota in the MarketCheck portal"
                    )
                # 422 specifically on a `start >= 500` request is the free-tier
                # pagination ceiling: anything past row 500 is structurally
                # rejected. Distinct from a generic 4xx so the UI can guide
                # the user to drop max_pages rather than guessing at the
                # cause.
                if response.status_code == 422 and params.get("start", 0) >= 500:
                    raise MarketCheckPaginationExceeded(
                        "MarketCheck free-tier 500-row pagination ceiling hit — "
                        "drop max_pages to 1 or upgrade your MarketCheck plan"
                    )
                response.raise_for_status()

                data = response.json()
                raw_listings = data.get("listings", []) or []
                num_found = data.get("num_found", num_found)

                if not raw_listings:
                    break  # API exhausted

                # Parse + filter this page
                page_useful = 0
                for raw in raw_listings:
                    try:
                        lst = _parse_listing(raw)
                    except Exception:
                        logger.debug("Skipping malformed listing", exc_info=True)
                        continue
                    if not lst:
                        continue
                    if not _passes_filters(
                        lst,
                        fuel_types=fuel_types,
                        included_lower=included_lower,
                        excluded_lower=excluded_lower,
                        min_price=min_price_usd,
                        max_price=max_price_usd,
                        min_year=min_year,
                        max_year=current_year,
                    ):
                        continue
                    if lst["listing_id"] in seen_ids:
                        continue
                    seen_ids.add(lst["listing_id"])
                    collected.append(lst)
                    page_useful += 1
                    if len(collected) >= target_count:
                        break

                logger.info(
                    "MarketCheck page %d: %d raw, %d useful (collected %d/%d, "
                    "api_calls=%d, num_found=%d)",
                    page + 1,
                    len(raw_listings),
                    page_useful,
                    len(collected),
                    target_count,
                    api_calls,
                    num_found,
                )

                if len(collected) >= target_count:
                    break
                # Stop if we've fetched everything available
                if (page + 1) * PAGE_SIZE >= num_found:
                    break

        logger.info(
            "MarketCheck done: %d listings collected, %d API calls, %d total available",
            len(collected),
            api_calls,
            num_found,
        )
        return collected

    # ---- Quota tracking (per-key monthly call counter) ----

    def _check_monthly_quota_or_raise(self) -> None:
        """Pre-flight: raise MarketCheckQuotaExhausted if the shared bucket is full.

        Only applied to the default-tier shared key (`api_key_id == "shared"`).
        BYOK users may be on a paid tier with higher quotas — we don't know
        their plan, so we let MarketCheck's own rate limiter speak for their
        calls instead of guessing.
        """
        if self._api_key_id != "shared":
            return
        # Avoid importing the store at module load to keep this file
        # importable in environments without DynamoDB plumbing (used by some
        # ad-hoc local scripts). The store itself short-circuits in TEST_MODE.
        from src.memory import api_usage_store

        usage = api_usage_store.get_usage(self._api_key_id)
        if usage.get("calls", 0) >= SHARED_TIER_MONTHLY_LIMIT:
            raise MarketCheckQuotaExhausted(
                "MarketCheck monthly quota reached — runs resume next month"
            )

    def _record_call(self) -> None:
        """Increment this key's monthly counter. Called once per API attempt."""
        if self._api_key_id is None:
            return
        try:
            from src.memory import api_usage_store

            api_usage_store.increment_calls(self._api_key_id)
        except Exception:
            # Never let observability bookkeeping break the agent run. A
            # missed counter increment shows up as "we undercount this user's
            # usage by 1" — visible drift, not a broken run.
            logger.exception("api_usage_store increment failed; continuing")

    # ---- Rate-limit handling ----

    def _request_with_retry(self, client: httpx.Client, params: dict[str, Any]) -> httpx.Response:
        """Call MarketCheck with proactive throttling + a single 429 retry.

        Pre-call: if the previous response said `ratelimit-remaining: 0`, sleep
        until the reset time before firing.

        Post-call: if we get a 429, parse `retry-after` (or fall back to a
        default), sleep, and retry exactly once. A second 429 propagates up
        with full headers logged so the caller can decide what to do.
        """
        self._wait_until_ready()
        response = client.get(MARKETCHECK_BASE_URL, params=params)
        self._record_rate_limit_state(response)

        if response.status_code != 429:
            return response

        wait = self._parse_retry_after(response, default=1.0)
        # Always wait at least the buffer; cap at our budget.
        wait = min(max(wait, 0.5), self._MAX_WAIT_SECONDS)
        logger.info("MarketCheck 429 — waiting %.2fs and retrying once", wait)
        time.sleep(wait + self._RETRY_BUFFER_SECONDS)

        response = client.get(MARKETCHECK_BASE_URL, params=params)
        self._record_rate_limit_state(response)
        return response

    def _wait_until_ready(self) -> None:
        """Block until the cool-off armed by the last response has expired."""
        if self._next_call_after is None:
            return
        delay = self._next_call_after - time.time()
        if delay > 0:
            delay = min(delay, self._MAX_WAIT_SECONDS)
            logger.info("MarketCheck throttle: sleeping %.2fs before next call", delay)
            time.sleep(delay)
        self._next_call_after = None

    def _record_rate_limit_state(self, response: httpx.Response) -> None:
        """Read ratelimit headers; arm cool-off when remaining == 0."""
        try:
            remaining = int(response.headers.get("ratelimit-remaining", "1"))
        except (TypeError, ValueError):
            remaining = 1
        if remaining > 0:
            self._next_call_after = None
            return
        # Bucket exhausted; figure out when it resets.
        wait = self._parse_retry_after(response, default=1.0)
        self._next_call_after = time.time() + wait + self._RETRY_BUFFER_SECONDS

    @staticmethod
    def _parse_retry_after(response: httpx.Response, *, default: float) -> float:
        """Extract a retry-after delay (seconds) from response headers.

        Honors RFC 7231 `Retry-After` (delta-seconds), and MarketCheck's
        `ratelimit-reset-time` ("YYYY-MM-DD HH:MM:SS UTC"). Falls back to the
        default on parse failure rather than risk a silent infinite wait.
        """
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass

        reset_time = response.headers.get("ratelimit-reset-time")
        if reset_time:
            try:
                cleaned = reset_time.replace(" UTC", "").strip()
                reset_dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                delta = reset_dt.timestamp() - time.time()
                if delta > 0:
                    return delta
            except ValueError:
                pass

        return default

    @staticmethod
    def _log_429(response: httpx.Response) -> None:
        """Log the rate-limit headers + body preview when we give up on a 429."""
        rate_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower().startswith(("x-ratelimit", "ratelimit", "retry-after"))
        }
        body_preview = response.text[:500] if response.text else ""
        logger.warning(
            "MarketCheck 429 — headers=%s body=%r",
            rate_headers,
            body_preview,
        )


def _passes_filters(
    lst: dict[str, Any],
    *,
    fuel_types: list[str],
    included_lower: set[str],
    excluded_lower: set[str],
    min_price: float,
    max_price: float,
    min_year: int,
    max_year: int,
) -> bool:
    """Apply all client-side filters. Returns True if listing passes."""
    # Fuel type (defense — API also filters)
    if fuel_types and (lst.get("fuel_type") or "").lower() not in {ft.lower() for ft in fuel_types}:
        return False
    # Year (defense — API also filters)
    year = int(lst.get("year") or 0)
    if not (min_year <= year <= max_year):
        return False
    # Brand whitelist (defense — API also filters when set)
    make_lower = (lst.get("make") or "").lower()
    if included_lower and make_lower not in included_lower:
        return False
    # Brand blacklist (excluded always wins)
    if make_lower in excluded_lower:
        return False
    # Price bounds
    price = float(lst.get("selling_price") or 0)
    if min_price and price < min_price:
        return False
    return not (max_price and price > max_price)


def _parse_listing(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map a MarketCheck listing record to the standard listing shape.

    Returns None if required fields are missing.
    """
    build = raw.get("build", {}) or {}
    dealer = raw.get("dealer", {}) or {}

    make = build.get("make")
    model = build.get("model")
    year = build.get("year")
    selling_price = raw.get("price")

    if not (make and model and year and selling_price):
        return None

    msrp = raw.get("msrp") or build.get("msrp") or None
    vin = raw.get("vin") or ""
    dealer_name = dealer.get("name", "Unknown Dealer")
    distance = float(raw.get("dist", 0) or dealer.get("distance_miles", 0) or 0)
    miles = int(raw.get("miles") or 0)
    url = raw.get("vdp_url", "") or raw.get("source", "")
    powertrain_type = (build.get("powertrain_type") or "").upper()
    raw_fuel_type = build.get("fuel_type") or ""
    cylinders = int(build.get("cylinders") or 0)
    body_type = build.get("body_type") or build.get("vehicle_type") or ""
    trim = build.get("trim") or ""

    # Determine canonical fuel_type. MarketCheck's `fuel_type` is unreliable
    # (e.g. labels PHEVs like the Jeep 4xe as "Electric"). Prefer powertrain_type
    # when present; fall back to fuel_type + cylinder check otherwise.
    fuel_type = _canonical_fuel_type(raw_fuel_type, powertrain_type, cylinders)

    # Stable ID: prefer VIN; fall back to source+price hash
    if vin:
        listing_id = f"mc:{vin}"
    else:
        raw_key = f"mc:{make}:{model}:{year}:{selling_price}:{dealer_name}"
        listing_id = f"mc:{hashlib.sha256(raw_key.encode()).hexdigest()[:16]}"

    return {
        "listing_id": listing_id,
        "make": str(make),
        "model": str(model),
        "trim": str(trim),
        "year": int(year),
        "mileage": miles,
        "msrp": float(msrp) if msrp else None,
        "selling_price": float(selling_price),
        "effective_apr": None,  # MarketCheck doesn't expose financing terms
        "lease_monthly": None,
        "dealer_name": dealer_name,
        "dealer_distance_miles": distance,
        "url": url,
        "fuel_type": fuel_type,
        "powertrain_type": powertrain_type,
        "cylinders": cylinders,
        "body_type": body_type,
        "is_ev": fuel_type.lower() == "electric",
        "source": "marketcheck",
    }


def _canonical_fuel_type(raw_fuel_type: str, powertrain_type: str, cylinders: int) -> str:
    """Return the canonical fuel_type, correcting MarketCheck misclassifications.

    Priority:
        1. powertrain_type (authoritative when present): BEV/PHEV/HEV/FCEV/ICE
        2. raw fuel_type + cylinder check: if "Electric" but cylinders > 0,
           it's a PHEV mislabeled (BEVs have 0 cylinders)
        3. raw fuel_type as-is
    """
    pt = powertrain_type.upper()
    if pt == "BEV":
        return "Electric"
    if pt == "PHEV":
        return "Plug-in Hybrid"
    if pt == "HEV":
        return "Hybrid"
    if pt == "FCEV":
        return "Hydrogen"
    if pt == "ICE":
        # ICE vehicles — trust raw fuel_type (Gasoline, Diesel, Flex Fuel)
        return raw_fuel_type

    # No powertrain_type — use cylinders as a tiebreaker
    if raw_fuel_type.lower() == "electric" and cylinders > 0:
        return "Plug-in Hybrid"
    return raw_fuel_type


def _resolve_api_key(*, secret_arn_override: str | None = None) -> str:
    """Resolve the API key.

    Precedence:
      1. `secret_arn_override` (per-user BYOK secret) — when present.
      2. MARKETCHECK_API_KEY env var (local/dev convenience).
      3. MARKETCHECK_API_KEY_SECRET_ARN env (shared default-tier secret).
    """
    if secret_arn_override:
        key = _get_secret(secret_arn_override).get("api_key", "")
        if key:
            return key
        # Fall through if the override secret was empty — better to try the
        # shared key than to return "" and silently 401 every request.

    direct = os.environ.get("MARKETCHECK_API_KEY", "").strip()
    if direct:
        return direct

    secret_arn = os.environ.get("MARKETCHECK_API_KEY_SECRET_ARN", "").strip()
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
