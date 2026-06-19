#!/usr/bin/env python3
"""Debug the MarketCheck API directly — bypasses client filters so you can probe
arbitrary search params (make, rows, sort order, etc.) without touching code.

Usage:
    MARKETCHECK_API_KEY=<key> python scripts/debug_marketcheck.py

Defaults pulled from src/config/preferences.yaml. Override anything via flags:

    # Different zip / radius / age:
    python scripts/debug_marketcheck.py --zip 10001 --radius 50 --max-age 5

    # Different fuel types (uses the canonical fuel_type after parsing):
    python scripts/debug_marketcheck.py --fuel "Electric"
    python scripts/debug_marketcheck.py --fuel "Electric,Plug-in Hybrid"
    python scripts/debug_marketcheck.py --fuel ""        # no filter

    # Filter by make (sent directly to MarketCheck, then post-filtered locally):
    python scripts/debug_marketcheck.py --make Toyota --fuel ""
    python scripts/debug_marketcheck.py --make "Tesla,Rivian"

    # Get more rows (default 50, max 50 per call on free tier — paginate via --start):
    python scripts/debug_marketcheck.py --rows 50 --start 0
    python scripts/debug_marketcheck.py --rows 50 --start 50

    # Sort order (MarketCheck supports: price, miles, dist, list_date)
    python scripts/debug_marketcheck.py --sort-by dist --sort-order asc

    # Inspect a specific listing:
    python scripts/debug_marketcheck.py --index 3 --raw
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from src.config.loader import load_preferences
    from src.tools.listings.marketcheck import MARKETCHECK_BASE_URL, _parse_listing

    prefs = load_preferences()

    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", default=prefs.search.location_zip)
    parser.add_argument("--radius", type=int, default=prefs.search.radius_miles)
    parser.add_argument("--max-age", type=int, default=prefs.search.max_vehicle_age_years)
    parser.add_argument(
        "--fuel",
        default=None,
        help='Comma-separated fuel types (post-filter). "" = no filter. Default: prefs.',
    )
    parser.add_argument(
        "--make",
        default=None,
        help="Comma-separated makes sent to MarketCheck (e.g. Toyota or Tesla,Rivian).",
    )
    parser.add_argument(
        "--rows", type=int, default=50, help="Page size (MarketCheck max ~50 free tier)."
    )
    parser.add_argument("--start", type=int, default=0, help="Pagination offset.")
    parser.add_argument(
        "--sort-by",
        default=None,
        help="MarketCheck sort: price, miles, dist, list_date.",
    )
    parser.add_argument("--sort-order", default="asc", help="asc or desc")
    parser.add_argument("--limit", type=int, default=15, help="How many to print in summary.")
    parser.add_argument("--index", type=int, default=0, help="Listing index for detail dump.")
    parser.add_argument(
        "--raw", action="store_true", help="Dump raw MarketCheck record for --index."
    )
    args = parser.parse_args()

    if args.fuel is None:
        fuel_types = prefs.search.fuel_types
    elif args.fuel == "":
        fuel_types = []
    else:
        fuel_types = [s.strip() for s in args.fuel.split(",") if s.strip()]

    api_key = os.environ.get("MARKETCHECK_API_KEY", "")
    if not api_key:
        print("ERROR: MARKETCHECK_API_KEY env var not set")
        return 1

    current_year = datetime.now(UTC).year
    params: dict[str, object] = {
        "api_key": api_key,
        "zip": args.zip,
        "radius": args.radius,
        "year_min": current_year - args.max_age,
        "year_max": current_year,
        "rows": args.rows,
        "start": args.start,
    }
    if fuel_types:
        params["fuel_type"] = ",".join(fuel_types)
    if args.make:
        params["make"] = args.make
    if args.sort_by:
        params["sort_by"] = args.sort_by
        params["sort_order"] = args.sort_order

    print(
        f"Zip: {args.zip}, Radius: {args.radius}mi, Max age: {args.max_age}y, "
        f"Fuel: {fuel_types or 'any'}, Make: {args.make or 'any'}, "
        f"Sort: {args.sort_by or 'default'} {args.sort_order if args.sort_by else ''}\n"
    )

    resp = httpx.get(MARKETCHECK_BASE_URL, params=params, timeout=30.0)
    if resp.status_code != 200:
        print(f"FAILED: HTTP {resp.status_code}: {resp.text[:300]}")
        return 1
    data = resp.json()
    raw_listings = data.get("listings") or []
    num_found = data.get("num_found", len(raw_listings))

    parsed = [p for p in (_parse_listing(r) for r in raw_listings) if p]
    if fuel_types:
        wanted = {ft.lower() for ft in fuel_types}
        before = len(parsed)
        parsed = [lst for lst in parsed if (lst.get("fuel_type") or "").lower() in wanted]
        if before != len(parsed):
            print(f"(post-filter dropped {before - len(parsed)} outside fuel filter)")

    print(f"Got {len(parsed)} parsed (raw {len(raw_listings)}, total available {num_found})\n")
    if not parsed:
        print("(no results — try removing filters or paginating with --start)")
        return 0

    fuel_dist = Counter((lst.get("fuel_type") or "(blank)") for lst in parsed)
    print("Fuel-type distribution:")
    for ft, n in fuel_dist.most_common():
        print(f"  {ft:>20}: {n}")
    make_dist = Counter(lst.get("make") or "(blank)" for lst in parsed)
    print("Make distribution:")
    for mk, n in make_dist.most_common(10):
        print(f"  {mk:>20}: {n}")
    print()

    n_show = min(args.limit, len(parsed))
    print(f"Top {n_show} listings:")
    for i, lst in enumerate(parsed[:n_show]):
        print(
            f"  [{i:>2}] {lst['year']} {lst['make']} {lst['model']} {lst['trim']}".rstrip()
            + f" [{lst.get('fuel_type', '?')}/{lst.get('powertrain_type') or '-'}]"
            + f" @ ${lst['selling_price']:,.0f}"
            + f" — {lst['dealer_name']} ({lst['dealer_distance_miles']:.0f}mi)"
        )

    if args.index < 0 or args.index >= len(parsed):
        print(f"\nERROR: --index {args.index} out of range (have {len(parsed)} listings)")
        return 1

    if args.raw:
        target_vin = parsed[args.index].get("listing_id", "").removeprefix("mc:")
        match = next((r for r in raw_listings if r.get("vin") == target_vin), raw_listings[0])
        print(f"\nRaw MarketCheck record for index {args.index}:")
        print(json.dumps(match, indent=2))
    else:
        print(f"\nParsed record for index {args.index}:")
        print(json.dumps(parsed[args.index], indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
