"""Main entry point for the home finder scraper."""

import argparse
import asyncio
import sys
from pathlib import Path

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters import CommuteFilter, CriteriaFilter, Deduplicator
from home_finder.logging import configure_logging, get_logger
from home_finder.models import Property, TransportMode
from home_finder.notifiers import TelegramNotifier
from home_finder.scrapers import (
    OnTheMarketScraper,
    OpenRentScraper,
    RightmoveScraper,
    ZooplaScraper,
)

logger = get_logger(__name__)

# Search areas - supports both boroughs and postcodes (outcodes)
SEARCH_AREAS = [
    # Boroughs
    "hackney",
    "islington",
    "haringey",
    "tower-hamlets",
    # Postcodes
    "e3",  # Bow (Tower Hamlets)
    "e5",  # Clapton (Hackney)
    "e9",  # Hackney Wick, Homerton (Hackney)
    "e10",  # Leyton (Waltham Forest)
    "n15",  # South Tottenham (Haringey)
]


async def scrape_all_platforms(
    *,
    min_price: int,
    max_price: int,
    min_bedrooms: int,
    max_bedrooms: int,
) -> list[Property]:
    """Scrape all platforms for matching properties.

    Args:
        min_price: Minimum monthly rent.
        max_price: Maximum monthly rent.
        min_bedrooms: Minimum bedrooms.
        max_bedrooms: Maximum bedrooms.

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
        for area in SEARCH_AREAS:
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
        # Step 1: Scrape all platforms
        logger.info("pipeline_started", phase="scraping")
        all_properties = await scrape_all_platforms(
            min_price=criteria.min_price,
            max_price=criteria.max_price,
            min_bedrooms=criteria.min_bedrooms,
            max_bedrooms=criteria.max_bedrooms,
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

        # Step 3: Deduplicate
        logger.info("pipeline_started", phase="deduplication")
        deduplicator = Deduplicator(enable_cross_platform=True)
        unique = deduplicator.deduplicate(filtered)
        logger.info("deduplication_summary", unique_count=len(unique))

        # Step 4: Filter to new properties only
        logger.info("pipeline_started", phase="new_property_filter")
        new_properties = await storage.filter_new(unique)
        logger.info("new_property_summary", new_count=len(new_properties))

        if not new_properties:
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

            # Filter properties with coordinates
            props_with_coords = [p for p in new_properties if p.latitude and p.longitude]
            props_without_coords = [p for p in new_properties if not (p.latitude and p.longitude)]

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
                if result.within_limit:
                    if result.property_id not in commute_lookup:
                        commute_lookup[result.property_id] = (
                            result.travel_time_minutes,
                            result.transport_mode,
                        )
                    elif result.travel_time_minutes < commute_lookup[result.property_id][0]:
                        commute_lookup[result.property_id] = (
                            result.travel_time_minutes,
                            result.transport_mode,
                        )

            # Keep properties within commute limit or without coords
            properties_to_notify = [p for p in props_with_coords if p.unique_id in commute_lookup]
            # Include properties without coordinates (can't filter them)
            properties_to_notify.extend(props_without_coords)

            logger.info(
                "commute_filter_summary",
                within_limit=len(properties_to_notify),
                total_checked=len(props_with_coords),
            )
        else:
            properties_to_notify = new_properties
            commute_lookup = {}
            logger.info("skipping_commute_filter", reason="no_traveltime_credentials")

        if not properties_to_notify:
            logger.info("no_properties_within_commute_limit")
            return

        # Step 6: Save and notify
        logger.info(
            "pipeline_started",
            phase="save_and_notify",
            count=len(properties_to_notify),
        )

        for prop in properties_to_notify:
            commute_info = commute_lookup.get(prop.unique_id)
            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None

            # Save to database
            await storage.save_property(
                prop,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
            )

            # Send notification
            success = await notifier.send_property_notification(
                prop,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
            )

            if success:
                await storage.mark_notified(prop.unique_id)
            else:
                await storage.mark_notification_failed(prop.unique_id)

            # Small delay between notifications
            await asyncio.sleep(1)

        logger.info("pipeline_complete", notified=len(properties_to_notify))

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

        # Step 3: Deduplicate
        logger.info("pipeline_started", phase="deduplication")
        deduplicator = Deduplicator(enable_cross_platform=True)
        unique = deduplicator.deduplicate(filtered)
        logger.info("deduplication_summary", unique_count=len(unique))

        # Step 4: Filter to new properties only
        logger.info("pipeline_started", phase="new_property_filter")
        new_properties = await storage.filter_new(unique)
        logger.info("new_property_summary", new_count=len(new_properties))

        if not new_properties:
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

            props_with_coords = [p for p in new_properties if p.latitude and p.longitude]
            props_without_coords = [p for p in new_properties if not (p.latitude and p.longitude)]

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
                if result.within_limit:
                    if result.property_id not in commute_lookup:
                        commute_lookup[result.property_id] = (
                            result.travel_time_minutes,
                            result.transport_mode,
                        )
                    elif result.travel_time_minutes < commute_lookup[result.property_id][0]:
                        commute_lookup[result.property_id] = (
                            result.travel_time_minutes,
                            result.transport_mode,
                        )

            properties_to_notify = [p for p in props_with_coords if p.unique_id in commute_lookup]
            properties_to_notify.extend(props_without_coords)

            logger.info(
                "commute_filter_summary",
                within_limit=len(properties_to_notify),
                total_checked=len(props_with_coords),
            )
        else:
            properties_to_notify = new_properties
            logger.info("skipping_commute_filter", reason="no_traveltime_credentials")

        if not properties_to_notify:
            logger.info("no_properties_within_commute_limit")
            print("\nNo properties within commute limit.")
            return

        # Step 6: Save (but don't notify in dry-run mode)
        logger.info(
            "pipeline_started",
            phase="save_only",
            count=len(properties_to_notify),
            dry_run=True,
        )

        print(f"\n{'=' * 60}")
        print(f"[DRY RUN] Would notify about {len(properties_to_notify)} properties:")
        print(f"{'=' * 60}\n")

        for prop in properties_to_notify:
            commute_info = commute_lookup.get(prop.unique_id)
            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None

            # Save to database
            await storage.save_property(
                prop,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
            )

            # Print instead of notify
            print(f"[{prop.source.value}] {prop.title}")
            print(f"  Price: £{prop.price_pcm}/month | Beds: {prop.bedrooms}")
            print(f"  Address: {prop.address}")
            if prop.postcode:
                print(f"  Postcode: {prop.postcode}")
            if commute_minutes is not None:
                mode_str = transport_mode.value if transport_mode else ""
                print(f"  Commute: {commute_minutes} min ({mode_str})")
            print(f"  URL: {prop.url}")
            print()

        logger.info("dry_run_complete", saved=len(properties_to_notify))

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
