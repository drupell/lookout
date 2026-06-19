"""Incentive-stack handler — backs `GET /me/incentives`.

Composes the user's qualified federal + state + utility incentive stack from:
  1. Cached AFDC payload (federal + state) in `macro_series_store`.
  2. Hand-curated stackability matrix at `src/data/incentive_stackability.yaml`.
  3. Federal Section 30D eligibility from `fed_credit_eligibility`.

Auto-hide: when the user's `prefs.search.fuel_types` doesn't include any
EV/PHEV value, the whole stack is empty with `hidden_reason="non-ev-prefs"`
— the frontend can choose to skip rendering the card altogether.

Warnings: any program with an `expiration_date` within ~6 weeks raises a
warning string on the response. Frontend renders these inline under the
stack.

Resilience: AFDC unavailability falls through to "no data" rather than
500-ing. The handler is read-only and must never raise; per-row validation
errors are skipped silently.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.memory import macro_series_store
from src.tools.external import fed_credit_eligibility

logger = logging.getLogger(__name__)

# A rough zip-prefix-to-state map covering the EV-adoption states the
# stackability matrix encodes. Not exhaustive — the long tail returns
# "UNKNOWN" (which skips state programs but still surfaces federal).
# Prefix lookup is cheap and avoids hauling in a 40k-row zipcode dataset.
_ZIP_PREFIX_STATE: dict[str, str] = {
    # Massachusetts: 010-027
    **{f"0{i:02d}": "MA" for i in range(10, 28)},
    # Rhode Island: 028-029
    "028": "RI",
    "029": "RI",
    # New Hampshire: 030-038
    **{f"0{i:02d}": "NH" for i in range(30, 39)},
    # Maine: 039-049
    **{f"0{i:02d}": "ME" for i in range(39, 50)},
    # Vermont: 050-059
    **{f"0{i:02d}": "VT" for i in range(50, 60)},
    # Connecticut: 060-069
    **{f"0{i:02d}": "CT" for i in range(60, 70)},
    # New Jersey: 070-089
    **{f"0{i:02d}": "NJ" for i in range(70, 90)},
    # New York: 100-149 (covers NYC + upstate)
    **{f"{i:03d}": "NY" for i in range(100, 150)},
    # Pennsylvania: 150-196
    **{f"{i:03d}": "PA" for i in range(150, 197)},
    # Delaware: 197-199
    "197": "DE",
    "198": "DE",
    "199": "DE",
    # Washington DC: 200, 202-205
    "200": "DC",
    "202": "DC",
    "203": "DC",
    "204": "DC",
    "205": "DC",
    # Virginia: 201, 220-246
    "201": "VA",
    **{f"{i:03d}": "VA" for i in range(220, 247)},
    # Maryland: 206-219
    **{f"{i:03d}": "MD" for i in range(206, 220)},
    # North Carolina: 270-289
    **{f"{i:03d}": "NC" for i in range(270, 290)},
    # South Carolina: 290-299
    **{f"{i:03d}": "SC" for i in range(290, 300)},
    # Georgia: 300-319, 398-399
    **{f"{i:03d}": "GA" for i in range(300, 320)},
    # Florida: 320-349
    **{f"{i:03d}": "FL" for i in range(320, 350)},
    # Illinois: 600-629
    **{f"{i:03d}": "IL" for i in range(600, 630)},
    # Texas: 750-799
    **{f"{i:03d}": "TX" for i in range(750, 800)},
    # Colorado: 800-816
    **{f"{i:03d}": "CO" for i in range(800, 817)},
    # Washington: 980-994
    **{f"{i:03d}": "WA" for i in range(980, 995)},
    # Oregon: 970-979
    **{f"{i:03d}": "OR" for i in range(970, 980)},
    # California: 900-961
    **{f"{i:03d}": "CA" for i in range(900, 962)},
    # Arizona: 850-865
    **{f"{i:03d}": "AZ" for i in range(850, 866)},
    # Nevada: 889-898
    **{f"{i:03d}": "NV" for i in range(889, 899)},
}

# EV/PHEV indicators on the user's fuel_types pref. Case-insensitive set.
_EV_FUEL_TYPES = frozenset(
    {
        "electric",
        "plug-in hybrid",
        "phev",
        "bev",
        "hybrid electric",
    }
)

# Warn when a program expires within this window. The window is keyed by
# jurisdiction tier: federal sunsets are huge events and worth a wider lookahead
# (90d), state programs use the original 6-week default (42d), and utility
# programs change fastest so we keep their window narrow (21d) — fires only
# when the expiration is genuinely imminent.
_EXPIRATION_WARNING_WINDOW_BY_TIER: dict[str, timedelta] = {
    "federal": timedelta(days=90),
    "state": timedelta(days=42),
    "utility": timedelta(days=21),
}
# Fallback for any jurisdiction we haven't keyed (defensive — the loader
# normalizes to one of the three above).
_DEFAULT_WARNING_WINDOW = timedelta(days=42)

_STACKABILITY_PATH = Path(__file__).parent.parent / "data" / "incentive_stackability.yaml"


def get_incentives(user_id: str) -> dict[str, Any]:
    """Return the user's qualified federal + state + utility incentive stack.

    Empty/missing data degrades gracefully:
      - no zip resolved -> federal layer only (state="UNKNOWN")
      - non-EV prefs -> empty stack with hidden_reason="non-ev-prefs"
      - AFDC cache empty -> empty `items` list, no warnings

    Never raises — caller pattern is "show whatever we have."
    """
    prefs = _load_prefs_safe(user_id)
    if prefs is None:
        # We couldn't resolve prefs at all — return empty rather than 500.
        return {
            "stack": {"total_usd": 0, "items": []},
            "warnings": [],
            "hidden_reason": "preferences-unavailable",
            "state": "UNKNOWN",
        }

    fuel_types = _safe_attr(prefs, "search", "fuel_types") or []
    if not _wants_ev(fuel_types):
        return {
            "stack": {"total_usd": 0, "items": []},
            "warnings": [],
            "hidden_reason": "non-ev-prefs",
            "state": _state_from_prefs(prefs),
        }

    state_code = _state_from_prefs(prefs)
    matrix = _load_stackability()

    raw_programs: list[dict[str, Any]] = []
    raw_programs.extend(_load_programs("US"))
    if state_code != "UNKNOWN":
        raw_programs.extend(_load_programs(state_code))

    items = _project_items(raw_programs, matrix, prefs)
    # Dedupe by title — AFDC sometimes lists multiple tiers under the same
    # human-readable title (e.g. DE's separate new-BEV / new-PHEV / used-BEV
    # rows). For the headline-stack UX, those collapse to a single highest-
    # amount row to avoid the visual duplicate.
    items = _dedupe_by_title(items)
    total = sum(int(item.get("amount_usd") or 0) for item in items if item.get("stacks"))
    # If nothing stacks (rare — federal credit is the base), fall back to the
    # largest non-stacking single program so the headline isn't $0.
    if total == 0 and items:
        total = max((int(item.get("amount_usd") or 0) for item in items), default=0)

    warnings = _build_warnings(items)

    return {
        "stack": {
            "total_usd": total,
            "items": items,
        },
        "warnings": warnings,
        "state": state_code,
    }


# ---- prefs / state resolution ----


def _load_prefs_safe(user_id: str) -> Any:
    """Load merged preferences; return None on any failure."""
    try:
        from src.config.loader import load_preferences

        return load_preferences(user_id=user_id)
    except Exception:
        logger.exception("Failed to load preferences for user_id=%s", user_id)
        return None


def _state_from_prefs(prefs: Any) -> str:
    """Resolve a 2-letter state code from prefs.search.location_zip.

    Returns "UNKNOWN" if the zip is missing or its prefix isn't in our map.
    """
    zip_code = _safe_attr(prefs, "search", "location_zip") or ""
    if not isinstance(zip_code, str) or len(zip_code) < 3:
        return "UNKNOWN"
    return _ZIP_PREFIX_STATE.get(zip_code[:3], "UNKNOWN")


def _safe_attr(obj: Any, *path: str) -> Any:
    """Walk attribute path defensively. Returns None if anything is missing."""
    cur = obj
    for name in path:
        if cur is None:
            return None
        cur = getattr(cur, name, None)
    return cur


def _wants_ev(fuel_types: list[Any]) -> bool:
    """True iff at least one fuel_type entry matches an EV/PHEV value."""
    for ft in fuel_types or []:
        if isinstance(ft, str) and ft.strip().lower() in _EV_FUEL_TYPES:
            return True
    return False


# ---- AFDC cache lookup ----


# Lazy-fetch policy: when a request asks for a state we haven't seen yet,
# call AFDC inline (~200ms) and write the cache. Subsequent requests for
# the same state hit the cache. Past this many days, the cached value is
# stale enough that we re-fetch even on cache hit — keeps the data fresh
# without needing the nightly cron to know about every state.
_LAZY_REFETCH_DAYS = 7


def _load_programs(state_or_us: str) -> list[dict[str, Any]]:
    """Read the cached AFDC programs list for a state (or 'US').

    Lazy-fetch path: if the cache is empty or stale (>7 days old), call
    AFDC directly, persist, and return. First user in a state pays the
    ~200ms latency; subsequent users get the cache. The nightly cron
    pre-warms the high-traffic states from src/handler.py — adding a
    state to that list is purely a first-load optimization now, not
    a correctness requirement.

    Returns [] if AFDC is unreachable and we have no prior cache —
    handler treats that as silently degraded rather than failing.
    """
    series_key = f"afdc:{state_or_us}"
    row = None
    try:
        row = macro_series_store.get_latest(series_key)
    except Exception:
        logger.exception("AFDC cache read failed for %s", series_key)

    if _is_cache_fresh(row):
        return _extract_programs(row)

    # Cache is empty or stale — try a live fetch.
    fresh = _lazy_fetch(state_or_us)
    if fresh is not None:
        return fresh
    # Live fetch failed; fall back to the stale cache if we have one rather
    # than blanking the card.
    return _extract_programs(row)


def _is_cache_fresh(row: dict[str, Any] | None) -> bool:
    """True if `row` exists and was fetched within the last `_LAZY_REFETCH_DAYS`."""
    if not row:
        return False
    fetched_at = row.get("fetched_at")
    if not isinstance(fetched_at, str):
        return False
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    age = datetime.now(UTC) - ts
    return age <= timedelta(days=_LAZY_REFETCH_DAYS)


def _extract_programs(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull the `programs` list out of a snapshot row (or [] if shape is off)."""
    if not row:
        return []
    meta = row.get("provider_metadata") or {}
    programs = meta.get("programs") if isinstance(meta, dict) else None
    if not isinstance(programs, list):
        return []
    return [p for p in programs if isinstance(p, dict)]


def _lazy_fetch(state_or_us: str) -> list[dict[str, Any]] | None:
    """Fetch AFDC for one state inline and persist into the cache.

    Returns the parsed programs list on success, None on any failure
    (so the caller can fall back to a stale cache rather than empty).
    """
    # Lazy import — afdc module pulls in httpx which we don't need on
    # hits that resolve from the cache.
    try:
        from src.tools.external import afdc
    except Exception:
        logger.exception("AFDC module import failed; can't lazy-fetch %s", state_or_us)
        return None

    fetcher = (
        afdc.fetch_federal_incentives
        if state_or_us == "US"
        else (lambda s=state_or_us: afdc.fetch_state_incentives(s))
    )
    try:
        programs = fetcher()
    except Exception:
        logger.exception("AFDC lazy fetch raised for %s", state_or_us)
        return None
    if programs is None:
        return None
    # Persist for the next caller. The AFDC client's refresh_for_states
    # writes the same shape, so we mirror its serialization here.
    try:
        macro_series_store.write_observation(
            series_key=f"afdc:{state_or_us}",
            timestamp=datetime.now(UTC).isoformat(),
            value=0.0,
            provider_metadata={"programs": programs},
        )
    except Exception:
        logger.exception("AFDC lazy fetch cache write failed for %s", state_or_us)
    return programs


# ---- stackability matrix ----


@lru_cache(maxsize=1)
def _load_stackability() -> dict[str, Any]:
    """Load the YAML matrix once per cold start.

    Returns a dict with keys `programs` and `afdc_id_map`. Missing or
    malformed file yields empty dicts (so callers see "unknown program,
    assume stacks=true").
    """
    try:
        with _STACKABILITY_PATH.open() as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("incentive stackability YAML load failed: %s", exc)
        return {"programs": {}, "afdc_id_map": {}}
    if not isinstance(data, dict):
        return {"programs": {}, "afdc_id_map": {}}
    return {
        "programs": data.get("programs") or {},
        "afdc_id_map": data.get("afdc_id_map") or {},
    }


# ---- item projection ----


def _project_items(
    raw_programs: list[dict[str, Any]],
    matrix: dict[str, Any],
    prefs: Any,
) -> list[dict[str, Any]]:
    """Combine AFDC programs with the stackability matrix into wire items.

    For the federal 30D entry, we additionally run a vehicle-eligibility
    check against `fed_credit_eligibility` if the user has specified any
    `included_brands` — letting the dashboard surface a "you qualify"
    confirmation. Without an included_brand, we assume eligibility (it's
    "qualified pending vehicle pick" — better UX than hiding it entirely).
    """
    id_map: dict[Any, str] = matrix.get("afdc_id_map") or {}
    programs_meta: dict[str, Any] = matrix.get("programs") or {}

    items: list[dict[str, Any]] = []
    seen_canonical: set[str] = set()

    for prog in raw_programs:
        afdc_id = prog.get("id")
        # YAML keys may be parsed as int or str — try both.
        canonical = id_map.get(afdc_id) or id_map.get(_as_int(afdc_id))

        meta = programs_meta.get(canonical) if canonical else None
        # Skip duplicates when the same canonical program appears twice (e.g.
        # federal 30D listed under both US and a state's response).
        if canonical and canonical in seen_canonical:
            continue
        if canonical:
            seen_canonical.add(canonical)

        item = _build_item(prog, canonical, meta, prefs)
        if item is not None:
            items.append(item)

    return items


def _build_item(
    prog: dict[str, Any],
    canonical: str | None,
    meta: dict[str, Any] | None,
    prefs: Any,
) -> dict[str, Any] | None:
    """Project one AFDC + curated entry to the wire shape.

    Defaults:
      - stacks=true when no curated entry exists (best-faith assumption —
        most programs do stack with federal)
      - amount_usd: prefer a curated YAML override when the program is
        canonical (matched via `afdc_id_map`) — the AFDC body parser is
        noisy enough that for the top federal/state programs we know the
        published value better than the regex does. Falls through to the
        AFDC parsed amount for unknown programs, and to the federal-credit
        eligibility helper for `federal_30d` as a final fallback.
    """
    # Curated YAML amount takes precedence for canonical programs — the user
    # is the source of truth for top-10-15 federal + state programs.
    curated_amount = (meta or {}).get("amount_usd") if canonical else None
    if isinstance(curated_amount, (int, float)):
        amount: Any = curated_amount
    else:
        amount = prog.get("amount_usd")
    if amount is None and canonical == "federal_30d":
        amount = _federal_credit_amount_for_user(prefs)

    title = (meta or {}).get("title") or prog.get("title") or "(untitled incentive)"
    jurisdiction = (meta or {}).get("jurisdiction") or prog.get("jurisdiction") or "state"
    # Best-faith assumption when uncurated: stacks=true (federal-base layer
    # is the universal exception, but it doesn't have stacks_with anyway).
    # Empty stacks_with on a base-layer entry (federal_30d) still counts as
    # "this is on the stack" — it just doesn't stack onto anything underneath.
    # Both branches collapse to True today; we keep the lookup for the future
    # case where stacks_with carries a real "incompatible with: ..." semantic.
    _ = (meta or {}).get("stacks_with")
    stacks = True

    # Expiration precedence: curated YAML override (canonical) > AFDC-parsed
    # > null. The YAML carries the IRA statutory sunset dates for federal
    # credits, where we know the horizon better than the regex can. For state
    # / utility programs the YAML is typically null, so we fall through to
    # whatever the AFDC plaintext parser surfaced in `_normalize_program`.
    curated_expiration = (meta or {}).get("expiration_date") if canonical else None
    if isinstance(curated_expiration, str) and curated_expiration.strip():
        expiration_date: str | None = curated_expiration
    else:
        afdc_expiration = prog.get("expiration_date")
        expiration_date = afdc_expiration if isinstance(afdc_expiration, str) else None

    return {
        "id": canonical or f"afdc:{prog.get('id')}",
        "title": title,
        "amount_usd": int(amount) if isinstance(amount, (int, float)) else 0,
        "jurisdiction": jurisdiction,
        "expiration_date": expiration_date,
        "stacks": stacks,
        "notes": (meta or {}).get("notes") or prog.get("summary") or "",
        "source_url": prog.get("source_url") or "",
    }


def _federal_credit_amount_for_user(prefs: Any) -> int:
    """Use fed_credit_eligibility to derive an amount for the user.

    Without an `included_brands[0]`, we don't know what vehicle they're
    targeting — assume the maximum credit ($7,500) as the "pending pick"
    headline. With a brand specified, look up the most recent model year
    for that brand in the eligibility list and surface its credit.
    """
    included = _safe_attr(prefs, "included_brands") or []
    if not isinstance(included, list) or not included:
        return 7500

    target_brand = included[0]
    if not isinstance(target_brand, str):
        return 7500

    # Walk all vehicles in the eligibility list for this brand and pick the
    # max credit. Tells the user the *best case* federal credit for their
    # brand preference.
    rows = fed_credit_eligibility._load()
    best = 0
    target_norm = target_brand.strip().lower()
    for row in rows:
        if str(row.get("make", "")).strip().lower() != target_norm:
            continue
        try:
            credit = int(row.get("credit_amount_usd") or 0)
        except (TypeError, ValueError):
            credit = 0
        if credit > best:
            best = credit
    return best or 7500


def _as_int(value: Any) -> Any:
    """Cast to int if possible — used for YAML key lookups since AFDC ids
    can arrive as int or str."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_by_title(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse same-title entries to the highest-amount row.

    AFDC catalogs sometimes split a single human-facing program across
    multiple tier rows that share a title (e.g. DE's "DE EV/PHEV Rebate"
    appearing twice for new-BEV vs new-PHEV pricing tiers). For the
    headline-stack UX, those collapse to a single highest-amount row so
    the dashboard isn't showing what looks like a duplicate.

    Order is preserved by the first occurrence of each title; ties on
    amount keep the earlier row. Items with no title fall through
    unaffected (treated as unique).
    """
    best_by_title: dict[str, int] = {}
    order: list[str] = []
    no_title_items: list[dict[str, Any]] = []
    title_to_item: dict[str, dict[str, Any]] = {}

    for item in items:
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            no_title_items.append(item)
            continue
        key = title.strip()
        amount = int(item.get("amount_usd") or 0)
        if key not in best_by_title:
            best_by_title[key] = amount
            title_to_item[key] = item
            order.append(key)
        elif amount > best_by_title[key]:
            best_by_title[key] = amount
            title_to_item[key] = item

    return [title_to_item[k] for k in order] + no_title_items


# ---- warnings ----


def _build_warnings(items: list[dict[str, Any]]) -> list[str]:
    """Surface short urgency warnings for expiring programs.

    Each item's warning window is keyed off its `jurisdiction` —
    federal (90d), state (42d), utility (21d) — reflecting the cadence at
    which each tier actually changes. Warnings are emitted only when the
    expiration falls within that tier's window.

    Copy carries the days remaining and the actual ISO date, with a
    different shape depending on whether the program stacks (collaborative
    framing — "only stacks if claimed before then") or doesn't (standalone
    framing — "consider claiming before expiration"). The output list is
    sorted most-urgent-first so the rendered card leads with whatever's
    closest to sunsetting.
    """
    candidates: list[tuple[int, str]] = []  # (days_remaining, warning text)
    now = datetime.now(UTC)

    for item in items:
        exp_raw = item.get("expiration_date")
        if not exp_raw or not isinstance(exp_raw, str):
            continue
        exp_dt = _parse_date(exp_raw)
        if exp_dt is None:
            continue

        jurisdiction = str(item.get("jurisdiction") or "").lower()
        window = _EXPIRATION_WARNING_WINDOW_BY_TIER.get(jurisdiction, _DEFAULT_WARNING_WINDOW)

        days_remaining = (exp_dt - now).days
        exp_iso = exp_dt.date().isoformat()
        title = item.get("title") or "(untitled incentive)"
        if days_remaining < 0:
            # Expired — surface it so the user knows a program has lapsed,
            # but sort it to the end (least urgent in the "do something now"
            # sense; the action is gone).
            candidates.append((10_000, f"{title} expired {exp_iso}"))
            continue
        if exp_dt - now > window:
            continue

        if item.get("stacks"):
            text = (
                f"{title} expires in {days_remaining} days ({exp_iso}) — "
                "only stacks if claimed before then"
            )
        else:
            text = f"{title} sunsets {exp_iso} — consider claiming before expiration"
        candidates.append((days_remaining, text))

    candidates.sort(key=lambda pair: pair[0])
    return [text for _, text in candidates]


def _parse_date(raw: str) -> datetime | None:
    """Best-effort date parse for AFDC expiration strings (YYYY-MM-DD)."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            continue
    return None
