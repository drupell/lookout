"""Playwright-based EV listings scraper for Cars.com and CarGurus.

Falls back to static HTML fixtures when TEST_MODE=true.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from src.config.loader import load_preferences
from src.tools.listings.parser import parse_cargurus_listings, parse_carscom_listings

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def scrape_listings() -> list[dict[str, Any]]:
    """Scrape EV listings from configured sources.

    Returns a combined list of raw listing dicts ready for Pydantic validation.
    In TEST_MODE, reads from static HTML fixtures instead of live sites.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        return _load_fixture_listings()

    prefs = load_preferences()
    zip_code = prefs.search.location_zip
    radius = prefs.search.radius_miles
    max_age = prefs.search.max_vehicle_age_years

    all_listings: list[dict[str, Any]] = []

    try:
        carscom_html = _scrape_carscom(zip_code, radius, max_age)
        all_listings.extend(parse_carscom_listings(carscom_html, zip_code))
    except Exception:
        logger.exception("Cars.com scrape failed")

    try:
        cargurus_html = _scrape_cargurus(zip_code, radius, max_age)
        all_listings.extend(parse_cargurus_listings(cargurus_html, zip_code))
    except Exception:
        logger.exception("CarGurus scrape failed")

    # Apply brand/model exclusions
    excluded_brands = {b.lower() for b in prefs.excluded_brands}
    excluded_models = {m.lower() for m in prefs.excluded_models}

    filtered = [
        lst
        for lst in all_listings
        if lst.get("make", "").lower() not in excluded_brands
        and lst.get("model", "").lower() not in excluded_models
    ]

    logger.info("Scraped %d listings (%d after exclusions)", len(all_listings), len(filtered))
    return filtered


def _load_fixture_listings() -> list[dict[str, Any]]:
    """Load listings from JSON fixture file for test mode."""
    fixture_path = (
        Path(__file__).parent.parent.parent.parent / "evals" / "fixtures" / "listings_sample.json"
    )
    if fixture_path.exists():
        return json.loads(fixture_path.read_text())

    logger.warning("No fixture file found at %s, returning empty listings", fixture_path)
    return []


def _scrape_carscom(zip_code: str, radius: int, max_age: int) -> str:
    """Scrape Cars.com search results page using Playwright."""
    from playwright.sync_api import sync_playwright

    current_year = 2026
    min_year = current_year - max_age

    url = (
        f"https://www.cars.com/shopping/results/"
        f"?fuel_slugs[]=electric"
        f"&list_price_max="
        f"&maximum_distance={radius}"
        f"&mileage_max="
        f"&page_size=50"
        f"&sort=best_match_desc"
        f"&stock_type=all"
        f"&year_max={current_year}"
        f"&year_min={min_year}"
        f"&zip={zip_code}"
    )

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
            # Wait for listings to render
            page.wait_for_selector(".vehicle-card", timeout=10000)
            html = page.content()
        finally:
            browser.close()

    return html


def _scrape_cargurus(zip_code: str, radius: int, max_age: int) -> str:
    """Scrape CarGurus search results page using Playwright."""
    from playwright.sync_api import sync_playwright

    current_year = 2026
    min_year = current_year - max_age

    url = (
        f"https://www.cargurus.com/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action"
        f"?zip={zip_code}"
        f"&showNegotiable=true"
        f"&sortDir=ASC"
        f"&sourceContext=carGurusHomePageModel"
        f"&distance={radius}"
        f"&minYear={min_year}"
        f"&maxYear={current_year}"
        f"&fuelTypes=ELECTRIC"
    )

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
            html = page.content()
        finally:
            browser.close()

    return html
