"""Used-vs-new arbitrage handler — backs `GET /me/used-vs-new-arbitrage`.

Surfaces the new-vs-used price spread for the user's primary make/model as
one of the supporting metric pills around the market-trend chart. The pill
fires only in two editorially-interesting states — either the used-to-new
gap has collapsed (verdict="tight" → "new captures unusual value") or it
has widened sharply (verdict="wide" → "used is unusually cheap"). The
in-between case returns None so the dashboard isn't perpetually noisy.

Resilience contract: read-only handler, never raises. Pref-resolution
failures, missing primary brand, auto.dev failures, and insufficient
comp counts all degrade to `None` — the frontend hides the pill rather
than rendering a placeholder.

Caching: results are persisted to `macro_series_store` under the series
key `arbitrage:<user_id>:<make>:<model>` with a 24h TTL applied at read
time. We reuse the macro-series store instead of standing up a dedicated
arbitrage cache table — the shape (one row per key, latest read, lazy
refresh) is identical to how the AFDC handler reuses it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.memory import macro_series_store
from src.tools.external import auto_dev

logger = logging.getLogger(__name__)

# Cache TTL — auto.dev's free tier is 1000 calls/mo and the median moves
# slowly within a day; we burn at most 2 calls per user per day.
_CACHE_TTL = timedelta(hours=24)

# Verdict thresholds. Stated in both absolute USD and percent-of-new terms
# because either alone is wrong at the extremes — a $3k gap on a $33k car
# is meaningful; a $3k gap on a $90k car is rounding noise.
_TIGHT_SPREAD_USD = 3_000
_TIGHT_SPREAD_PCT = 8.0
_WIDE_SPREAD_USD = 8_000
_WIDE_SPREAD_PCT = 18.0

# Below this we don't trust the median — auto.dev returned too few
# comparable listings to make a confident call. Suppress the pill.
_MIN_COMP_COUNT = 5

# Default model used when a user has set `included_brands` but hasn't
# pinned a specific model. Picked per brand — keep it to a single popular
# EV model per brand so the headline comp matches what the user is
# actually shopping. Brands not in the map fall through to None (no pill).
_DEFAULT_MODEL_BY_BRAND: dict[str, str] = {
    "tesla": "Model Y",
    "hyundai": "Ioniq 5",
    "kia": "EV6",
    "ford": "Mustang Mach-E",
    "chevrolet": "Blazer EV",
    "chevy": "Blazer EV",
    "volkswagen": "ID.4",
    "vw": "ID.4",
    "rivian": "R1S",
    "polestar": "Polestar 2",
    "nissan": "Ariya",
    "toyota": "bZ4X",
    "subaru": "Solterra",
    "audi": "Q4 e-tron",
    "bmw": "i4",
    "mercedes": "EQE",
    "mercedes-benz": "EQE",
    "lucid": "Air",
    "cadillac": "Lyriq",
    "genesis": "GV60",
    "honda": "Prologue",
    "acura": "ZDX",
    "lexus": "RZ",
    "mazda": "CX-50",
    "volvo": "EX30",
}


def get_used_vs_new_arbitrage(user_id: str) -> dict[str, Any] | None:
    """Compute the new-vs-used price spread for the user's primary make/model.

    Returns the payload described in the route contract when a tight/wide
    verdict is reached, or None when:
      - the user has no primary brand set,
      - no default model can be inferred for that brand,
      - auto.dev returned no usable comps (or failed),
      - either side has fewer than `_MIN_COMP_COUNT` listings, or
      - the spread falls in the boring middle (verdict="normal").

    Never raises.
    """
    prefs = _load_prefs_safe(user_id)
    if prefs is None:
        return None

    primary_brand = _primary_brand(prefs)
    if not primary_brand:
        # No primary brand on file — can't compute a meaningful single-model
        # spread. Returning None (rather than averaging across the segment)
        # avoids surfacing a number we can't defend.
        return None

    primary_model = _default_model_for_brand(primary_brand)
    if not primary_model:
        logger.info("used-vs-new: no default model for brand=%s; skipping", primary_brand)
        return None

    zip_code = _safe_attr(prefs, "search", "location_zip") or ""
    radius_miles = _safe_attr(prefs, "search", "radius_miles") or 50
    if not zip_code:
        return None

    cache_key = f"arbitrage:{user_id}:{primary_brand}:{primary_model}".lower()
    cached = _read_fresh_cache(cache_key)
    comps: dict[str, Any] | None
    if cached is not None:
        comps = cached
    else:
        try:
            comps = auto_dev.fetch_make_model_comps(
                zip_code=zip_code,
                radius_miles=int(radius_miles),
                make=primary_brand,
                model=primary_model,
            )
        except Exception:
            logger.exception(
                "auto.dev fetch raised for user=%s make=%s model=%s",
                user_id,
                primary_brand,
                primary_model,
            )
            comps = None
        if comps is not None:
            _write_cache(cache_key, comps)

    if comps is None:
        return None

    return _build_payload(
        comps=comps,
        make=primary_brand,
        model=primary_model,
    )


# ---- payload composition ----


def _build_payload(*, comps: dict[str, Any], make: str, model: str) -> dict[str, Any] | None:
    """Apply verdict thresholds and project the wire shape, or None on suppress."""
    new_median = comps.get("new_median_usd")
    used_median = comps.get("used_1_2yr_median_usd")
    new_count = int(comps.get("new_count") or 0)
    used_count = int(comps.get("used_count") or 0)
    as_of = comps.get("as_of") or datetime.now(UTC).isoformat()

    if not isinstance(new_median, (int, float)) or not isinstance(used_median, (int, float)):
        return None
    if new_median <= 0:
        return None
    if new_count < _MIN_COMP_COUNT or used_count < _MIN_COMP_COUNT:
        logger.info(
            "used-vs-new: insufficient comps (new=%d, used=%d) for %s %s",
            new_count,
            used_count,
            make,
            model,
        )
        return None

    spread_usd = float(new_median) - float(used_median)
    spread_pct = (spread_usd / float(new_median)) * 100.0

    verdict = _classify(spread_usd=spread_usd, spread_pct=spread_pct)
    if verdict == "normal":
        return None

    return {
        "spread_pct": round(spread_pct, 1),
        "spread_usd": round(spread_usd),
        "median_new_usd": round(float(new_median)),
        "median_used_usd": round(float(used_median)),
        "label_short": "Used 1-2yr",
        "verdict": verdict,
        "as_of": as_of,
    }


def _classify(*, spread_usd: float, spread_pct: float) -> str:
    """Bucket the spread into `tight` / `wide` / `normal`.

    "tight"  — used is within $3k of new (or within 8% by ratio). UI surfaces
               this as "new captures unusual value right now."
    "wide"   — used is >$8k below new (or >18% by ratio). UI surfaces this
               as "used is unusually cheap."
    "normal" — the boring middle; pill suppressed.

    A negative spread (used selling *higher* than new, which happens in
    constrained-supply moments) buckets as "tight" too — the editorial
    framing still applies ("new captures unusual value relative to used").
    """
    if spread_usd <= _TIGHT_SPREAD_USD or spread_pct <= _TIGHT_SPREAD_PCT:
        return "tight"
    if spread_usd >= _WIDE_SPREAD_USD or spread_pct >= _WIDE_SPREAD_PCT:
        return "wide"
    return "normal"


# ---- prefs / brand resolution ----


def _load_prefs_safe(user_id: str) -> Any:
    """Load merged preferences; return None on any failure."""
    try:
        from src.config.loader import load_preferences

        return load_preferences(user_id=user_id)
    except Exception:
        logger.exception("Failed to load preferences for user_id=%s", user_id)
        return None


def _primary_brand(prefs: Any) -> str | None:
    """First entry in `included_brands`, normalized to title case.

    Returns None when the user has no whitelist — we can't compute a
    meaningful spread across all brands without averaging away the
    signal entirely.
    """
    included = _safe_attr(prefs, "included_brands") or []
    if not isinstance(included, list) or not included:
        return None
    first = included[0]
    if not isinstance(first, str) or not first.strip():
        return None
    return first.strip()


def _default_model_for_brand(brand: str) -> str | None:
    """Pick the canonical EV model for the user's primary brand.

    Returns None when the brand isn't in the lookup — a niche brand with
    no clear single-model headline shouldn't produce a confidently wrong
    comp.
    """
    return _DEFAULT_MODEL_BY_BRAND.get(brand.strip().lower())


def _safe_attr(obj: Any, *path: str) -> Any:
    """Walk attribute path defensively. Returns None if anything is missing."""
    cur = obj
    for name in path:
        if cur is None:
            return None
        cur = getattr(cur, name, None)
    return cur


# ---- cache layer ----


def _read_fresh_cache(cache_key: str) -> dict[str, Any] | None:
    """Return the cached comps dict if it's within the 24h TTL, else None."""
    try:
        row = macro_series_store.get_latest(cache_key)
    except Exception:
        logger.exception("arbitrage cache read failed for %s", cache_key)
        return None
    if not row:
        return None

    fetched_at = row.get("fetched_at")
    if not isinstance(fetched_at, str):
        return None
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    if datetime.now(UTC) - ts > _CACHE_TTL:
        return None

    meta = row.get("provider_metadata") or {}
    if not isinstance(meta, dict):
        return None
    comps = meta.get("comps")
    if not isinstance(comps, dict):
        return None
    return comps


def _write_cache(cache_key: str, comps: dict[str, Any]) -> None:
    """Persist comps under the macro-series store. Never raises."""
    try:
        macro_series_store.write_observation(
            series_key=cache_key,
            timestamp=datetime.now(UTC).isoformat(),
            value=0.0,
            provider_metadata={"comps": comps},
        )
    except Exception:
        logger.exception("arbitrage cache write failed for %s", cache_key)
