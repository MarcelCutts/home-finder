"""Collect raw property data from all scrapers and save as JSON snapshots.

Usage:
    uv run python collect.py                        # Full scrape with detail enrichment
    uv run python collect.py --max-per-scraper 5    # Quick test run
    uv run python collect.py --no-details           # Skip detail page fetching
    uv run python collect.py --hash-images          # Also compute gallery image hashes
    uv run python collect.py --cache-images         # Download gallery images to local cache
    uv run python collect.py --embed-images         # Cache + compute SSCD embeddings
"""

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from home_finder.config import Settings
from home_finder.logging import configure_logging, get_logger
from home_finder.main import scrape_all_platforms
from home_finder.models import Property
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
from home_finder.utils.address import extract_outcode

logger = get_logger(__name__)

SNAPSHOTS_DIR = Path(__file__).parent / "data" / "snapshots"


def property_to_dict(prop: Property) -> dict:
    """Serialize a Property to a JSON-safe dict."""
    d = prop.model_dump(mode="json")
    d["url"] = str(prop.url)
    if prop.image_url:
        d["image_url"] = str(prop.image_url)
    return d


async def fetch_details(
    properties: list[Property],
    *,
    proxy_url: str = "",
) -> dict[str, DetailPageData]:
    """Fetch detail page data for all properties.

    Returns dict mapping unique_id -> DetailPageData.
    """
    fetcher = DetailFetcher(max_gallery_images=15, proxy_url=proxy_url)
    results: dict[str, DetailPageData] = {}

    try:
        for i, prop in enumerate(properties):
            try:
                data = await fetcher.fetch_detail_page(prop)
                if data:
                    results[prop.unique_id] = data
            except Exception as e:
                logger.warning(
                    "detail_fetch_failed",
                    unique_id=prop.unique_id,
                    error=str(e),
                )

            if (i + 1) % 20 == 0:
                logger.info("detail_progress", completed=i + 1, total=len(properties))

            # Rate limit
            await asyncio.sleep(0.5)
    finally:
        await fetcher.close()

    return results


def save_snapshot(
    properties: list[Property],
    details: dict[str, DetailPageData] | None = None,
) -> Path:
    """Save properties (with optional detail data) to a timestamped JSON file."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"snapshot_{timestamp}.json"
    path = SNAPSHOTS_DIR / filename

    records = []
    for prop in properties:
        record = property_to_dict(prop)

        if details and prop.unique_id in details:
            detail = details[prop.unique_id]
            record["detail"] = {
                "description": detail.description,
                "features": detail.features,
                "gallery_urls": detail.gallery_urls,
                "floorplan_url": detail.floorplan_url,
            }

        records.append(record)

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "count": len(records),
        "properties": records,
    }

    path.write_text(json.dumps(snapshot, indent=2, default=str))
    return path


async def main() -> None:
    parser = argparse.ArgumentParser(description="Collect property data for dedup experiments")
    parser.add_argument(
        "--max-per-scraper",
        type=int,
        default=None,
        help="Limit properties per scraper",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip fetching detail pages (faster, but no descriptions/gallery)",
    )
    parser.add_argument(
        "--hash-images",
        action="store_true",
        help="Compute pHash+wHash+crop_hash for gallery images (requires details)",
    )
    parser.add_argument(
        "--cache-images",
        action="store_true",
        help="Download gallery images to local cache (requires details)",
    )
    parser.add_argument(
        "--embed-images",
        action="store_true",
        help="Cache images + compute SSCD embeddings (requires details, torch)",
    )
    args = parser.parse_args()

    configure_logging(json_output=False, level=logging.INFO)

    settings = Settings()
    criteria = settings.get_search_criteria()
    search_areas = settings.get_search_areas()

    logger.info(
        "collecting_data",
        areas=search_areas,
        max_per_scraper=args.max_per_scraper,
        fetch_details=not args.no_details,
    )

    # Scrape
    properties = await scrape_all_platforms(
        min_price=criteria.min_price,
        max_price=criteria.max_price,
        min_bedrooms=criteria.min_bedrooms,
        max_bedrooms=criteria.max_bedrooms,
        search_areas=search_areas,
        furnish_types=settings.get_furnish_types(),
        min_bathrooms=settings.min_bathrooms,
        include_let_agreed=settings.include_let_agreed,
        max_per_scraper=args.max_per_scraper,
        proxy_url=settings.proxy_url,
    )

    logger.info("scraping_done", total=len(properties))

    # Filter to search areas (OpenRent/OTM return radius-based results)
    allowed_outcodes = {oc.upper() for oc in search_areas}
    before = len(properties)
    properties = [p for p in properties if extract_outcode(p.postcode) in allowed_outcodes]
    if len(properties) < before:
        logger.info(
            "outcode_filtered",
            removed=before - len(properties),
            remaining=len(properties),
        )

    # Fetch details
    details = None
    if not args.no_details and properties:
        logger.info("fetching_details", total=len(properties))
        details = await fetch_details(properties, proxy_url=settings.proxy_url)
        logger.info("details_done", fetched=len(details), total=len(properties))

    # Save
    path = save_snapshot(properties, details)
    logger.info("snapshot_saved", path=str(path), count=len(properties))

    # Hash gallery images if requested
    if args.hash_images and not args.no_details:
        from hash_snapshot import hash_snapshot

        logger.info("hashing_gallery_images")
        await hash_snapshot(path)
    elif args.hash_images and args.no_details:
        print("Warning: --hash-images requires detail pages (can't use with --no-details)")

    # Cache images locally (--embed-images implies --cache-images)
    needs_cache = (args.cache_images or args.embed_images) and not args.no_details
    if needs_cache:
        from image_cache import cache_snapshot

        logger.info("caching_gallery_images")
        await cache_snapshot(path)
    elif (args.cache_images or args.embed_images) and args.no_details:
        print(
            "Warning: --cache-images/--embed-images requires detail pages"
            " (can't use with --no-details)"
        )

    # Compute SSCD embeddings if requested
    if args.embed_images and not args.no_details:
        try:
            from embed_snapshot import embed_snapshot

            logger.info("computing_sscd_embeddings")
            embed_snapshot(path)
        except ImportError:
            print(
                "Warning: --embed-images requires torch/torchvision"
                " (install with: uv sync --extra sscd)"
            )

    # Summary
    by_source = {}
    for p in properties:
        by_source.setdefault(p.source.value, 0)
        by_source[p.source.value] += 1

    print(f"\nSnapshot saved: {path}")
    print(f"Total properties: {len(properties)}")
    for source, count in sorted(by_source.items()):
        print(f"  {source}: {count}")
    if details:
        with_desc = sum(1 for d in details.values() if d.description)
        with_gallery = sum(1 for d in details.values() if d.gallery_urls)
        print(
            f"Details fetched: {len(details)} ({with_desc} with description, {with_gallery} with gallery)"
        )


if __name__ == "__main__":
    asyncio.run(main())
