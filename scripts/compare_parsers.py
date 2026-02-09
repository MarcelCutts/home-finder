#!/usr/bin/env python3
"""Compare zoopla-scraper vs home-finder parsing of the same Zoopla page.

Fetches a single search results page using curl_cffi and runs both
parsers on the same HTML, then compares listing IDs, prices, bedrooms,
and addresses side by side.

Usage:
    uv run python scripts/compare_parsers.py [AREA] [--page N]

Examples:
    uv run python scripts/compare_parsers.py e8
    uv run python scripts/compare_parsers.py hackney
    uv run python scripts/compare_parsers.py n17 --page 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add zoopla-scraper to import path
ZOOPLA_SCRAPER_SRC = Path(__file__).resolve().parent.parent.parent / "zoopla-scraper" / "src"
if not ZOOPLA_SCRAPER_SRC.exists():
    print(f"ERROR: zoopla-scraper not found at {ZOOPLA_SCRAPER_SRC}")
    print("Expected sibling directory: ../zoopla-scraper/src/")
    sys.exit(1)
sys.path.insert(0, str(ZOOPLA_SCRAPER_SRC))


def fetch_html(area: str, page: int) -> str:
    """Fetch a single Zoopla search page using curl_cffi."""
    from curl_cffi import requests as curl_requests

    # Use zoopla-scraper's URL builder (it's the reference implementation)
    from zoopla_scraper.fetch import build_search_url  # type: ignore[import-untyped]

    url = build_search_url(area=area, page=page)
    print(f"Fetching: {url}")
    print()

    response = curl_requests.get(
        url,
        impersonate="chrome",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        },
        timeout=30,
    )

    if response.status_code != 200:
        print(f"ERROR: HTTP {response.status_code}")
        sys.exit(1)

    html: str = response.text
    print(f"Fetched {len(html):,} bytes (HTTP {response.status_code})")
    return html


def parse_with_zoopla_scraper(html: str) -> list[dict]:
    """Parse using zoopla-scraper's parse_listings()."""
    from zoopla_scraper.parse import parse_listings  # type: ignore[import-untyped]

    listings, method = parse_listings(html)
    print(f"  zoopla-scraper: {len(listings)} listings via '{method}'")

    return [
        {
            "listing_id": l.listing_id,
            "price_pcm": l.price_pcm,
            "bedrooms": l.bedrooms,
            "address": l.address[:60],
            "title": l.title[:60],
            "url": l.url,
            "image_url": l.image_url,
            "latitude": l.latitude,
            "longitude": l.longitude,
            "postcode": l.postcode,
        }
        for l in listings
    ]


def parse_with_home_finder(html: str) -> list[dict]:
    """Parse using home-finder's ZooplaScraper._extract_rsc_listings()."""
    from home_finder.scrapers.zoopla import ZooplaScraper

    scraper = ZooplaScraper()
    listings = scraper._extract_rsc_listings(html)
    print(f"  home-finder:    {len(listings)} listings via RSC extraction")

    results = []
    for l in listings:
        detail_url = l.get_detail_url() or ""
        if detail_url and not detail_url.startswith("http"):
            detail_url = f"https://www.zoopla.co.uk{detail_url}"

        results.append(
            {
                "listing_id": l.listing_id,
                "price_pcm": l.get_price_pcm(),
                "bedrooms": l.get_bedrooms(),
                "address": l.get_address()[:60],
                "title": l.get_title()[:60],
                "url": detail_url,
                "image_url": l.get_image_url(),
                "latitude": l.pos.lat if l.pos else None,
                "longitude": l.pos.lng if l.pos else None,
                "postcode": None,  # home-finder extracts postcode in _listing_to_property
            }
        )
    return results


def compare(zs_listings: list[dict], hf_listings: list[dict]) -> None:
    """Compare the two sets of listings and print a report."""
    zs_by_id = {l["listing_id"]: l for l in zs_listings}
    hf_by_id = {l["listing_id"]: l for l in hf_listings}

    zs_ids = set(zs_by_id.keys())
    hf_ids = set(hf_by_id.keys())

    common = sorted(zs_ids & hf_ids)
    only_zs = sorted(zs_ids - hf_ids)
    only_hf = sorted(hf_ids - zs_ids)

    print()
    print("=" * 90)
    print("COMPARISON RESULTS")
    print("=" * 90)
    print(f"  zoopla-scraper found: {len(zs_ids)} listings")
    print(f"  home-finder found:    {len(hf_ids)} listings")
    print(f"  Common:               {len(common)}")
    print(f"  Only in zoopla-scraper: {len(only_zs)}")
    print(f"  Only in home-finder:    {len(only_hf)}")

    # --- Listings only in one parser ---
    if only_zs:
        print()
        print("-" * 90)
        print(f"ONLY IN zoopla-scraper ({len(only_zs)} listings):")
        print("-" * 90)
        for lid in only_zs:
            l = zs_by_id[lid]
            print(f"  ID={lid}  £{l['price_pcm']}pcm  {l['bedrooms']}bed  {l['address']}")

    if only_hf:
        print()
        print("-" * 90)
        print(f"ONLY IN home-finder ({len(only_hf)} listings):")
        print("-" * 90)
        for lid in only_hf:
            l = hf_by_id[lid]
            print(f"  ID={lid}  £{l['price_pcm']}pcm  {l['bedrooms']}bed  {l['address']}")

    # --- Field-level comparison for common listings ---
    diffs: list[tuple[int, str, object, object]] = []
    for lid in common:
        zs = zs_by_id[lid]
        hf = hf_by_id[lid]

        for field in ("price_pcm", "bedrooms", "address", "latitude", "longitude"):
            zs_val = zs[field]
            hf_val = hf[field]

            # Normalize for comparison
            if isinstance(zs_val, str) and isinstance(hf_val, str):
                if zs_val.strip().lower() != hf_val.strip().lower():
                    diffs.append((lid, field, zs_val, hf_val))
            elif isinstance(zs_val, float) and isinstance(hf_val, float):
                if abs(zs_val - hf_val) > 0.0001:
                    diffs.append((lid, field, zs_val, hf_val))
            elif zs_val != hf_val:
                # One could be None and other not, or different types
                diffs.append((lid, field, zs_val, hf_val))

    if diffs:
        print()
        print("-" * 90)
        print(f"FIELD DIFFERENCES ({len(diffs)} mismatches across {len(set(d[0] for d in diffs))} listings):")
        print("-" * 90)
        for lid, field, zs_val, hf_val in diffs:
            print(f"  ID={lid}  {field}:")
            print(f"    zoopla-scraper: {zs_val!r}")
            print(f"    home-finder:    {hf_val!r}")
    else:
        print()
        print("-" * 90)
        print("NO FIELD DIFFERENCES in common listings")
        print("-" * 90)

    # --- Summary ---
    print()
    print("=" * 90)
    total_issues = len(only_zs) + len(only_hf) + len(diffs)
    if total_issues == 0:
        print("RESULT: IDENTICAL - Both parsers produce the same output")
    else:
        print(f"RESULT: {total_issues} differences found")
        if only_zs:
            print(f"  - {len(only_zs)} listings missed by home-finder (zoopla-scraper found them)")
        if only_hf:
            print(f"  - {len(only_hf)} listings missed by zoopla-scraper (home-finder found them)")
        if diffs:
            fields_with_diffs = set(d[1] for d in diffs)
            print(f"  - {len(diffs)} field mismatches in: {', '.join(sorted(fields_with_diffs))}")
    print("=" * 90)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare zoopla-scraper vs home-finder parsing"
    )
    parser.add_argument("area", nargs="?", default="e8", help="Area to search (default: e8)")
    parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    args = parser.parse_args()

    html = fetch_html(args.area, args.page)
    print()

    zs_listings = parse_with_zoopla_scraper(html)
    hf_listings = parse_with_home_finder(html)

    compare(zs_listings, hf_listings)


if __name__ == "__main__":
    main()
