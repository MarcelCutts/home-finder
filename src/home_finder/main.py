"""Main entry point for the home finder scraper."""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

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
from home_finder.logging import configure_logging, get_logger
from home_finder.models import (
    FurnishType,
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
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

logger = get_logger(__name__)


def _source_counts(properties: list[Property] | list[MergedProperty]) -> dict[str, int]:
    """Count properties by source for diagnostic logging."""
    counts: dict[str, int] = {}
    for p in properties:
        src = (
            p.source.value
            if isinstance(p, Property)
            else p.canonical.source.value
        )
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

    Returns:
        Combined list of properties from all platforms.
    """
    areas = search_areas or []
    if not areas:
        logger.warning("no_search_areas_configured")
        return []
    scrapers = [
        OpenRentScraper(),
        RightmoveScraper(),
        ZooplaScraper(),
        OnTheMarketScraper(proxy_url=proxy_url),
    ]

    all_properties: list[Property] = []

    try:
        for scraper in scrapers:
            scraper_known = (
                known_ids_by_source.get(scraper.source.value) if known_ids_by_source else None
            )
            scraper_count = 0
            scraper_seen_ids: set[str] = set()
            for i, area in enumerate(areas):
                if max_per_scraper is not None and scraper_count >= max_per_scraper:
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
                    )
                # Delay between areas to avoid rate limiting
                if i < len(areas) - 1:
                    await asyncio.sleep(2)
    finally:
        for scraper in scrapers:
            await scraper.close()

    return all_properties


@dataclass
class PreAnalysisResult:
    """Result of the pre-analysis pipeline (scrape -> filter -> enrich)."""

    merged_to_process: list[MergedProperty] = field(default_factory=list)
    commute_lookup: dict[str, tuple[int, TransportMode]] = field(default_factory=dict)


async def _run_pre_analysis_pipeline(
    settings: Settings,
    storage: PropertyStorage,
    *,
    max_per_scraper: int | None = None,
) -> PreAnalysisResult | None:
    """Run the pre-analysis pipeline: scrape, filter, deduplicate, enrich.

    Returns None if pipeline ends early (no properties at any stage).
    Quality analysis is handled separately by the caller for concurrency.
    """
    criteria = settings.get_search_criteria()
    search_areas = settings.get_search_areas()

    # Load known source IDs for early-stop pagination
    known_ids_by_source = await storage.get_all_known_source_ids()
    logger.info(
        "loaded_known_ids",
        total=sum(len(v) for v in known_ids_by_source.values()),
    )

    # Step 1: Scrape all platforms
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
    )
    logger.info("scraping_summary", total_found=len(all_properties), by_source=_source_counts(all_properties))

    if not all_properties:
        logger.info("no_properties_found")
        return None

    # Step 2: Apply criteria filter
    logger.info("pipeline_started", phase="criteria_filtering")
    criteria_filter = CriteriaFilter(criteria)
    filtered = criteria_filter.filter_properties(all_properties)
    logger.info("criteria_filter_summary", matched=len(filtered), by_source=_source_counts(filtered))

    if not filtered:
        logger.info("no_properties_match_criteria")
        return None

    # Step 2.5: Apply location filter (catch scraper leakage)
    logger.info("pipeline_started", phase="location_filtering")
    location_filter = LocationFilter(search_areas, strict=False)
    filtered = location_filter.filter_properties(filtered)
    logger.info("location_filter_summary", matched=len(filtered), by_source=_source_counts(filtered))

    if not filtered:
        logger.info("no_properties_in_search_areas")
        return None

    # Step 3: Wrap as single-source MergedProperties for downstream pipeline
    logger.info("pipeline_started", phase="wrap_merged")
    deduplicator = Deduplicator(
        enable_cross_platform=True,
        enable_image_hashing=settings.enable_image_hash_matching,
    )
    merged_properties = deduplicator.properties_to_merged(filtered)
    logger.info("wrap_merged_summary", count=len(merged_properties), by_source=_source_counts(merged_properties))

    # Step 4: Filter to new properties only
    logger.info("pipeline_started", phase="new_property_filter")
    new_merged = await storage.filter_new_merged(merged_properties)
    logger.info("new_property_summary", new_count=len(new_merged), by_source=_source_counts(new_merged))

    if not new_merged:
        logger.info("no_new_properties")
        return None

    # Step 5: Filter by commute time (if TravelTime configured)
    commute_lookup: dict[str, tuple[int, TransportMode]] = {}
    if settings.traveltime_app_id and settings.traveltime_api_key:
        logger.info("pipeline_started", phase="commute_filtering")
        commute_filter = CommuteFilter(
            app_id=settings.traveltime_app_id,
            api_key=settings.traveltime_api_key.get_secret_value(),
            destination_postcode=criteria.destination_postcode,
        )

        # Geocode properties that have postcode but no coordinates
        new_merged = await commute_filter.geocode_properties(new_merged)

        merged_with_coords = [
            m for m in new_merged if m.canonical.latitude and m.canonical.longitude
        ]
        merged_without_coords = [
            m for m in new_merged if not (m.canonical.latitude and m.canonical.longitude)
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

        merged_to_notify = [
            m for m in merged_with_coords if m.canonical.unique_id in commute_lookup
        ]
        merged_to_notify.extend(merged_without_coords)

        logger.info(
            "commute_filter_summary",
            within_limit=len(merged_to_notify),
            total_checked=len(merged_with_coords),
            without_coords=len(merged_without_coords),
            by_source=_source_counts(merged_to_notify),
        )
    else:
        merged_to_notify = new_merged
        logger.info("skipping_commute_filter", reason="no_traveltime_credentials")

    if not merged_to_notify:
        logger.info("no_properties_within_commute_limit")
        return None

    # Step 5.5: Enrich with detail page data (gallery, floorplan, descriptions)
    logger.info("pipeline_started", phase="detail_enrichment")
    detail_fetcher = DetailFetcher(
        max_gallery_images=settings.quality_filter_max_images,
        proxy_url=settings.proxy_url,
    )
    try:
        merged_to_notify = await enrich_merged_properties(
            merged_to_notify,
            detail_fetcher,
            data_dir=settings.data_dir,
            storage=storage,
        )
    finally:
        await detail_fetcher.close()

    logger.info(
        "enrichment_summary",
        total=len(merged_to_notify),
        with_floorplan=sum(1 for m in merged_to_notify if m.floorplan),
        with_images=sum(1 for m in merged_to_notify if m.images),
        by_source=_source_counts(merged_to_notify),
    )

    # Step 5.6: Cross-platform dedup (including DB anchors for cross-run detection)
    logger.info("pipeline_started", phase="deduplication_merge")

    # Load recent DB properties as dedup anchors so that a property appearing
    # on platform B today can be matched against platform A stored last week.
    db_anchors = await storage.get_recent_properties_for_dedup(days=30)
    logger.info(
        "loaded_dedup_anchors",
        anchor_count=len(db_anchors),
    )

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

    logger.info(
        "deduplication_merge_summary",
        dedup_input=len(combined_for_dedup),
        dedup_output=len(dedup_results),
        genuinely_new=len(genuinely_new),
        anchors_updated=anchors_updated,
        multi_source_count=sum(1 for m in genuinely_new if len(m.sources) > 1),
        by_source=_source_counts(genuinely_new),
    )

    merged_to_notify = genuinely_new

    if not merged_to_notify:
        logger.info("no_new_properties_after_cross_run_dedup")
        return None

    # Step 5.7: Floorplan gate (if configured)
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

    return PreAnalysisResult(
        merged_to_process=merged_to_notify,
        commute_lookup=commute_lookup,
    )


async def _save_one(
    merged: MergedProperty,
    commute_info: tuple[int, TransportMode] | None,
    quality_analysis: PropertyQualityAnalysis | None,
    storage: PropertyStorage,
) -> None:
    """Save a single property with its commute and quality data to the database."""
    commute_minutes = commute_info[0] if commute_info else None
    transport_mode = commute_info[1] if commute_info else None

    await storage.save_merged_property(
        merged,
        commute_minutes=commute_minutes,
        transport_mode=transport_mode,
    )

    if merged.images:
        await storage.save_property_images(merged.unique_id, list(merged.images))
    if merged.floorplan:
        await storage.save_property_images(merged.unique_id, [merged.floorplan])

    if quality_analysis:
        await storage.save_quality_analysis(merged.unique_id, quality_analysis)


# Type alias for the per-property callback in quality analysis
_CommInfo = tuple[int, TransportMode] | None
_OnResult = Callable[
    [MergedProperty, _CommInfo, PropertyQualityAnalysis | None],
    Awaitable[None],
]


async def _run_quality_and_save(
    pre: PreAnalysisResult,
    settings: Settings,
    storage: PropertyStorage,
    on_result: _OnResult,
) -> int:
    """Run quality analysis, save each property, and invoke callback.

    Args:
        pre: Pre-analysis pipeline result.
        settings: Application settings.
        storage: Database storage.
        on_result: Async callback invoked after each property is saved.

    Returns:
        Number of properties processed.
    """
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

    semaphore = asyncio.Semaphore(_QUALITY_CONCURRENCY)
    count = 0

    try:

        async def _analyze_one(
            merged: MergedProperty,
        ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
            async with semaphore:
                if quality_filter:
                    return await quality_filter.analyze_single_merged(
                        merged, data_dir=settings.data_dir
                    )
                return merged, None

        tasks = [asyncio.create_task(_analyze_one(m)) for m in pre.merged_to_process]

        for coro in asyncio.as_completed(tasks):
            try:
                merged, quality_analysis = await coro
            except Exception:
                logger.error("property_processing_failed", exc_info=True)
                continue

            commute_info = pre.commute_lookup.get(merged.canonical.unique_id)
            await _save_one(merged, commute_info, quality_analysis, storage)
            await on_result(merged, commute_info, quality_analysis)
            count += 1
    finally:
        if quality_filter:
            await quality_filter.close()

    return count


async def run_pipeline(settings: Settings, *, max_per_scraper: int | None = None) -> None:
    """Run the full scraping and notification pipeline.

    Quality analysis runs concurrently (bounded by semaphore), and each
    property is saved + notified as soon as its analysis completes.

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
    """
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

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

        pre = await _run_pre_analysis_pipeline(settings, storage, max_per_scraper=max_per_scraper)
        if pre is None:
            return

        logger.info(
            "pipeline_started",
            phase="quality_analysis_and_notify",
            count=len(pre.merged_to_process),
        )

        async def _notify(
            merged: MergedProperty,
            commute_info: _CommInfo,
            quality_analysis: PropertyQualityAnalysis | None,
        ) -> None:
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
            else:
                await storage.mark_notification_failed(merged.unique_id)

            await asyncio.sleep(1)  # Telegram rate limit

        notified_count = await _run_quality_and_save(pre, settings, storage, _notify)
        logger.info("pipeline_complete", notified=notified_count)

    finally:
        await notifier.close()
        await storage.close()


async def run_scrape_only(settings: Settings, *, max_per_scraper: int | None = None) -> None:
    """Run scraping only and print results (no filtering, storage, or notifications).

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
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


async def run_dry_run(settings: Settings, *, max_per_scraper: int | None = None) -> None:
    """Run the full pipeline without sending Telegram notifications.

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
    """
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    try:
        pre = await _run_pre_analysis_pipeline(settings, storage, max_per_scraper=max_per_scraper)
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
        logger.error("failed_to_load_settings", error=str(e))
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

    if args.serve:
        import uvicorn

        from home_finder.web.app import create_app

        app = create_app(settings, run_pipeline=not args.no_pipeline)
        uvicorn.run(app, host=settings.web_host, port=settings.web_port, log_level="info")
    elif args.scrape_only:
        asyncio.run(run_scrape_only(settings, max_per_scraper=args.max_per_scraper))
    elif args.dry_run:
        asyncio.run(run_dry_run(settings, max_per_scraper=args.max_per_scraper))
    else:
        asyncio.run(run_pipeline(settings, max_per_scraper=args.max_per_scraper))


if __name__ == "__main__":
    main()
