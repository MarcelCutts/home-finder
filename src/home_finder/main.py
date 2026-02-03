"""Main entry point for the home finder scraper."""

import argparse
import asyncio
import sys

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters import (
    CommuteFilter,
    CriteriaFilter,
    Deduplicator,
    LocationFilter,
    PropertyQualityAnalysis,
    PropertyQualityFilter,
    enrich_merged_properties,
    filter_by_floorplan,
)
from home_finder.logging import configure_logging, get_logger
from home_finder.models import FurnishType, MergedProperty, Property, TransportMode
from home_finder.notifiers import TelegramNotifier
from home_finder.scrapers import (
    OnTheMarketScraper,
    OpenRentScraper,
    RightmoveScraper,
    ZooplaScraper,
)
from home_finder.scrapers.detail_fetcher import DetailFetcher

logger = get_logger(__name__)

# Search areas - supports both boroughs and postcodes (outcodes)
SEARCH_AREAS = [
    # Boroughs
    # "hackney",
    # "islington",
    # "haringey",
    # "tower-hamlets",
    # # Postcodes
    "e3",  # Bow (Tower Hamlets)
    "e5",  # Clapton (Hackney)
    "e9",  # Hackney Wick, Homerton (Hackney)
    "e10",  # Leyton (Waltham Forest)
    "e15",  # Stratford (Newham)
    "e17",  # Walthamstow (Waltham Forest)
    "n15",  # South Tottenham (Haringey)
    "n16",  # Stoke Newington (Hackney)
    "n17",  # Tottenham (Haringey)
]


async def scrape_all_platforms(
    *,
    min_price: int,
    max_price: int,
    min_bedrooms: int,
    max_bedrooms: int,
    furnish_types: tuple[FurnishType, ...] = (),
    min_bathrooms: int = 0,
    include_let_agreed: bool = True,
) -> list[Property]:
    """Scrape all platforms for matching properties.

    Args:
        min_price: Minimum monthly rent.
        max_price: Maximum monthly rent.
        min_bedrooms: Minimum bedrooms.
        max_bedrooms: Maximum bedrooms.
        furnish_types: Furnishing types to include.
        min_bathrooms: Minimum number of bathrooms.
        include_let_agreed: Whether to include already-let properties.

    Returns:
        Combined list of properties from all platforms.
    """
    scrapers = [
        OpenRentScraper(),
        RightmoveScraper(),
        ZooplaScraper(),
        OnTheMarketScraper(),
    ]

    all_properties: list[Property] = []

    for scraper in scrapers:
        for i, area in enumerate(SEARCH_AREAS):
            try:
                logger.info(
                    "scraping_platform",
                    platform=scraper.source.value,
                    area=area,
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
                )
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
            if i < len(SEARCH_AREAS) - 1:
                await asyncio.sleep(2)

    return all_properties


async def run_pipeline(settings: Settings) -> None:
    """Run the full scraping and notification pipeline.

    Args:
        settings: Application settings.
    """
    criteria = settings.get_search_criteria()

    # Initialize storage
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    # Initialize notifier
    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_chat_id,
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

        # Step 1: Scrape all platforms
        logger.info("pipeline_started", phase="scraping")
        all_properties = await scrape_all_platforms(
            min_price=criteria.min_price,
            max_price=criteria.max_price,
            min_bedrooms=criteria.min_bedrooms,
            max_bedrooms=criteria.max_bedrooms,
            furnish_types=settings.get_furnish_types(),
            min_bathrooms=settings.min_bathrooms,
            include_let_agreed=settings.include_let_agreed,
        )
        logger.info("scraping_summary", total_found=len(all_properties))

        if not all_properties:
            logger.info("no_properties_found")
            return

        # Step 2: Apply criteria filter
        logger.info("pipeline_started", phase="criteria_filtering")
        criteria_filter = CriteriaFilter(criteria)
        filtered = criteria_filter.filter_properties(all_properties)
        logger.info("criteria_filter_summary", matched=len(filtered))

        if not filtered:
            logger.info("no_properties_match_criteria")
            return

        # Step 2.5: Apply location filter (catch scraper leakage)
        logger.info("pipeline_started", phase="location_filtering")
        location_filter = LocationFilter(SEARCH_AREAS, strict=False)
        filtered = location_filter.filter_properties(filtered)
        logger.info("location_filter_summary", matched=len(filtered))

        if not filtered:
            logger.info("no_properties_in_search_areas")
            return

        # Step 3: Deduplicate and merge cross-platform listings
        logger.info("pipeline_started", phase="deduplication_merge")
        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=settings.enable_image_hash_matching,
        )
        merged_properties = await deduplicator.deduplicate_and_merge_async(filtered)
        logger.info(
            "deduplication_merge_summary",
            merged_count=len(merged_properties),
            multi_source_count=sum(1 for m in merged_properties if len(m.sources) > 1),
        )

        # Step 4: Filter to new properties only
        logger.info("pipeline_started", phase="new_property_filter")
        new_merged = await storage.filter_new_merged(merged_properties)
        logger.info("new_property_summary", new_count=len(new_merged))

        if not new_merged:
            logger.info("no_new_properties")
            return

        # Step 5: Filter by commute time (if TravelTime configured)
        commute_filter = None
        if settings.traveltime_app_id and settings.traveltime_api_key:
            logger.info("pipeline_started", phase="commute_filtering")
            commute_filter = CommuteFilter(
                app_id=settings.traveltime_app_id,
                api_key=settings.traveltime_api_key.get_secret_value(),
                destination_postcode=criteria.destination_postcode,
            )

            # Filter merged properties with coordinates (use canonical property)
            merged_with_coords = [
                m for m in new_merged if m.canonical.latitude and m.canonical.longitude
            ]
            merged_without_coords = [
                m for m in new_merged if not (m.canonical.latitude and m.canonical.longitude)
            ]

            # Extract canonical properties for commute filtering
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

            # Build lookup of best commute time per property
            commute_lookup: dict[str, tuple[int, TransportMode]] = {}
            for result in commute_results:
                if result.within_limit and (
                    result.property_id not in commute_lookup
                    or result.travel_time_minutes < commute_lookup[result.property_id][0]
                ):
                    commute_lookup[result.property_id] = (
                        result.travel_time_minutes,
                        result.transport_mode,
                    )

            # Keep merged properties within commute limit or without coords
            merged_to_notify = [
                m for m in merged_with_coords if m.canonical.unique_id in commute_lookup
            ]
            # Include merged properties without coordinates (can't filter them)
            merged_to_notify.extend(merged_without_coords)

            logger.info(
                "commute_filter_summary",
                within_limit=len(merged_to_notify),
                total_checked=len(merged_with_coords),
            )
        else:
            merged_to_notify = new_merged
            commute_lookup = {}
            logger.info("skipping_commute_filter", reason="no_traveltime_credentials")

        if not merged_to_notify:
            logger.info("no_properties_within_commute_limit")
            return

        # Step 5.5: Enrich with detail page data (gallery, floorplan, descriptions)
        logger.info("pipeline_started", phase="detail_enrichment")
        detail_fetcher = DetailFetcher(max_gallery_images=settings.quality_filter_max_images)
        try:
            merged_to_notify = await enrich_merged_properties(merged_to_notify, detail_fetcher)
        finally:
            await detail_fetcher.close()

        logger.info(
            "enrichment_summary",
            total=len(merged_to_notify),
            with_floorplan=sum(1 for m in merged_to_notify if m.floorplan),
            with_images=sum(1 for m in merged_to_notify if m.images),
        )

        # Step 5.6: Floorplan gate (if configured)
        if settings.require_floorplan:
            before_count = len(merged_to_notify)
            merged_to_notify = filter_by_floorplan(merged_to_notify)
            logger.info(
                "floorplan_filter",
                before=before_count,
                after=len(merged_to_notify),
                dropped=before_count - len(merged_to_notify),
            )

            if not merged_to_notify:
                logger.info("no_properties_with_floorplans")
                return

        # Step 6: Property quality analysis (if configured)
        quality_lookup: dict[str, PropertyQualityAnalysis] = {}
        analyzed_merged: dict[str, MergedProperty] = {}
        quality_filter = None
        if settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter:
            logger.info("pipeline_started", phase="quality_analysis")
            quality_filter = PropertyQualityFilter(
                api_key=settings.anthropic_api_key.get_secret_value(),
                max_images=settings.quality_filter_max_images,
            )

            try:
                quality_results = await quality_filter.analyze_merged_properties(merged_to_notify)

                for merged, analysis in quality_results:
                    quality_lookup[merged.unique_id] = analysis
                    analyzed_merged[merged.unique_id] = merged

                concerns = sum(1 for _, a in quality_results if a.condition_concerns)
                logger.info(
                    "quality_analysis_summary",
                    analyzed=len(quality_results),
                    condition_concerns=concerns,
                )
            finally:
                await quality_filter.close()
        else:
            logger.info("skipping_quality_analysis", reason="not_configured")

        # Step 7: Save and notify
        logger.info(
            "pipeline_started",
            phase="save_and_notify",
            count=len(merged_to_notify),
        )

        for merged in merged_to_notify:
            # Use updated merged property with images if available
            final_merged = analyzed_merged.get(merged.unique_id, merged)

            commute_info = commute_lookup.get(merged.canonical.unique_id)
            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None
            quality_analysis = quality_lookup.get(merged.unique_id)

            # Save merged property to database
            await storage.save_merged_property(
                final_merged,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
            )

            # Save images if any
            if final_merged.images:
                await storage.save_property_images(
                    final_merged.unique_id, list(final_merged.images)
                )
            if final_merged.floorplan:
                await storage.save_property_images(final_merged.unique_id, [final_merged.floorplan])

            # Send notification
            success = await notifier.send_merged_property_notification(
                final_merged,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
                quality_analysis=quality_analysis,
            )

            if success:
                await storage.mark_notified(merged.unique_id)
            else:
                await storage.mark_notification_failed(merged.unique_id)

            # Small delay between notifications
            await asyncio.sleep(1)

        logger.info("pipeline_complete", notified=len(merged_to_notify))

    finally:
        await notifier.close()
        await storage.close()


async def run_scrape_only(settings: Settings) -> None:
    """Run scraping only and print results (no filtering, storage, or notifications).

    Args:
        settings: Application settings.
    """
    criteria = settings.get_search_criteria()

    logger.info("scrape_only_started")
    all_properties = await scrape_all_platforms(
        min_price=criteria.min_price,
        max_price=criteria.max_price,
        min_bedrooms=criteria.min_bedrooms,
        max_bedrooms=criteria.max_bedrooms,
        furnish_types=settings.get_furnish_types(),
        min_bathrooms=settings.min_bathrooms,
        include_let_agreed=settings.include_let_agreed,
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


async def run_dry_run(settings: Settings) -> None:
    """Run the full pipeline without sending Telegram notifications.

    Args:
        settings: Application settings.
    """
    criteria = settings.get_search_criteria()

    # Initialize storage
    storage = PropertyStorage(settings.database_path)
    await storage.initialize()

    try:
        # Step 1: Scrape all platforms
        logger.info("pipeline_started", phase="scraping", dry_run=True)
        all_properties = await scrape_all_platforms(
            min_price=criteria.min_price,
            max_price=criteria.max_price,
            min_bedrooms=criteria.min_bedrooms,
            max_bedrooms=criteria.max_bedrooms,
            furnish_types=settings.get_furnish_types(),
            min_bathrooms=settings.min_bathrooms,
            include_let_agreed=settings.include_let_agreed,
        )
        logger.info("scraping_summary", total_found=len(all_properties))

        if not all_properties:
            logger.info("no_properties_found")
            print("\nNo properties found.")
            return

        # Step 2: Apply criteria filter
        logger.info("pipeline_started", phase="criteria_filtering")
        criteria_filter = CriteriaFilter(criteria)
        filtered = criteria_filter.filter_properties(all_properties)
        logger.info("criteria_filter_summary", matched=len(filtered))

        if not filtered:
            logger.info("no_properties_match_criteria")
            print("\nNo properties match criteria.")
            return

        # Step 2.5: Apply location filter (catch scraper leakage)
        logger.info("pipeline_started", phase="location_filtering")
        location_filter = LocationFilter(SEARCH_AREAS, strict=False)
        filtered = location_filter.filter_properties(filtered)
        logger.info("location_filter_summary", matched=len(filtered))

        if not filtered:
            logger.info("no_properties_in_search_areas")
            print("\nNo properties in search areas.")
            return

        # Step 3: Deduplicate and merge cross-platform listings
        logger.info("pipeline_started", phase="deduplication_merge")
        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=settings.enable_image_hash_matching,
        )
        merged_properties = await deduplicator.deduplicate_and_merge_async(filtered)
        logger.info(
            "deduplication_merge_summary",
            merged_count=len(merged_properties),
            multi_source_count=sum(1 for m in merged_properties if len(m.sources) > 1),
        )

        # Step 4: Filter to new properties only
        logger.info("pipeline_started", phase="new_property_filter")
        new_merged = await storage.filter_new_merged(merged_properties)
        logger.info("new_property_summary", new_count=len(new_merged))

        if not new_merged:
            logger.info("no_new_properties")
            print("\nNo new properties found.")
            return

        # Step 5: Filter by commute time (if TravelTime configured)
        commute_lookup: dict[str, tuple[int, TransportMode]] = {}
        if settings.traveltime_app_id and settings.traveltime_api_key:
            logger.info("pipeline_started", phase="commute_filtering")
            commute_filter = CommuteFilter(
                app_id=settings.traveltime_app_id,
                api_key=settings.traveltime_api_key.get_secret_value(),
                destination_postcode=criteria.destination_postcode,
            )

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
            )
        else:
            merged_to_notify = new_merged
            logger.info("skipping_commute_filter", reason="no_traveltime_credentials")

        if not merged_to_notify:
            logger.info("no_properties_within_commute_limit")
            print("\nNo properties within commute limit.")
            return

        # Step 5.5: Enrich with detail page data (gallery, floorplan, descriptions)
        logger.info("pipeline_started", phase="detail_enrichment")
        detail_fetcher = DetailFetcher(max_gallery_images=settings.quality_filter_max_images)
        try:
            merged_to_notify = await enrich_merged_properties(merged_to_notify, detail_fetcher)
        finally:
            await detail_fetcher.close()

        logger.info(
            "enrichment_summary",
            total=len(merged_to_notify),
            with_floorplan=sum(1 for m in merged_to_notify if m.floorplan),
            with_images=sum(1 for m in merged_to_notify if m.images),
        )

        # Step 5.6: Floorplan gate (if configured)
        if settings.require_floorplan:
            before_count = len(merged_to_notify)
            merged_to_notify = filter_by_floorplan(merged_to_notify)
            logger.info(
                "floorplan_filter",
                before=before_count,
                after=len(merged_to_notify),
                dropped=before_count - len(merged_to_notify),
            )

            if not merged_to_notify:
                logger.info("no_properties_with_floorplans")
                print("\nNo properties with floorplans.")
                return

        # Step 5.7: Property quality analysis (if configured)
        quality_lookup: dict[str, PropertyQualityAnalysis] = {}
        analyzed_merged: dict[str, MergedProperty] = {}
        quality_filter = None
        if settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter:
            logger.info("pipeline_started", phase="quality_analysis")
            quality_filter = PropertyQualityFilter(
                api_key=settings.anthropic_api_key.get_secret_value(),
                max_images=settings.quality_filter_max_images,
            )

            try:
                quality_results = await quality_filter.analyze_merged_properties(merged_to_notify)

                for merged, analysis in quality_results:
                    quality_lookup[merged.unique_id] = analysis
                    analyzed_merged[merged.unique_id] = merged

                concerns = sum(1 for _, a in quality_results if a.condition_concerns)
                logger.info(
                    "quality_analysis_summary",
                    analyzed=len(quality_results),
                    condition_concerns=concerns,
                )
            finally:
                await quality_filter.close()
        else:
            logger.info("skipping_quality_analysis", reason="not_configured")

        # Step 6: Save (but don't notify in dry-run mode)
        logger.info(
            "pipeline_started",
            phase="save_only",
            count=len(merged_to_notify),
            dry_run=True,
        )

        print(f"\n{'=' * 60}")
        print(f"[DRY RUN] Would notify about {len(merged_to_notify)} properties:")
        print(f"{'=' * 60}\n")

        for merged in merged_to_notify:
            final_merged = analyzed_merged.get(merged.unique_id, merged)
            prop = final_merged.canonical

            commute_info = commute_lookup.get(prop.unique_id)
            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None
            quality_analysis = quality_lookup.get(merged.unique_id)

            # Save merged property to database
            await storage.save_merged_property(
                final_merged,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
            )

            # Save images if any
            if final_merged.images:
                await storage.save_property_images(
                    final_merged.unique_id, list(final_merged.images)
                )
            if final_merged.floorplan:
                await storage.save_property_images(final_merged.unique_id, [final_merged.floorplan])

            # Print instead of notify
            source_str = ", ".join(s.value for s in final_merged.sources)
            print(f"[{source_str}] {prop.title}")

            if final_merged.price_varies:
                print(
                    f"  Price: £{final_merged.min_price}-£{final_merged.max_price}/month | "
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
            if len(final_merged.sources) > 1:
                print(f"  Listed on: {len(final_merged.sources)} platforms")
            if final_merged.images or final_merged.floorplan:
                img_str = f"{len(final_merged.images)} images"
                if final_merged.floorplan:
                    img_str += " + floorplan"
                print(f"  Photos: {img_str}")
            if quality_analysis:
                print(f"  Summary: {quality_analysis.summary}")
                if quality_analysis.condition_concerns:
                    print(
                        f"  ⚠️ Condition concerns ({quality_analysis.concern_severity}): "
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

        logger.info("dry_run_complete", saved=len(merged_to_notify))

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
    args = parser.parse_args()

    # Configure logging
    configure_logging(json_output=False)

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

    if args.scrape_only:
        asyncio.run(run_scrape_only(settings))
    elif args.dry_run:
        asyncio.run(run_dry_run(settings))
    else:
        asyncio.run(run_pipeline(settings))


if __name__ == "__main__":
    main()
