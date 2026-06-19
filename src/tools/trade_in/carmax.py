"""CarMax instant offer estimation.

Uses Playwright to automate CarMax's instant offer flow.
Falls back to fixture data in TEST_MODE.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def get_carmax_estimate(
    vin: str,
    mileage: int,
    condition: str,
    zip_code: str,
) -> dict[str, Any] | None:
    """Get CarMax instant offer estimate.

    Returns a dict matching TradeInSource schema, or None on failure.

    The Playwright scraper requires a chromium binary that isn't available
    in the default Lambda runtime — set DISABLE_TRADE_IN_SCRAPERS=true to
    short-circuit and silently return None instead of stack-tracing on every
    run. Downstream code treats None as "no estimate available".
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return _fixture_estimate()
    if os.environ.get("DISABLE_TRADE_IN_SCRAPERS", "").lower() == "true":
        return None

    try:
        return _scrape_carmax(vin, mileage, condition, zip_code)
    except Exception:
        logger.exception("CarMax scrape failed for VIN %s", vin)
        return None


def _fixture_estimate() -> dict[str, Any]:
    """Return fixture CarMax estimate for test mode."""
    return {
        "source_name": "carmax",
        "estimate_usd": 20500.0,
        "estimate_type": "instant_offer",
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def _scrape_carmax(vin: str, mileage: int, condition: str, zip_code: str) -> dict[str, Any]:
    """Scrape CarMax for an instant offer using Playwright."""
    from playwright.sync_api import sync_playwright

    url = "https://www.carmax.com/sell-my-car"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_extra_http_headers(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                }
            )
            page.goto(url, timeout=30000)
            page.wait_for_load_state("domcontentloaded")

            # Attempt VIN entry
            vin_input = page.locator("input[data-testid='vin-input'], input[name='vin']").first
            if vin_input.is_visible():
                vin_input.fill(vin)

            page.wait_for_timeout(3000)
            html = page.content()
        finally:
            browser.close()

    match = re.search(r"\$(\d{1,3}(?:,\d{3})*)", html)
    if match:
        value = float(match.group(1).replace(",", ""))
        return {
            "source_name": "carmax",
            "estimate_usd": value,
            "estimate_type": "instant_offer",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    raise ValueError("Could not parse CarMax offer from page")
