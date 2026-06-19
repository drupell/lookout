"""Extracts structured listing data from raw HTML scraped from Cars.com and CarGurus."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def parse_carscom_listings(html: str, zip_code: str) -> list[dict[str, Any]]:
    """Parse Cars.com search results HTML into structured listing dicts."""
    soup = BeautifulSoup(html, "html.parser")
    listings: list[dict[str, Any]] = []

    cards = soup.select(".vehicle-card")
    for card in cards:
        try:
            listing = _parse_carscom_card(card, zip_code)
            if listing:
                listings.append(listing)
        except Exception:
            logger.debug("Failed to parse Cars.com card", exc_info=True)
            continue

    logger.info("Parsed %d listings from Cars.com HTML", len(listings))
    return listings


def parse_cargurus_listings(html: str, zip_code: str) -> list[dict[str, Any]]:
    """Parse CarGurus search results HTML into structured listing dicts."""
    soup = BeautifulSoup(html, "html.parser")
    listings: list[dict[str, Any]] = []

    # CarGurus uses various card selectors
    cards = soup.select("[data-cg-ft='car-blade']") or soup.select(".listing-row")
    for card in cards:
        try:
            listing = _parse_cargurus_card(card, zip_code)
            if listing:
                listings.append(listing)
        except Exception:
            logger.debug("Failed to parse CarGurus card", exc_info=True)
            continue

    logger.info("Parsed %d listings from CarGurus HTML", len(listings))
    return listings


def _parse_carscom_card(card: Any, zip_code: str) -> dict[str, Any] | None:
    """Parse a single Cars.com vehicle card element."""
    # Title: "2025 Chevrolet Equinox EV"
    title_el = card.select_one(".title")
    if not title_el:
        return None

    title_text = title_el.get_text(strip=True)
    year, make, model = _parse_title(title_text)
    if not year or not make:
        return None

    # Price
    price_el = card.select_one(".primary-price")
    if not price_el:
        return None
    selling_price = _parse_price(price_el.get_text(strip=True))
    if selling_price is None:
        return None

    # MSRP (sometimes shown separately)
    msrp_el = card.select_one(".msrp")
    msrp = _parse_price(msrp_el.get_text(strip=True)) if msrp_el else selling_price

    # Dealer
    dealer_el = card.select_one(".dealer-name")
    dealer_name = dealer_el.get_text(strip=True) if dealer_el else "Unknown Dealer"

    # Distance
    distance_el = card.select_one(".miles-from")
    distance = _parse_distance(distance_el.get_text(strip=True)) if distance_el else 0.0

    # URL
    link_el = card.select_one("a[href]")
    url = f"https://www.cars.com{link_el['href']}" if link_el and link_el.get("href") else ""

    listing_id = _generate_listing_id("carscom", title_text, selling_price)

    return {
        "listing_id": listing_id,
        "make": make,
        "model": model,
        "year": year,
        "msrp": msrp,
        "selling_price": selling_price,
        "effective_apr": None,
        "lease_monthly": None,
        "dealer_name": dealer_name,
        "dealer_distance_miles": distance,
        "url": url,
        "is_ev": True,
    }


def _parse_cargurus_card(card: Any, zip_code: str) -> dict[str, Any] | None:
    """Parse a single CarGurus listing card element."""
    title_el = card.select_one("h4") or card.select_one(".listing-title")
    if not title_el:
        return None

    title_text = title_el.get_text(strip=True)
    year, make, model = _parse_title(title_text)
    if not year or not make:
        return None

    price_el = card.select_one(".listing-price") or card.select_one(
        "[data-cg-ft='listing-blade-price']"
    )
    if not price_el:
        return None
    selling_price = _parse_price(price_el.get_text(strip=True))
    if selling_price is None:
        return None

    msrp = selling_price  # CarGurus rarely shows MSRP separately

    dealer_el = card.select_one(".dealer-name") or card.select_one(
        "[data-cg-ft='listing-blade-dealer-name']"
    )
    dealer_name = dealer_el.get_text(strip=True) if dealer_el else "Unknown Dealer"

    distance_el = card.select_one(".distance") or card.select_one(
        "[data-cg-ft='listing-blade-distance']"
    )
    distance = _parse_distance(distance_el.get_text(strip=True)) if distance_el else 0.0

    link_el = card.select_one("a[href]")
    url = ""
    if link_el and link_el.get("href"):
        href = link_el["href"]
        url = f"https://www.cargurus.com{href}" if href.startswith("/") else href

    listing_id = _generate_listing_id("cargurus", title_text, selling_price)

    return {
        "listing_id": listing_id,
        "make": make,
        "model": model,
        "year": year,
        "msrp": msrp,
        "selling_price": selling_price,
        "effective_apr": None,
        "lease_monthly": None,
        "dealer_name": dealer_name,
        "dealer_distance_miles": distance,
        "url": url,
        "is_ev": True,
    }


def _parse_title(title: str) -> tuple[int | None, str, str]:
    """Extract year, make, model from a title like '2025 Chevrolet Equinox EV'."""
    match = re.match(r"(\d{4})\s+(\S+)\s+(.+)", title.strip())
    if not match:
        return None, "", ""
    year = int(match.group(1))
    make = match.group(2)
    model = match.group(3).strip()
    return year, make, model


def _parse_price(text: str) -> float | None:
    """Extract a numeric price from text like '$31,500' or 'Price: $31,500'."""
    match = re.search(r"\$?([\d,]+)", text.replace(",", "").replace("$", ""))
    if not match:
        # Try without dollar sign
        digits = re.findall(r"\d+", text.replace(",", ""))
        if digits:
            value = float(digits[0])
            if value > 1000:  # sanity check — prices should be > $1,000
                return value
        return None
    return float(match.group(1).replace(",", ""))


def _parse_distance(text: str) -> float:
    """Extract miles from text like '12.3 mi away'."""
    match = re.search(r"([\d.]+)", text)
    return float(match.group(1)) if match else 0.0


def _generate_listing_id(source: str, title: str, price: float) -> str:
    """Generate a deterministic listing ID from source + title + price."""
    raw = f"{source}:{title}:{price}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
