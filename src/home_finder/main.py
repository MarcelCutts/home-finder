"""Main entry point for the home finder scraper."""

import argparse
import asyncio
import random
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

import httpx

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters import (
    CommuteFilter,
    CriteriaFilter,
    Deduplicator,
    LocationFilter,
    PropertyQualityFilter,
    enrich_merged_properties,
    filter_by_floorplan,
)
from home_finder.filters.quality import APIUnavailableError
from home_finder.logging import configure_logging, get_logger
from home_finder.models import (
    FurnishType,
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    PropertySource,
    SearchCriteria,
    TransportMode,
)
from home_finder.notifiers import TelegramNotifier
from home_finder.scrapers import (
    OnTheMarketScraper,
    OpenRentScraper,
    RightmoveScraper,
    ZooplaScraper,
)
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.utils.address import is_outcode
from home_finder.utils.image_cache import clear_image_cache, copy_cached_images

logger = get_logger(__name__)


def _source_counts(properties: list[Property] | list[MergedProperty]) -> dict[str, int]:
    """Count properties by source for diagnostic logging."""
    counts: dict[str, int] = {}
    for p in properties:
        src = p.source.value if isinstance(p, Property) else p.canonical.source.value
        counts[src] = counts.get(src, 0) + 1
    return counts


_QUALITY_CONCURRENCY: Final = 15


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
        OpenRentScraper(),
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
                    properties = await scraper.scrape(
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


@dataclass
class CrossRunDedupResult:
    """Result of cross-run deduplication against DB anchors."""

    genuinely_new: list[MergedProperty] = field(default_factory=list)
    anchors_updated: int = 0


@dataclass
class PreAnalysisResult:
    """Result of the pre-analysis pipeline (scrape -> filter -> enrich)."""

    merged_to_process: list[MergedProperty] = field(default_factory=list)
    commute_lookup: dict[str, tuple[int, TransportMode]] = field(default_factory=dict)
    scraped_count: int = 0
    enriched_count: int = 0
    anchors_updated: int = 0


async def _cross_run_deduplicate(
    deduplicator: Deduplicator,
    merged_to_notify: list[MergedProperty],
    storage: PropertyStorage,
    re_enrichment_ids: set[str],
) -> CrossRunDedupResult:
    """Deduplicate new properties against recent DB anchors (cross-run detection).

    Loads recent DB properties as anchors so a property appearing on platform B
    today can be matched against platform A stored last week. Updates anchors
    that gained new sources and cleans up consumed retry rows.

    Args:
        deduplicator: Deduplicator instance for cross-platform matching.
        merged_to_notify: New/re-enriched properties to check.
        storage: Database storage for anchor lookup and updates.
        re_enrichment_ids: IDs of properties being re-enriched (for cleanup).

    Returns:
        CrossRunDedupResult with genuinely new properties and update count.
    """
    db_anchors = await storage.get_recent_properties_for_dedup(days=30)
    logger.info("loaded_dedup_anchors", anchor_count=len(db_anchors))

    # Map each anchor's source URLs to its DB unique_id so we can detect
    # merges even when the deduplicator picks a different canonical.
    anchor_url_to_id: dict[str, str] = {}
    anchor_by_id: dict[str, MergedProperty] = {}
    for anchor in db_anchors:
        anchor_by_id[anchor.canonical.unique_id] = anchor
        for url in anchor.source_urls.values():
            anchor_url_to_id[str(url)] = anchor.canonical.unique_id

    # Combine new properties with DB anchors for dedup comparison
    combined_for_dedup = merged_to_notify + db_anchors
    dedup_results = await deduplicator.deduplicate_merged_async(combined_for_dedup)

    # Split: anchors that gained new sources vs genuinely new properties
    genuinely_new: list[MergedProperty] = []
    anchors_updated = 0
    for merged in dedup_results:
        # Check if this result involves any DB anchor (by URL match)
        matched_anchor_id: str | None = None
        for url in merged.source_urls.values():
            aid = anchor_url_to_id.get(str(url))
            if aid is not None:
                matched_anchor_id = aid
                break

        if matched_anchor_id is not None:
            original_anchor = anchor_by_id[matched_anchor_id]
            if set(merged.sources) != set(original_anchor.sources):
                await storage.update_merged_sources(matched_anchor_id, merged)
                anchors_updated += 1
                logger.info(
                    "cross_run_duplicate_detected",
                    anchor_id=matched_anchor_id,
                    new_sources=[s.value for s in merged.sources],
                    original_sources=[s.value for s in original_anchor.sources],
                )
        else:
            genuinely_new.append(merged)

    # Clean up unenriched DB rows consumed by anchor merges.
    # When a re-enriched property merges into an existing anchor,
    # update_merged_sources adds data to the anchor but the old
    # unenriched row still exists — delete it.
    genuinely_new_ids = {m.unique_id for m in genuinely_new}
    consumed_retries = re_enrichment_ids - genuinely_new_ids
    for uid in consumed_retries:
        await storage.delete_property(uid)
        logger.debug("consumed_retry_cleaned", unique_id=uid)

    logger.info(
        "deduplication_merge_summary",
        dedup_input=len(combined_for_dedup),
        dedup_output=len(dedup_results),
        genuinely_new=len(genuinely_new),
        anchors_updated=anchors_updated,
        consumed_retries=len(consumed_retries),
        multi_source_count=sum(1 for m in genuinely_new if len(m.sources) > 1),
        by_source=_source_counts(genuinely_new),
    )

    return CrossRunDedupResult(genuinely_new=genuinely_new, anchors_updated=anchors_updated)


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


def _run_criteria_and_location_filters(
    properties: list[Property],
    criteria: SearchCriteria,
    search_areas: list[str],
) -> list[Property] | None:
    """Apply criteria and location filters. Returns None if nothing passes."""
    logger.info("pipeline_started", phase="criteria_filtering")
    criteria_filter = CriteriaFilter(criteria)
    filtered = criteria_filter.filter_properties(properties)
    logger.info(
        "criteria_filter_summary", matched=len(filtered), by_source=_source_counts(filtered)
    )

    if not filtered:
        logger.info("no_properties_match_criteria")
        return None

    logger.info("pipeline_started", phase="location_filtering")
    location_filter = LocationFilter(search_areas, strict=False)
    filtered = location_filter.filter_properties(filtered)
    logger.info(
        "location_filter_summary", matched=len(filtered), by_source=_source_counts(filtered)
    )

    if not filtered:
        logger.info("no_properties_in_search_areas")
        return None

    return filtered


async def _load_unenriched(
    storage: PropertyStorage,
    settings: Settings,
) -> tuple[list[MergedProperty], set[str]]:
    """Load unenriched properties for retry, clearing stale image caches."""
    max_attempts = settings.max_enrichment_attempts
    unenriched = await storage.get_unenriched_properties(max_attempts=max_attempts)
    re_enrichment_ids: set[str] = set()
    if unenriched:
        logger.info(
            "loaded_unenriched_for_retry",
            count=len(unenriched),
            by_source=_source_counts(unenriched),
        )
        for m in unenriched:
            if settings.data_dir:
                clear_image_cache(settings.data_dir, m.unique_id)
        re_enrichment_ids = {m.unique_id for m in unenriched}
    return unenriched, re_enrichment_ids


async def _geocode_and_compute_commute(
    merged: list[MergedProperty],
    criteria: SearchCriteria,
    settings: Settings,
) -> tuple[list[MergedProperty], dict[str, tuple[int, TransportMode]]]:
    """Geocode properties and compute commute times via TravelTime API.

    This is NOT a hard filter — it returns all properties (geocoded) plus a
    lookup of commute times for those within the configured limit.  Properties
    without coordinates or beyond the commute limit are still returned so that
    downstream steps receive geocoded data.  The only filtering is an early-exit
    gate: if TravelTime is configured and zero properties are reachable, the
    caller can abort the pipeline.

    Returns:
        Tuple of (all properties with geocoded coordinates, commute lookup
        mapping unique_id -> (minutes, transport_mode) for reachable properties).
    """
    commute_lookup: dict[str, tuple[int, TransportMode]] = {}
    if settings.traveltime_app_id and settings.traveltime_api_key:
        logger.info("pipeline_started", phase="commute_filtering")
        commute_filter = CommuteFilter(
            app_id=settings.traveltime_app_id,
            api_key=settings.traveltime_api_key.get_secret_value(),
            destination_postcode=criteria.destination_postcode,
        )

        merged = await commute_filter.geocode_properties(merged)

        merged_with_coords = [
            m for m in merged if m.canonical.latitude and m.canonical.longitude
        ]
        merged_without_coords = [
            m for m in merged if not (m.canonical.latitude and m.canonical.longitude)
        ]

        props_with_coords = [m.canonical for m in merged_with_coords]

        commute_results = []
        if props_with_coords:
            for mode in criteria.transport_modes:
                results = await commute_filter.filter_properties(
                    props_with_coords,
                    max_minutes=criteria.max_commute_minutes,
                    transport_mode=mode,
                )
                commute_results.extend(results)

        for result in commute_results:
            if result.within_limit and (
                result.property_id not in commute_lookup
                or result.travel_time_minutes < commute_lookup[result.property_id][0]
            ):
                commute_lookup[result.property_id] = (
                    result.travel_time_minutes,
                    result.transport_mode,
                )

        within_limit = [
            m for m in merged_with_coords if m.canonical.unique_id in commute_lookup
        ]
        notify_count = len(within_limit) + len(merged_without_coords)

        logger.info(
            "commute_filter_summary",
            within_limit=notify_count,
            total_checked=len(merged_with_coords),
            without_coords=len(merged_without_coords),
            by_source=_source_counts(merged),
        )

        if not within_limit and not merged_without_coords:
            return [], commute_lookup
    else:
        logger.info("skipping_commute_filter", reason="no_traveltime_credentials")

    return merged, commute_lookup


async def _run_enrichment(
    merged: list[MergedProperty],
    settings: Settings,
    storage: PropertyStorage,
) -> list[MergedProperty] | None:
    """Enrich with detail page data and handle failures. Returns None if nothing enriched."""
    logger.info("pipeline_started", phase="detail_enrichment")
    detail_fetcher = DetailFetcher(
        max_gallery_images=settings.quality_filter_max_images,
        proxy_url=settings.proxy_url,
    )
    try:
        enrichment_result = await enrich_merged_properties(
            merged,
            detail_fetcher,
            data_dir=settings.data_dir,
            storage=storage,
        )
    finally:
        await detail_fetcher.close()

    enriched = enrichment_result.enriched

    for failed in enrichment_result.failed:
        await storage.save_unenriched_property(failed)

    max_attempts = settings.max_enrichment_attempts
    expired = await storage.expire_unenriched(max_attempts=max_attempts)

    logger.info(
        "enrichment_summary",
        enriched=len(enrichment_result.enriched),
        failed=len(enrichment_result.failed),
        expired=expired,
        with_floorplan=sum(1 for m in enriched if m.floorplan),
        with_images=sum(1 for m in enriched if m.images),
        by_source=_source_counts(enriched),
    )

    if not enriched:
        logger.info("no_enriched_properties")
        return None

    return enriched


async def _run_post_enrichment(
    merged: list[MergedProperty],
    deduplicator: Deduplicator,
    storage: PropertyStorage,
    settings: Settings,
    re_enrichment_ids: set[str],
) -> tuple[list[MergedProperty], int] | None:
    """Cross-run dedup and floorplan gate. Returns None if nothing remains."""
    logger.info("pipeline_started", phase="deduplication_merge")
    dedup_result = await _cross_run_deduplicate(
        deduplicator, merged, storage, re_enrichment_ids
    )
    merged_to_notify = dedup_result.genuinely_new
    anchors_updated = dedup_result.anchors_updated

    if not merged_to_notify:
        logger.info("no_new_properties_after_cross_run_dedup")
        return None

    if settings.require_floorplan:
        before_count = len(merged_to_notify)
        merged_to_notify = filter_by_floorplan(merged_to_notify)
        logger.info(
            "floorplan_filter",
            before=before_count,
            after=len(merged_to_notify),
            dropped=before_count - len(merged_to_notify),
            by_source=_source_counts(merged_to_notify),
        )

        if not merged_to_notify:
            logger.info("no_properties_with_floorplans")
            return None

    return merged_to_notify, anchors_updated


async def _run_pre_analysis_pipeline(
    settings: Settings,
    storage: PropertyStorage,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
) -> PreAnalysisResult | None:
    """Run the pre-analysis pipeline: scrape, filter, deduplicate, enrich.

    Returns None if pipeline ends early (no properties at any stage).
    Quality analysis is handled separately by the caller for concurrency.
    """
    # Step 1: Scrape all platforms
    all_properties = await _run_scrape(
        settings,
        storage,
        max_per_scraper=max_per_scraper,
        only_scrapers=only_scrapers,
        full_scrape=full_scrape,
    )
    if all_properties is None:
        return None

    # Step 2: Criteria + location filters
    criteria = settings.get_search_criteria()
    search_areas = settings.get_search_areas()
    filtered = _run_criteria_and_location_filters(all_properties, criteria, search_areas)
    if filtered is None:
        return None

    # Step 3: Wrap as single-source MergedProperties + filter to new only
    logger.info("pipeline_started", phase="wrap_merged")
    deduplicator = Deduplicator(
        enable_cross_platform=True,
        enable_image_hashing=settings.enable_image_hash_matching,
        data_dir=settings.data_dir,
    )
    merged_properties = deduplicator.properties_to_merged(filtered)
    logger.info(
        "wrap_merged_summary",
        count=len(merged_properties),
        by_source=_source_counts(merged_properties),
    )

    logger.info("pipeline_started", phase="new_property_filter")
    new_merged = await storage.filter_new_merged(merged_properties)
    logger.info(
        "new_property_summary", new_count=len(new_merged), by_source=_source_counts(new_merged)
    )

    # Step 4: Load unenriched retries
    unenriched, re_enrichment_ids = await _load_unenriched(storage, settings)
    if not new_merged and not unenriched:
        logger.info("no_new_properties")
        return None

    # Step 5: Enrichment
    merged_to_enrich = list(new_merged) + list(unenriched)
    enriched = await _run_enrichment(merged_to_enrich, settings, storage)
    if enriched is None:
        return None

    # Step 6: Geocode + commute (after enrichment so all properties have full coords)
    geocoded, commute_lookup = await _geocode_and_compute_commute(
        enriched, criteria, settings
    )
    if not geocoded:
        logger.info("no_properties_within_commute_limit")
        return None

    # Step 7: Post-enrichment dedup + floorplan gate
    # Use geocoded list (not enriched) so coordinates from geocoding are preserved
    post_result = await _run_post_enrichment(
        geocoded, deduplicator, storage, settings, re_enrichment_ids
    )
    if post_result is None:
        return None

    final_merged, anchors_updated = post_result
    return PreAnalysisResult(
        merged_to_process=final_merged,
        commute_lookup=commute_lookup,
        scraped_count=len(all_properties),
        enriched_count=len(enriched),
        anchors_updated=anchors_updated,
    )


async def _save_one(
    merged: MergedProperty,
    commute_info: tuple[int, TransportMode] | None,
    quality_analysis: PropertyQualityAnalysis | None,
    storage: PropertyStorage,
) -> None:
    """Complete analysis for a single property and transition to notification-ready.

    Called after quality analysis completes. The property was already saved to DB
    by save_pre_analysis_properties() before analysis started.
    """
    # Save quality data and transition notification_status to 'pending'
    await storage.complete_analysis(merged.unique_id, quality_analysis)

    # Transition re-enriched properties into normal notification flow.
    # No-op for genuinely new properties (already handled by complete_analysis).
    await storage.mark_enriched(merged.unique_id)


# Type aliases for quality analysis callbacks
_CommInfo = tuple[int, TransportMode] | None
_OnResult = Callable[
    [MergedProperty, _CommInfo, PropertyQualityAnalysis | None],
    Awaitable[None],
]
_AnalyzeFn = Callable[
    [MergedProperty],
    Awaitable[tuple[MergedProperty, PropertyQualityAnalysis | None]],
]
_OnAnalysisResult = Callable[
    [MergedProperty, PropertyQualityAnalysis | None],
    Awaitable[None],
]


async def _lookup_wards(storage: PropertyStorage) -> None:
    """Look up ward names via postcodes.io for properties missing them."""
    from home_finder.utils.postcode_lookup import (
        bulk_reverse_lookup_wards,
        lookup_ward,
    )

    props = await storage.get_properties_without_ward()
    if not props:
        return

    # Split into coordinate-based (reverse geocode) and full-postcode (forward)
    coord_props: list[dict[str, object]] = []
    postcode_props: list[dict[str, object]] = []
    for p in props:
        if p.get("latitude") and p.get("longitude"):
            coord_props.append(p)
        elif p.get("postcode") and " " in str(p["postcode"]):
            # Full postcode has a space (e.g. "E8 3RH")
            postcode_props.append(p)

    ward_map: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        # Bulk reverse geocode for properties with coordinates
        if coord_props:
            coords = [
                (float(p["latitude"]), float(p["longitude"]))  # type: ignore[arg-type]
                for p in coord_props
            ]
            wards = await bulk_reverse_lookup_wards(coords, client=client)
            for p, ward in zip(coord_props, wards, strict=True):
                if ward:
                    ward_map[str(p["unique_id"])] = ward

        # Forward lookup for properties with full postcodes (no coordinates)
        for p in postcode_props:
            ward = await lookup_ward(str(p["postcode"]), client=client)
            if ward:
                ward_map[str(p["unique_id"])] = ward

    if ward_map:
        updated = await storage.update_wards(ward_map)
        logger.info("ward_lookup_complete", updated=updated, total=len(props))


async def _run_concurrent_analysis(
    items: list[MergedProperty],
    analyze_fn: _AnalyzeFn,
    on_result: _OnAnalysisResult,
    *,
    on_error: Callable[[], None] | None = None,
    breaker_log_event: str = "api_circuit_breaker_activated",
    error_log_event: str = "analysis_failed",
) -> int:
    """Run quality analysis concurrently with circuit breaker protection.

    Executes analyze_fn for each item under a semaphore, using as_completed
    for streaming results. On APIUnavailableError, cancels remaining tasks.

    Args:
        items: Properties to analyze.
        analyze_fn: Async function that analyzes a single property.
        on_result: Async callback invoked with (merged, analysis) for each success.
        on_error: Optional sync callback invoked on per-property errors.
        breaker_log_event: Log event name for circuit breaker activation.
        error_log_event: Log event name for per-property errors.

    Returns:
        Number of properties successfully processed.
    """
    semaphore = asyncio.Semaphore(_QUALITY_CONCURRENCY)
    count = 0

    async def _bounded(
        merged: MergedProperty,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
        async with semaphore:
            return await analyze_fn(merged)

    tasks = [asyncio.create_task(_bounded(m)) for m in items]

    for coro in asyncio.as_completed(tasks):
        try:
            merged, quality_analysis = await coro
        except APIUnavailableError:
            remaining = [t for t in tasks if not t.done()]
            for t in remaining:
                t.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
            logger.warning(
                breaker_log_event,
                deferred_properties=len(remaining),
                processed=count,
            )
            break
        except Exception:
            logger.error(error_log_event, exc_info=True)
            if on_error:
                on_error()
            continue

        await on_result(merged, quality_analysis)
        count += 1

    return count


async def _run_quality_and_save(
    pre: PreAnalysisResult,
    settings: Settings,
    storage: PropertyStorage,
    on_result: _OnResult,
) -> int:
    """Run quality analysis, save each property, and invoke callback.

    Properties are saved to DB *before* analysis starts (with notification_status
    'pending_analysis'). If the process crashes mid-analysis, the next run picks
    them up via get_pending_analysis_properties().

    Args:
        pre: Pre-analysis pipeline result.
        settings: Application settings.
        storage: Database storage.
        on_result: Async callback invoked after each property is analyzed.

    Returns:
        Number of properties processed.
    """
    # Save all properties to DB before analysis (crash recovery checkpoint)
    await storage.save_pre_analysis_properties(pre.merged_to_process, pre.commute_lookup)

    # Ward lookup: populate ward column for properties with coordinates
    await _lookup_wards(storage)

    # Reset properties with fallback analysis (API failed previously)
    # so they get re-analyzed this run
    reset_count = await storage.reset_failed_analyses()
    if reset_count:
        logger.info("reset_failed_analyses_for_retry", count=reset_count)

    # Load any pending_analysis properties from previous crashed runs
    current_ids = {m.unique_id for m in pre.merged_to_process}
    pending_from_prev = await storage.get_pending_analysis_properties(exclude_ids=current_ids)
    retried = list(pending_from_prev)
    if retried:
        logger.info("retrying_pending_analysis_from_previous_run", count=len(retried))

    all_to_analyze = list(pre.merged_to_process) + retried

    use_quality = settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter
    quality_filter: PropertyQualityFilter | None = None
    if use_quality:
        quality_filter = PropertyQualityFilter(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
            thinking_budget_tokens=settings.thinking_budget_tokens,
        )
    else:
        logger.info("skipping_quality_analysis", reason="not_configured")

    async def _analyze(
        merged: MergedProperty,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
        if quality_filter:
            return await quality_filter.analyze_single_merged(merged, data_dir=settings.data_dir)
        return merged, None

    async def _handle_result(
        merged: MergedProperty, quality_analysis: PropertyQualityAnalysis | None
    ) -> None:
        commute_info = pre.commute_lookup.get(merged.canonical.unique_id)
        await _save_one(merged, commute_info, quality_analysis, storage)
        await on_result(merged, commute_info, quality_analysis)

    try:
        count = await _run_concurrent_analysis(
            all_to_analyze,
            _analyze,
            _handle_result,
            breaker_log_event="api_circuit_breaker_activated",
            error_log_event="property_processing_failed",
        )
    finally:
        if quality_filter:
            await quality_filter.close()

    return count


async def run_pipeline(
    settings: Settings,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
) -> None:
    """Run the full scraping and notification pipeline.

    Quality analysis runs concurrently (bounded by semaphore), and each
    property is saved + notified as soon as its analysis completes.

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
        only_scrapers: If set, only run these scrapers (by source value).
        full_scrape: Disable early-stop pagination (scrape all pages).
    """
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    run_id = await storage.create_pipeline_run()
    logger.info("pipeline_run_created", run_id=run_id)

    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_chat_id,
        web_base_url=settings.web_base_url,
    )

    try:
        # Step 0: Retry any unsent notifications from previous runs
        unsent = await storage.get_unsent_notifications()
        if unsent:
            logger.info("retrying_unsent_notifications", count=len(unsent))
            for tracked in unsent:
                success = await notifier.send_property_notification(
                    tracked.property,
                    commute_minutes=tracked.commute_minutes,
                    transport_mode=tracked.transport_mode,
                )
                if success:
                    await storage.mark_notified(tracked.property.unique_id)
                    logger.info("retry_notification_sent", unique_id=tracked.property.unique_id)
                else:
                    logger.warning(
                        "retry_notification_failed", unique_id=tracked.property.unique_id
                    )
                await asyncio.sleep(1)

        pre = await _run_pre_analysis_pipeline(
            settings,
            storage,
            max_per_scraper=max_per_scraper,
            only_scrapers=only_scrapers,
            full_scrape=full_scrape,
        )
        if pre is None:
            await storage.complete_pipeline_run(run_id, "completed")
            return

        await storage.update_pipeline_run(
            run_id,
            scraped_count=pre.scraped_count,
            new_count=len(pre.merged_to_process),
            enriched_count=pre.enriched_count,
            anchors_updated=pre.anchors_updated,
        )

        logger.info(
            "pipeline_started",
            phase="quality_analysis_and_notify",
            count=len(pre.merged_to_process),
        )

        notified_count = 0

        async def _notify(
            merged: MergedProperty,
            commute_info: _CommInfo,
            quality_analysis: PropertyQualityAnalysis | None,
        ) -> None:
            nonlocal notified_count

            # Skip notification for properties with no images — not useful
            if not merged.images and not merged.canonical.image_url:
                await storage.mark_notified(merged.unique_id)
                return

            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None

            success = await notifier.send_merged_property_notification(
                merged,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
                quality_analysis=quality_analysis,
            )

            if success:
                await storage.mark_notified(merged.unique_id)
                notified_count += 1
            else:
                await storage.mark_notification_failed(merged.unique_id)

            await asyncio.sleep(1)  # Telegram rate limit

        analyzed_count = await _run_quality_and_save(pre, settings, storage, _notify)
        await storage.update_pipeline_run(
            run_id,
            analyzed_count=analyzed_count,
            notified_count=notified_count,
        )
        await storage.complete_pipeline_run(run_id, "completed")
        logger.info("pipeline_complete", notified=notified_count)

    except Exception as exc:
        await storage.complete_pipeline_run(run_id, "failed", error_message=str(exc))
        raise
    finally:
        await notifier.close()
        await storage.close()


async def run_scrape_only(
    settings: Settings,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
) -> None:
    """Run scraping only and print results (no filtering, storage, or notifications).

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
        only_scrapers: If set, only run these scrapers (by source value).
    """
    criteria = settings.get_search_criteria()
    search_areas = settings.get_search_areas()

    logger.info("scrape_only_started")
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
        proxy_url=settings.proxy_url,
        only_scrapers=only_scrapers,
        zoopla_max_areas=settings.zoopla_max_areas_per_run,
    )

    print(f"\n{'=' * 60}")
    print(f"Scraped {len(all_properties)} properties")
    print(f"{'=' * 60}\n")

    for prop in all_properties:
        print(f"[{prop.source.value}] {prop.title}")
        print(f"  Price: £{prop.price_pcm}/month | Beds: {prop.bedrooms}")
        print(f"  Address: {prop.address}")
        if prop.postcode:
            print(f"  Postcode: {prop.postcode}")
        print(f"  URL: {prop.url}")
        print()


async def run_dry_run(
    settings: Settings,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
) -> None:
    """Run the full pipeline without sending Telegram notifications.

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
        only_scrapers: If set, only run these scrapers (by source value).
        full_scrape: Disable early-stop pagination (scrape all pages).
    """
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    try:
        pre = await _run_pre_analysis_pipeline(
            settings,
            storage,
            max_per_scraper=max_per_scraper,
            only_scrapers=only_scrapers,
            full_scrape=full_scrape,
        )
        if pre is None:
            print("\nNo new properties to report.")
            return

        logger.info(
            "pipeline_started",
            phase="quality_analysis_and_save",
            count=len(pre.merged_to_process),
            dry_run=True,
        )

        # Accumulate results for summary printout
        processed: list[tuple[MergedProperty, _CommInfo, PropertyQualityAnalysis | None]] = []

        async def _accumulate(
            merged: MergedProperty,
            commute_info: _CommInfo,
            quality_analysis: PropertyQualityAnalysis | None,
        ) -> None:
            processed.append((merged, commute_info, quality_analysis))

        saved_count = await _run_quality_and_save(pre, settings, storage, _accumulate)

        # Print summary
        print(f"\n{'=' * 60}")
        print(f"[DRY RUN] Would notify about {len(processed)} properties:")
        print(f"{'=' * 60}\n")

        for merged, commute_info, quality_analysis in processed:
            prop = merged.canonical
            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None

            source_str = ", ".join(s.value for s in merged.sources)
            print(f"[{source_str}] {prop.title}")

            if merged.price_varies:
                print(
                    f"  Price: £{merged.min_price}-£{merged.max_price}/month | "
                    f"Beds: {prop.bedrooms}"
                )
            else:
                print(f"  Price: £{prop.price_pcm}/month | Beds: {prop.bedrooms}")

            print(f"  Address: {prop.address}")
            if prop.postcode:
                print(f"  Postcode: {prop.postcode}")
            if commute_minutes is not None:
                mode_str = transport_mode.value if transport_mode else ""
                print(f"  Commute: {commute_minutes} min ({mode_str})")
            if len(merged.sources) > 1:
                print(f"  Listed on: {len(merged.sources)} platforms")
            if merged.images or merged.floorplan:
                img_str = f"{len(merged.images)} images"
                if merged.floorplan:
                    img_str += " + floorplan"
                print(f"  Photos: {img_str}")
            if quality_analysis:
                print(f"  Summary: {quality_analysis.summary}")
                if quality_analysis.condition_concerns:
                    print(
                        f"  Condition concerns ({quality_analysis.concern_severity}): "
                        f"{', '.join(quality_analysis.condition.maintenance_concerns)}"
                    )
                sqm_str = (
                    f"~{quality_analysis.space.living_room_sqm:.0f}sqm"
                    if quality_analysis.space.living_room_sqm
                    else "size unknown"
                )
                print(f"  Living room: {sqm_str}")
                if quality_analysis.value and quality_analysis.value.rating:
                    print(
                        f"  Value: {quality_analysis.value.rating} ({quality_analysis.value.note})"
                    )
            print(f"  URL: {prop.url}")
            print()

        logger.info("dry_run_complete", saved=saved_count)

    finally:
        await storage.close()


async def run_reanalysis(
    settings: Settings,
    *,
    outcodes: list[str] | None = None,
    reanalyze_all: bool = False,
    request_only: bool = False,
) -> None:
    """Re-run quality analysis on flagged properties.

    Args:
        settings: Application settings.
        outcodes: Outcodes to flag for re-analysis.
        reanalyze_all: Flag all analyzed properties.
        request_only: Only flag, don't run analysis.
    """
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    try:
        # Step 1: Flag properties if outcodes or --all provided
        if outcodes or reanalyze_all:
            flagged = await storage.request_reanalysis_by_filter(
                outcodes=outcodes, all_properties=reanalyze_all
            )
            target = "all properties" if reanalyze_all else f"outcodes {', '.join(outcodes or [])}"
            print(f"Marked {flagged} properties for re-analysis ({target}).")

        if request_only:
            return

        # Step 2: Load queue
        queue = await storage.get_reanalysis_queue()

        if not queue:
            print("No properties queued for re-analysis.")
            return

        # Step 3: Cost estimate
        est_cost = len(queue) * 0.06
        print(f"Re-analyzing {len(queue)} properties (est. ~${est_cost:.2f})")

        # Step 4: Run quality analysis
        use_quality = (
            settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter
        )
        if not use_quality:
            print("Error: Quality analysis not configured (need ANTHROPIC_API_KEY).")
            return

        quality_filter = PropertyQualityFilter(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
            thinking_budget_tokens=settings.thinking_budget_tokens,
        )

        completed = 0
        failed = 0

        async def _analyze(
            merged: MergedProperty,
        ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
            return await quality_filter.analyze_single_merged(merged, data_dir=settings.data_dir)

        async def _handle_result(
            merged: MergedProperty,
            quality_analysis: PropertyQualityAnalysis | None,
        ) -> None:
            nonlocal completed, failed
            if quality_analysis:
                await storage.complete_reanalysis(merged.unique_id, quality_analysis)
                completed += 1
            else:
                failed += 1

        def _count_error() -> None:
            nonlocal failed
            failed += 1

        try:
            await _run_concurrent_analysis(
                queue,
                _analyze,
                _handle_result,
                on_error=_count_error,
                breaker_log_event="reanalysis_api_circuit_breaker",
                error_log_event="reanalysis_failed",
            )
        finally:
            await quality_filter.close()

        print(f"\nRe-analysis complete: {completed} updated, {failed} failed.")

    finally:
        await storage.close()


async def run_backfill_commute(settings: Settings) -> None:
    """Backfill commute data for properties that have coordinates but no commute_minutes.

    Queries the DB for such properties, runs the TravelTime API, and updates the DB.
    """
    if not settings.traveltime_app_id or not settings.traveltime_api_key:
        print("Error: TravelTime credentials not configured.")
        print("Set HOME_FINDER_TRAVELTIME_APP_ID and HOME_FINDER_TRAVELTIME_API_KEY.")
        return

    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    try:
        criteria = settings.get_search_criteria()

        properties = await storage.get_properties_needing_commute()
        if not properties:
            print("No properties need commute backfill.")
            return

        print(f"Found {len(properties)} properties with coordinates but no commute data.")

        commute_filter = CommuteFilter(
            app_id=settings.traveltime_app_id,
            api_key=settings.traveltime_api_key.get_secret_value(),
            destination_postcode=criteria.destination_postcode,
        )

        commute_lookup: dict[str, tuple[int, TransportMode]] = {}
        for mode in criteria.transport_modes:
            results = await commute_filter.filter_properties(
                properties,
                max_minutes=criteria.max_commute_minutes,
                transport_mode=mode,
            )
            for result in results:
                if result.within_limit and (
                    result.property_id not in commute_lookup
                    or result.travel_time_minutes < commute_lookup[result.property_id][0]
                ):
                    commute_lookup[result.property_id] = (
                        result.travel_time_minutes,
                        result.transport_mode,
                    )

        if not commute_lookup:
            print("No properties within commute limit.")
            return

        updated = await storage.update_commute_data(commute_lookup)
        print(f"Updated {updated} properties with commute data.")
        logger.info("backfill_commute_complete", updated=updated, total=len(properties))

    finally:
        await storage.close()


async def run_dedup_existing(settings: Settings) -> None:
    """Retroactively merge duplicate properties already in the database.

    Loads all stored properties, runs the deduplicator, and merges any
    cross-platform duplicates that were ingested before image hashing was enabled.
    """
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    try:
        # Load all properties from DB
        all_properties = await storage.get_recent_properties_for_dedup(days=None)
        if not all_properties:
            print("No properties in database.")
            return

        print(f"Found {len(all_properties)} properties, checking for duplicates...")

        # Build lookup of input IDs
        input_ids = {m.canonical.unique_id for m in all_properties}

        # Run deduplication
        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=settings.enable_image_hash_matching,
            data_dir=settings.data_dir,
        )
        dedup_results = await deduplicator.deduplicate_merged_async(all_properties)

        # Determine which IDs survived and which were absorbed
        output_ids = {m.canonical.unique_id for m in dedup_results}
        absorbed_ids = input_ids - output_ids

        if not absorbed_ids:
            print("No duplicates found.")
            return

        # For each merged result that absorbed other properties, update the DB
        merged_count = 0
        for merged in dedup_results:
            # Find which input properties were absorbed into this result
            merged_source_urls = {str(u) for u in merged.source_urls.values()}
            absorbed_into_this: list[str] = []
            for aid in absorbed_ids:
                # Check if any of the absorbed property's URLs appear in this merged result
                for orig in all_properties:
                    if orig.canonical.unique_id == aid:
                        orig_urls = {str(u) for u in orig.source_urls.values()}
                        if orig_urls & merged_source_urls:
                            absorbed_into_this.append(aid)
                        break

            if not absorbed_into_this:
                continue

            anchor_id = merged.canonical.unique_id
            # Update DB anchor with merged sources
            await storage.update_merged_sources(anchor_id, merged)

            # Copy cached images and delete absorbed properties
            for absorbed_id in absorbed_into_this:
                if settings.data_dir:
                    copied = copy_cached_images(
                        settings.data_dir, absorbed_id, anchor_id
                    )
                    if copied:
                        logger.info(
                            "dedup_images_copied",
                            from_id=absorbed_id,
                            to_id=anchor_id,
                            count=copied,
                        )
                await storage.delete_property(absorbed_id)
                logger.info(
                    "dedup_property_absorbed",
                    absorbed_id=absorbed_id,
                    anchor_id=anchor_id,
                )

            merged_count += 1

        remaining = len(all_properties) - len(absorbed_ids)
        print(
            f"Merged {merged_count} duplicate groups "
            f"({len(absorbed_ids)} properties absorbed), "
            f"{remaining} properties remaining."
        )

    finally:
        await storage.close()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Home Finder - Multi-platform London rental property scraper"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without sending Telegram notifications",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Only scrape and print properties (no filtering, storage, or notifications)",
    )
    parser.add_argument(
        "--max-per-scraper",
        type=int,
        default=None,
        help="Limit properties per scraper (for faster dev/test runs)",
    )
    parser.add_argument(
        "--scrapers",
        type=str,
        default=None,
        help="Comma-separated list of scrapers to run (e.g. openrent,zoopla). "
        "Options: openrent, rightmove, zoopla, onthemarket",
    )
    parser.add_argument(
        "--backfill-commute",
        action="store_true",
        help="Backfill commute data for properties with coordinates but no commute_minutes",
    )
    parser.add_argument(
        "--dedup-existing",
        action="store_true",
        help="Retroactively merge duplicate properties already in the database",
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Re-run quality analysis on flagged properties",
    )
    parser.add_argument(
        "--outcode",
        type=str,
        default=None,
        help="Comma-separated outcodes to flag for re-analysis (e.g. E2,E8). Use with --reanalyze",
    )
    parser.add_argument(
        "--request-only",
        action="store_true",
        help="Only flag properties for re-analysis, don't run analysis. Use with --reanalyze",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="reanalyze_all",
        help="Flag ALL analyzed properties for re-analysis. Use with --reanalyze",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start web server with background pipeline scheduler",
    )
    parser.add_argument(
        "--no-pipeline",
        action="store_true",
        help="With --serve: start web server only, skip background pipeline",
    )
    parser.add_argument(
        "--full-scrape",
        action="store_true",
        help="Disable pagination early-stop — scrape all pages even if properties are already in DB",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging for troubleshooting",
    )
    args = parser.parse_args()

    # Configure logging
    import logging

    configure_logging(json_output=False, level=logging.DEBUG if args.debug else logging.INFO)

    try:
        settings = Settings()
    except Exception as e:
        logger.error("failed_to_load_settings", error=str(e), exc_info=True)
        print(f"Error: Failed to load settings. {e}")
        print("Make sure you have a .env file with required settings.")
        print("Required: HOME_FINDER_TELEGRAM_BOT_TOKEN, HOME_FINDER_TELEGRAM_CHAT_ID")
        print("Optional: HOME_FINDER_TRAVELTIME_APP_ID, HOME_FINDER_TRAVELTIME_API_KEY")
        sys.exit(1)

    logger.info(
        "starting_home_finder",
        min_price=settings.min_price,
        max_price=settings.max_price,
        min_bedrooms=settings.min_bedrooms,
        max_bedrooms=settings.max_bedrooms,
        destination=settings.destination_postcode,
        max_commute=settings.max_commute_minutes,
        dry_run=args.dry_run,
        scrape_only=args.scrape_only,
    )

    # Parse --scrapers into a set of source values
    only_scrapers: set[str] | None = None
    if args.scrapers:
        valid = {s.value for s in PropertySource}
        only_scrapers = {s.strip().lower() for s in args.scrapers.split(",")}
        unknown = only_scrapers - valid
        if unknown:
            print(f"Error: Unknown scraper(s): {', '.join(sorted(unknown))}")
            print(f"Valid options: {', '.join(sorted(valid))}")
            sys.exit(1)

    if args.backfill_commute:
        asyncio.run(run_backfill_commute(settings))
    elif args.dedup_existing:
        asyncio.run(run_dedup_existing(settings))
    elif args.reanalyze:
        outcodes = [o.strip().upper() for o in args.outcode.split(",")] if args.outcode else None
        asyncio.run(
            run_reanalysis(
                settings,
                outcodes=outcodes,
                reanalyze_all=args.reanalyze_all,
                request_only=args.request_only,
            )
        )
    elif args.serve:
        import uvicorn

        from home_finder.web.app import create_app

        app = create_app(settings, run_pipeline=not args.no_pipeline)
        uvicorn.run(app, host=settings.web_host, port=settings.web_port, log_level="info")
    elif args.scrape_only:
        asyncio.run(
            run_scrape_only(
                settings, max_per_scraper=args.max_per_scraper, only_scrapers=only_scrapers
            )
        )
    elif args.dry_run:
        asyncio.run(
            run_dry_run(
                settings,
                max_per_scraper=args.max_per_scraper,
                only_scrapers=only_scrapers,
                full_scrape=args.full_scrape,
            )
        )
    else:
        asyncio.run(
            run_pipeline(
                settings,
                max_per_scraper=args.max_per_scraper,
                only_scrapers=only_scrapers,
                full_scrape=args.full_scrape,
            )
        )


if __name__ == "__main__":
    main()
