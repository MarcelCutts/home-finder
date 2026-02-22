"""Pipeline maintenance commands — backfill, off-market check, retroactive dedup."""

import contextlib
import json

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters import CommuteFilter, Deduplicator
from home_finder.logging import get_logger
from home_finder.models import TransportMode
from home_finder.utils.image_cache import clear_image_cache, copy_cached_images

logger = get_logger(__name__)


async def run_backfill_commute(settings: Settings) -> None:
    """Backfill commute data for properties that have coordinates but no commute_minutes.

    Queries the DB for such properties, runs the TravelTime API, and updates the DB.
    """
    if not settings.traveltime_app_id or not settings.traveltime_api_key:
        logger.error(
            "traveltime_not_configured",
            hint="Set HOME_FINDER_TRAVELTIME_APP_ID and HOME_FINDER_TRAVELTIME_API_KEY",
        )
        return

    async with PropertyStorage(settings.database_path) as storage:
        criteria = settings.get_search_criteria()

        properties = await storage.get_properties_needing_commute()
        if not properties:
            logger.info("no_properties_need_commute_backfill")
            return

        logger.info("commute_backfill_started", count=len(properties))

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
            logger.info("no_properties_within_commute_limit")
            return

        updated = await storage.update_commute_data(commute_lookup)
        logger.info("backfill_commute_complete", updated=updated, total=len(properties))


async def run_check_off_market(
    settings: Settings,
    *,
    only_scrapers: set[str] | None = None,
) -> None:
    """Check active properties for off-market removal signals.

    Visits each property's listing URL(s) and checks for definitive removal
    signals. Only flags as off-market when ALL sources return REMOVED.

    Args:
        settings: Application settings.
        only_scrapers: If set, only check URLs for these platforms.
    """
    from home_finder.filters.off_market import ListingStatus, OffMarketChecker

    async with PropertyStorage(settings.database_path) as storage:
        db_props = await storage.get_properties_for_off_market_check(
            sources=only_scrapers,
        )
        if not db_props:
            logger.info("no_properties_for_off_market_check")
            return

        logger.info("off_market_check_started", count=len(db_props))

        # Expand multi-source properties into individual URL checks
        checks: list[tuple[str, str, str]] = []  # (property_id, source, url)
        for prop in db_props:
            source_urls: dict[str, str] = {}
            if prop.get("source_urls"):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    source_urls = json.loads(prop["source_urls"])

            if source_urls:
                for source, url in source_urls.items():
                    if only_scrapers and source not in only_scrapers:
                        continue
                    checks.append((prop["unique_id"], source, url))
            else:
                # Single-source property
                source = prop["source"]
                if only_scrapers and source not in only_scrapers:
                    continue
                checks.append((prop["unique_id"], source, prop["url"]))

        if not checks:
            logger.info("no_urls_to_check", reason="source filter may have excluded all")
            return

        logger.info("off_market_checking_urls", urls=len(checks), properties=len(db_props))

        async with OffMarketChecker(proxy_url=settings.proxy_url) as checker:
            results = await checker.check_batch(checks)

        # Aggregate per-property: only flag off-market if ALL sources are REMOVED
        # (no source returned ACTIVE)
        per_property: dict[str, dict[str, ListingStatus]] = {}
        for r in results:
            per_property.setdefault(r.property_id, {})[r.source] = r.status

        marked_off = 0
        marked_returned = 0
        for prop in db_props:
            uid = prop["unique_id"]
            source_statuses = per_property.get(uid, {})
            if not source_statuses:
                continue

            has_active = any(s == ListingStatus.ACTIVE for s in source_statuses.values())
            all_removed = all(s == ListingStatus.REMOVED for s in source_statuses.values())
            was_off_market = bool(prop.get("is_off_market"))

            if all_removed and not has_active:
                if not was_off_market:
                    await storage.mark_off_market(uid)
                    marked_off += 1
                    logger.info(
                        "property_confirmed_off_market",
                        unique_id=uid,
                        sources=list(source_statuses.keys()),
                    )
            elif has_active and was_off_market:
                await storage.mark_returned_to_market(uid)
                marked_returned += 1
                logger.info(
                    "property_returned_to_market",
                    unique_id=uid,
                    active_sources=[
                        s for s, st in source_statuses.items() if st == ListingStatus.ACTIVE
                    ],
                )

        # Summary
        status_counts = {s.value: 0 for s in ListingStatus}
        for r in results:
            status_counts[r.status.value] += 1

        logger.info(
            "off_market_check_complete",
            status_counts=status_counts,
            marked_off=marked_off,
            marked_returned=marked_returned,
        )


async def run_dedup_existing(settings: Settings) -> None:
    """Retroactively merge duplicate properties already in the database.

    Loads all stored properties, runs the deduplicator, and merges any
    cross-platform duplicates that were ingested before image hashing was enabled.
    """
    async with PropertyStorage(settings.database_path) as storage:
        # Load all properties from DB
        all_properties = await storage.get_recent_properties_for_dedup(days=None)
        if not all_properties:
            logger.info("dedup_no_properties_in_database")
            return

        logger.info("dedup_started", count=len(all_properties))

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
            logger.info("dedup_no_duplicates_found")
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
                if settings.data_dir:
                    clear_image_cache(settings.data_dir, absorbed_id)
                logger.info(
                    "dedup_property_absorbed",
                    absorbed_id=absorbed_id,
                    anchor_id=anchor_id,
                )

            merged_count += 1

        remaining = len(all_properties) - len(absorbed_ids)
        logger.info(
            "dedup_complete",
            merged_groups=merged_count,
            absorbed=len(absorbed_ids),
            remaining=remaining,
        )
