"""Manheim Used Vehicle Value Index (MUVVI) puller.

Cox Automotive publishes MUVVI as a free public press release around the 7th of
each month, covering the prior month's data. It's the canonical wholesale used-
vehicle market pulse (Bloomberg, WSJ, the Fed cite it). The release contains
the headline index value (1995 = 100), MoM/YoY change, and segment breakouts
(compact car, midsize, luxury, pickup, SUV, sporty, van).

We extract the values via the project's LLM provider — monthly cadence makes
LLM extraction cheap and resilient to Cox restructuring their press portal,
which has historically been the dominant failure mode. The puller logs the URL
it hit on failure so we can repoint quickly.

Resilience contract: callers downstream treat `None` as "no fresh data, fall
back to cache." We therefore never raise — any failure is logged and returns
None.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Literal, cast

import httpx
from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.config import ModelConfig
from src.llm.provider import get_llm
from src.memory import macro_series_store

logger = logging.getLogger(__name__)

# Best-known public landing page. If Cox restructures the press portal the
# puller's first failure mode is logging this URL so we know exactly what
# changed. Repointing is a one-line change here (see the plan's "Manheim
# press-release HTML" gotcha).
#
# Cox migrated from publish.manheim.com → site.manheim.com around mid-2026;
# we point straight at the destination to skip the 301 and to keep the URL
# logged-on-failure honest about where we actually look.
MANHEIM_RELEASE_URL = "https://site.manheim.com/services/consulting/used-vehicle-value-index.html"

# Cap input size sent to the LLM — Cox's pages are mostly boilerplate; the
# numbers we need always appear in a few KB near the index callout. Keeping
# the prompt small keeps the Haiku call cheap and predictable.
_MAX_HTML_CHARS = 60_000

# Per CLAUDE.md: every LLM call sets a ModelConfig with provider, model_id,
# temperature, and max_tokens — no defaults. Provider + model id route
# through the same LLM_PROVIDER + LLM_DRAFTING_MODEL env vars the other
# nodes use, so a deployment that runs against Bedrock (Nova) vs the
# Anthropic SDK (Claude) needs only an env flip, not a code change.
_PROVIDER = cast(
    "Literal['anthropic', 'bedrock', 'openai']",
    os.environ.get("LLM_PROVIDER", "bedrock"),
)
_EXTRACTION_MODEL = ModelConfig(
    provider=_PROVIDER,
    model_id=os.environ.get("LLM_DRAFTING_MODEL", "amazon.nova-lite-v1:0"),
    temperature=0.0,
    max_tokens=1024,
)

_EXTRACTION_SYSTEM_PROMPT = (
    "You extract the Manheim Used Vehicle Value Index from press-release HTML.\n"
    "Return ONLY a JSON object — no prose, no markdown — with these keys:\n"
    '  - "headline_index": number (the top-line MUVVI value, 1995 = 100)\n'
    '  - "month_year": string like "May 2026" (the month the release covers,\n'
    "    NOT the publication date)\n"
    '  - "mom_change_pct": number or null (month-over-month percent change)\n'
    '  - "yoy_change_pct": number or null (year-over-year percent change)\n'
    '  - "segments": object mapping segment name -> index value, e.g.\n'
    '    {"compact car": 198.4, "midsize car": 191.2, "luxury": 178.6,\n'
    '    "pickup": 215.7, "suv": 207.1, "sporty": 184.0, "van": 230.5}\n'
    "Use null for any field you cannot find. If the headline index or month\n"
    "is missing, still return a JSON object with those fields set to null."
)


def fetch_and_cache_index() -> dict | None:
    """Fetch + extract + cache the most recent MUVVI release.

    Returns the extraction dict on success; `None` on any failure (page
    unreachable, LLM call failure, malformed extraction, missing headline).

    Honors `TEST_MODE=true`: short-circuits before any HTTP or LLM work.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        logger.info("TEST_MODE: skipping Manheim fetch")
        return None

    html = _fetch_html(MANHEIM_RELEASE_URL)
    if html is None:
        return None

    extracted = _extract_index(html)
    if extracted is None:
        return None

    timestamp = _month_year_to_iso(extracted.get("month_year"))
    if timestamp is None:
        logger.warning(
            "Manheim extraction missing/unparseable month_year (got %r); url=%s",
            extracted.get("month_year"),
            MANHEIM_RELEASE_URL,
        )
        return None

    headline = extracted.get("headline_index")
    if headline is None:
        logger.warning(
            "Manheim extraction missing headline_index; url=%s",
            MANHEIM_RELEASE_URL,
        )
        return None
    try:
        headline_value = float(headline)
    except (TypeError, ValueError):
        logger.warning(
            "Manheim headline_index not numeric (got %r); url=%s",
            headline,
            MANHEIM_RELEASE_URL,
        )
        return None

    # Write the headline first; segments are best-effort. A failure on any
    # one write logs and continues — better to land partial cache than to
    # leave the macro layer stale because one segment was unparseable.
    _safe_write(
        series_key="manheim:headline",
        timestamp=timestamp,
        value=headline_value,
    )

    segments = extracted.get("segments") or {}
    if isinstance(segments, dict):
        for segment_name, segment_value in segments.items():
            try:
                value = float(segment_value)
            except (TypeError, ValueError):
                logger.debug(
                    "Manheim segment %r value not numeric (got %r); skipping",
                    segment_name,
                    segment_value,
                )
                continue
            slug = _slugify(str(segment_name))
            if not slug:
                continue
            _safe_write(
                series_key=f"manheim:{slug}",
                timestamp=timestamp,
                value=value,
            )

    return extracted


# ---- Internals ----


def _fetch_html(url: str) -> str | None:
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code != 200:
            logger.warning("Manheim page %s returned HTTP %d", url, response.status_code)
            return None
        return response.text
    except httpx.HTTPError as exc:
        logger.warning("Manheim fetch failed for %s: %s", url, exc)
        return None


def _extract_index(html: str) -> dict | None:
    """Run the LLM extraction. Returns the parsed dict or None on any failure."""
    trimmed = html[:_MAX_HTML_CHARS]
    messages = [
        SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=f"HTML:\n{trimmed}"),
    ]

    try:
        llm = get_llm(_EXTRACTION_MODEL)
        response = llm.invoke(messages)
    except Exception as exc:
        logger.warning("Manheim LLM extraction call failed: %s", exc)
        return None

    content = response.content if isinstance(response.content, str) else str(response.content)
    try:
        return _parse_llm_json(content)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Manheim LLM response did not parse as JSON: %s; head=%r",
            exc,
            content[:200],
        )
        return None


def _parse_llm_json(content: str) -> dict:
    """Tolerant JSON extraction — accepts the raw object or one wrapped in code fences."""
    text = content.strip()
    # Strip ``` fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # If there's prose around the JSON, isolate the first {...} block.
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    return parsed


def _month_year_to_iso(month_year: object) -> str | None:
    """Convert "May 2026" → "2026-05-01T00:00:00+00:00"."""
    if not isinstance(month_year, str):
        return None
    cleaned = month_year.strip()
    if not cleaned:
        return None
    for fmt in ("%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        return f"{dt.year:04d}-{dt.month:02d}-01T00:00:00+00:00"
    return None


def _slugify(name: str) -> str:
    """Lowercase + collapse non-alphanumerics to underscores. Trim ends."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug


def _safe_write(*, series_key: str, timestamp: str, value: float) -> None:
    """Write one observation, swallowing storage errors so partial cache lands."""
    try:
        macro_series_store.write_observation(
            series_key=series_key,
            timestamp=timestamp,
            value=value,
        )
    except Exception as exc:
        logger.warning(
            "Manheim cache write failed for %s @ %s: %s",
            series_key,
            timestamp,
            exc,
        )
