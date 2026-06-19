#!/usr/bin/env python3
"""Debug the live scraper — run outside Lambda to see what it actually returns.

Usage:
    python scripts/debug_scraper.py            # scrape both sources
    python scripts/debug_scraper.py carscom    # cars.com only
    python scripts/debug_scraper.py cargurus   # cargurus only
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Force live mode
os.environ["TEST_MODE"] = "false"

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def dump_html(source: str, html: str) -> Path:
    """Save raw HTML for inspection."""
    path = ROOT / f"scraper-debug-{source}.html"
    path.write_text(html)
    return path


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    from src.config.loader import load_preferences
    from src.tools.listings.parser import (
        parse_cargurus_listings,
        parse_carscom_listings,
    )
    from src.tools.listings.scraper import _scrape_cargurus, _scrape_carscom

    prefs = load_preferences()
    zip_code = prefs.search.location_zip
    radius = prefs.search.radius_miles
    max_age = prefs.search.max_vehicle_age_years

    print(f"Zip: {zip_code}, Radius: {radius}mi, Max age: {max_age}y\n")

    if target in ("all", "carscom"):
        print("=" * 60)
        print("Cars.com")
        print("=" * 60)
        try:
            html = _scrape_carscom(zip_code, radius, max_age)
            path = dump_html("carscom", html)
            print(f"HTML saved: {path} ({len(html):,} bytes)")
            listings = parse_carscom_listings(html, zip_code)
            print(f"Parsed {len(listings)} listings\n")
            if listings:
                print(json.dumps(listings[:3], indent=2))
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")

    if target in ("all", "cargurus"):
        print("\n" + "=" * 60)
        print("CarGurus")
        print("=" * 60)
        try:
            html = _scrape_cargurus(zip_code, radius, max_age)
            path = dump_html("cargurus", html)
            print(f"HTML saved: {path} ({len(html):,} bytes)")
            listings = parse_cargurus_listings(html, zip_code)
            print(f"Parsed {len(listings)} listings\n")
            if listings:
                print(json.dumps(listings[:3], indent=2))
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
