"""Scraper orchestration — platform-level scraping and coordination."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

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
from home_finder.scrapers.base import ScrapeCancelled, ScrapeResult
from home_finder.utils.address import is_outcode
from home_finder.utils.async_tasks import finish_before_cancelling

logger = get_logger(__name__)


@dataclass
class ScraperMetrics:
    """Per-scraper performance metrics for a single pipeline run."""

    scraper_name: str = ""
    started_at: str = ""
    completed_at: str | None = None
    duration_seconds: float = 0.0
    areas_attempted: int = 0
    areas_completed: int = 0
    properties_found: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    parse_errors: int = 0
    is_healthy: bool = True
    error_message: str | None = None


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
    metrics_out: list[ScraperMetrics] | None = None,
    on_observations: Callable[[list[Property]], Awaitable[None]] | None = None,
) -> tuple[list[Property], list[ScraperMetrics]]:
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
        Tuple of (combined list of properties, list of per-scraper metrics).
    """
    areas = list(search_areas or [])
    if not areas:
        logger.warning("no_search_areas_configured")
        return ([], [])
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
    all_metrics = metrics_out if metrics_out is not None else []

    try:
        for scraper in scrapers:
            scraper_known = (
                known_ids_by_source.get(scraper.source.value) if known_ids_by_source else None
            )
            selected: dict[str, Property] = {}
            observed_ids: set[str] = set()
            metrics = ScraperMetrics(
                scraper_name=scraper.source.value,
                started_at=datetime.now(UTC).isoformat(),
            )
            # Register before any await so cancellation still leaves a snapshot.
            all_metrics.append(metrics)
            t_scraper = time.monotonic()
            scraper_areas = areas
            if scraper.max_areas_per_run is not None:
                scraper_areas = areas[: scraper.max_areas_per_run]
                logger.info(
                    "area_subset_applied",
                    platform=scraper.source.value,
                    total_areas=len(areas),
                    subset_size=len(scraper_areas),
                )

            async def consume(
                result: ScrapeResult,
                area: str,
                *,
                metrics: ScraperMetrics = metrics,
                observed_ids: set[str] = observed_ids,
                selected: dict[str, Property] = selected,
            ) -> None:
                metrics.pages_fetched += result.pages_fetched
                metrics.pages_failed += result.pages_failed
                metrics.parse_errors += result.parse_errors
                if result.stop_reason != "cancelled":
                    metrics.areas_completed += 1
                observations = (
                    result.observation_history or result.observations or result.properties
                )
                if is_outcode(area):
                    observations = [
                        p.model_copy(update={"postcode": area.upper()}) if p.postcode is None else p
                        for p in observations
                    ]
                by_id = {p.source_id: p for p in observations}
                observed_ids.update(by_id)
                metrics.properties_found = len(observed_ids)
                for uid in selected:
                    if uid in by_id:
                        selected[uid] = by_id[uid]
                for prop in result.properties:
                    if prop.source_id in selected:
                        continue
                    if max_per_scraper is not None and len(selected) >= max_per_scraper:
                        break
                    selected[prop.source_id] = by_id.get(prop.source_id, prop)
                if on_observations is not None and observations:
                    await on_observations(observations)
                logger.info(
                    "scraping_complete",
                    platform=metrics.scraper_name,
                    area=area,
                    observed=len(observations),
                    selected=len(selected),
                    pages_fetched=result.pages_fetched,
                    pages_failed=result.pages_failed,
                    parse_errors=result.parse_errors,
                    stop_reason=result.stop_reason,
                )

            try:
                for index, area in enumerate(scraper_areas):
                    if max_per_scraper is not None and len(selected) >= max_per_scraper:
                        break
                    if scraper.should_skip_remaining_areas:
                        logger.warning(
                            "skipping_remaining_areas",
                            platform=scraper.source.value,
                            areas_remaining=len(scraper_areas) - index,
                        )
                        break
                    metrics.areas_attempted += 1
                    remaining = (
                        max_per_scraper - len(selected) if max_per_scraper is not None else None
                    )
                    try:
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
                    except ScrapeCancelled as exc:
                        metrics.error_message = "cancelled"
                        await finish_before_cancelling(consume(exc.result, area))
                        raise
                    except asyncio.CancelledError:
                        metrics.error_message = "cancelled"
                        raise
                    except Exception as exc:
                        # Keep previously received observations; a failed area is
                        # not a zero-result success, and does not erase earlier areas.
                        metrics.error_message = type(exc).__name__
                        logger.error(
                            "scraping_failed",
                            platform=scraper.source.value,
                            area=area,
                            error_type=type(exc).__name__,
                        )
                    else:
                        await finish_before_cancelling(consume(result, area))
                    if index < len(scraper_areas) - 1 and (
                        max_per_scraper is None or len(selected) < max_per_scraper
                    ):
                        await scraper.area_delay()
            except asyncio.CancelledError:
                metrics.error_message = "cancelled"
                raise
            finally:
                metrics.completed_at = datetime.now(UTC).isoformat()
                metrics.duration_seconds = time.monotonic() - t_scraper
                metrics.is_healthy = (
                    metrics.pages_fetched > 0
                    and metrics.pages_failed == 0
                    and metrics.parse_errors == 0
                    and metrics.error_message is None
                )
            all_properties.extend(selected.values())
    finally:

        async def close_scrapers() -> None:
            outcomes = await asyncio.gather(
                *(scraper.close() for scraper in scrapers), return_exceptions=True
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome

        await finish_before_cancelling(close_scrapers())

    return all_properties, all_metrics


async def _run_scrape(
    settings: Settings,
    storage: PropertyStorage,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
    metrics_out: list[ScraperMetrics] | None = None,
) -> tuple[list[Property], list[ScraperMetrics]] | None:
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

    async def retain_observations(properties: list[Property]) -> None:
        await storage.upsert_source_listings(properties)
        # Update known prices even when the observation is outside criteria or
        # the processing cap. Source capture alone never queues model inference.
        for prop in properties:
            await storage.detect_and_record_price_change(
                prop.unique_id, prop.price_pcm, source=prop.source.value
            )

    all_properties, scraper_metrics = await scrape_all_platforms(
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
        metrics_out=metrics_out,
        on_observations=retain_observations,
    )
    if metrics_out is not None and scraper_metrics is not metrics_out:
        metrics_out[:] = scraper_metrics
    # Also supports simple adapters/fakes that return selected observations
    # directly. Real paginators return their latest values, so this is idempotent.
    if all_properties:
        await retain_observations(all_properties)
    logger.info(
        "scraping_summary",
        total_found=len(all_properties),
        by_source=_source_counts(all_properties),
    )
    logger.debug("scraping_selection", property_ids=[prop.unique_id for prop in all_properties])

    if not all_properties:
        logger.info("no_properties_found")
        return None

    return all_properties, scraper_metrics
