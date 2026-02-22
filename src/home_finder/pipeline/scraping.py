"""Scraper orchestration — platform-level scraping and coordination."""

import random

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import get_logger
from home_finder.models import (
    FurnishType,
    MergedProperty,
    Property,
)
from home_finder.scrapers import (
    OnTheMarketScraper,
    OpenRentScraper,
    RightmoveScraper,
    ZooplaScraper,
)
from home_finder.utils.address import is_outcode

logger = get_logger(__name__)


def _source_counts(properties: list[Property] | list[MergedProperty]) -> dict[str, int]:
    """Count properties by source for diagnostic logging."""
    counts: dict[str, int] = {}
    for p in properties:
        src = p.source.value if isinstance(p, Property) else p.canonical.source.value
        counts[src] = counts.get(src, 0) + 1
    return counts


async def scrape_all_platforms(
    *,
    min_price: int,
    max_price: int,
    min_bedrooms: int,
    max_bedrooms: int,
    search_areas: list[str] | None = None,
    furnish_types: tuple[FurnishType, ...] = (),
    min_bathrooms: int = 0,
    include_let_agreed: bool = True,
    max_per_scraper: int | None = None,
    known_ids_by_source: dict[str, set[str]] | None = None,
    proxy_url: str = "",
    only_scrapers: set[str] | None = None,
    zoopla_max_areas: int | None = None,
) -> list[Property]:
    """Scrape all platforms for matching properties.

    Args:
        min_price: Minimum monthly rent.
        max_price: Maximum monthly rent.
        min_bedrooms: Minimum bedrooms.
        max_bedrooms: Maximum bedrooms.
        search_areas: Areas to search (boroughs or outcodes).
        furnish_types: Furnishing types to include.
        min_bathrooms: Minimum number of bathrooms.
        include_let_agreed: Whether to include already-let properties.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
        known_ids_by_source: Known source IDs per platform for early-stop pagination.
        only_scrapers: If set, only run scrapers whose source value is in this set.
        zoopla_max_areas: Max areas for Zoopla scraper (None for unlimited).

    Returns:
        Combined list of properties from all platforms.
    """
    areas = list(search_areas or [])
    if not areas:
        logger.warning("no_search_areas_configured")
        return []
    # Shuffle so rate-limited scrapers (Zoopla) don't always block the same areas
    random.shuffle(areas)
    scrapers = [
        OpenRentScraper(proxy_url=proxy_url),
        RightmoveScraper(),
        ZooplaScraper(proxy_url=proxy_url, max_areas=zoopla_max_areas),
        OnTheMarketScraper(proxy_url=proxy_url),
    ]
    if only_scrapers:
        scrapers = [s for s in scrapers if s.source.value in only_scrapers]

    all_properties: list[Property] = []

    try:
        for scraper in scrapers:
            scraper_known = (
                known_ids_by_source.get(scraper.source.value) if known_ids_by_source else None
            )
            scraper_count = 0
            scraper_seen_ids: set[str] = set()

            # Apply per-scraper area limit (e.g. Zoopla rotates a subset)
            scraper_areas = areas
            if scraper.max_areas_per_run is not None:
                scraper_areas = areas[: scraper.max_areas_per_run]
                if len(scraper_areas) < len(areas):
                    logger.info(
                        "area_subset_applied",
                        platform=scraper.source.value,
                        total_areas=len(areas),
                        subset_size=len(scraper_areas),
                    )

            for i, area in enumerate(scraper_areas):
                if max_per_scraper is not None and scraper_count >= max_per_scraper:
                    break

                if scraper.should_skip_remaining_areas:
                    logger.warning(
                        "skipping_remaining_areas",
                        platform=scraper.source.value,
                        skipped_from=area,
                        areas_remaining=len(scraper_areas) - i,
                    )
                    break

                try:
                    logger.info(
                        "scraping_platform",
                        platform=scraper.source.value,
                        area=area,
                    )
                    remaining = (
                        max_per_scraper - scraper_count if max_per_scraper is not None else None
                    )
                    result = await scraper.scrape(
                        min_price=min_price,
                        max_price=max_price,
                        min_bedrooms=min_bedrooms,
                        max_bedrooms=max_bedrooms,
                        area=area,
                        furnish_types=furnish_types,
                        min_bathrooms=min_bathrooms,
                        include_let_agreed=include_let_agreed,
                        max_results=remaining,
                        known_source_ids=scraper_known,
                    )
                    properties = result.properties

                    if not result.is_healthy:
                        logger.warning(
                            "scraper_unhealthy",
                            platform=scraper.source.value,
                            area=area,
                            pages_fetched=result.pages_fetched,
                            pages_failed=result.pages_failed,
                            parse_errors=result.parse_errors,
                        )

                    # Cross-area dedup: remove properties already seen in other areas
                    before_dedup = len(properties)
                    properties = [p for p in properties if p.source_id not in scraper_seen_ids]
                    scraper_seen_ids.update(p.source_id for p in properties)
                    if len(properties) < before_dedup:
                        logger.info(
                            "cross_area_dedup",
                            platform=scraper.source.value,
                            area=area,
                            removed=before_dedup - len(properties),
                        )
                    # Backfill outcode for properties missing postcode
                    if is_outcode(area):
                        outcode = area.upper()
                        properties = [
                            p.model_copy(update={"postcode": outcode}) if p.postcode is None else p
                            for p in properties
                        ]
                    scraper_count += len(properties)
                    all_properties.extend(properties)
                    logger.info(
                        "scraping_complete",
                        platform=scraper.source.value,
                        area=area,
                        count=len(properties),
                        pages_fetched=result.pages_fetched,
                        pages_failed=result.pages_failed,
                    )
                except Exception as e:
                    logger.error(
                        "scraping_failed",
                        platform=scraper.source.value,
                        area=area,
                        error=str(e),
                        exc_info=True,
                    )
                # Delegate inter-area delay to the scraper
                if i < len(scraper_areas) - 1:
                    await scraper.area_delay()
    finally:
        for scraper in scrapers:
            await scraper.close()

    return all_properties


async def _run_scrape(
    settings: Settings,
    storage: PropertyStorage,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
) -> list[Property] | None:
    """Scrape all platforms and return results, or None if nothing found."""
    criteria = settings.get_search_criteria()
    search_areas = settings.get_search_areas()

    if full_scrape:
        known_ids_by_source = None
        logger.info("full_scrape_mode", msg="Early-stop disabled — scraping all pages")
    else:
        known_ids_by_source = await storage.get_all_known_source_ids()
        logger.info(
            "loaded_known_ids",
            total=sum(len(v) for v in known_ids_by_source.values()),
        )

    logger.info("pipeline_started", phase="scraping")
    all_properties = await scrape_all_platforms(
        min_price=criteria.min_price,
        max_price=criteria.max_price,
        min_bedrooms=criteria.min_bedrooms,
        max_bedrooms=criteria.max_bedrooms,
        search_areas=search_areas,
        furnish_types=settings.get_furnish_types(),
        min_bathrooms=settings.min_bathrooms,
        include_let_agreed=settings.include_let_agreed,
        max_per_scraper=max_per_scraper,
        known_ids_by_source=known_ids_by_source,
        proxy_url=settings.proxy_url,
        only_scrapers=only_scrapers,
        zoopla_max_areas=settings.zoopla_max_areas_per_run,
    )
    logger.info(
        "scraping_summary",
        total_found=len(all_properties),
        by_source=_source_counts(all_properties),
    )

    if not all_properties:
        logger.info("no_properties_found")
        return None

    return all_properties
