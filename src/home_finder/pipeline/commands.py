"""Pipeline maintenance commands — backfill, off-market check, retroactive dedup."""

import contextlib
import json
import time

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.filters import CommuteFilter, Deduplicator
from home_finder.logging import get_logger
from home_finder.models import CommuteResult
from home_finder.models.commute import commute_minutes_projection
from home_finder.pipeline.runner import exclusive_runner
from home_finder.utils.image_cache import copy_cached_images

logger = get_logger(__name__)


@exclusive_runner
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

        commute_results: list[CommuteResult] = []
        for mode in criteria.transport_modes:
            results = await commute_filter.filter_properties(
                properties,
                max_minutes=criteria.max_commute_minutes,
                transport_mode=mode,
            )
            commute_results.extend(results)

        commute_lookup = commute_minutes_projection(commute_results)
        if not commute_lookup:
            logger.info("no_measured_commute_results", unknown=len(properties))
            return

        updated = await storage.update_commute_data(commute_lookup)
        logger.info("backfill_commute_complete", updated=updated, total=len(properties))


@exclusive_runner
async def run_check_off_market(
    settings: Settings,
    *,
    only_scrapers: set[str] | None = None,
) -> None:
    """Check active properties for off-market removal/let-agreed signals.

    Persists per-source check results on source_listings when available,
    then derives the golden record status using "any-active-means-active".

    For legacy golden records without source_listings linkage, falls back to
    aggregating check results directly (the previous behaviour).

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

        # Build check list — prefer source_listings, fall back to source_urls JSON
        # Each check: (property_id, source, url)
        # Also track which checks map to a source_listing unique_id
        checks: list[tuple[str, str, str]] = []
        # Map (property_id, source, url) -> source_listing unique_id (if linked)
        sl_lookup: dict[tuple[str, str, str], str] = {}

        for prop in db_props:
            linked_sls: list[dict[str, object]] = prop.get("source_listings", [])
            covered_urls: set[str] = set()

            if linked_sls:
                # Source_listings-first path
                for sl in linked_sls:
                    source = str(sl["source"])
                    url = str(sl["url"])
                    sl_uid = str(sl["unique_id"])
                    covered_urls.add(url)
                    key = (prop["unique_id"], source, url)
                    checks.append(key)
                    sl_lookup[key] = sl_uid

            # Also check source_urls JSON for any URLs not covered by linked rows.
            # This handles partial linkage — e.g. property merged from OpenRent +
            # Zoopla where only OpenRent has a source_listings row.
            source_urls: dict[str, str] = {}
            if prop.get("source_urls"):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    source_urls = json.loads(prop["source_urls"])

            if source_urls:
                for source, url in source_urls.items():
                    if url in covered_urls:
                        continue
                    checks.append((prop["unique_id"], source, url))
            elif not linked_sls:
                # No source_listings and no source_urls — use primary source/url
                source = prop["source"]
                checks.append((prop["unique_id"], source, prop["url"]))

        # Removal requires evidence for every known URL, including URLs outside
        # a selected source subset. Multiple listings on one portal are distinct.
        checks = list(dict.fromkeys(checks))
        expected: dict[str, set[tuple[str, str]]] = {}
        for uid, source, url in checks:
            expected.setdefault(uid, set()).add((source, url))
        if only_scrapers:
            checks = [check for check in checks if check[1] in only_scrapers]

        if not checks:
            logger.info("no_urls_to_check", reason="source filter may have excluded all")
            return

        logger.info("off_market_checking_urls", urls=len(checks), properties=len(db_props))

        check_t0 = time.monotonic()
        async with OffMarketChecker(proxy_url=settings.proxy_url) as checker:
            batch = await checker.check_batch(checks)
        check_elapsed = round(time.monotonic() - check_t0, 1)

        requested = set(checks)
        results = [r for r in batch.results if (r.property_id, r.source, r.url) in requested]

        # --- Persist per-source results on source_listings ---
        for r in results:
            key = (r.property_id, r.source, r.url)
            checked_sl_uid = sl_lookup.get(key)
            if checked_sl_uid:
                # Stamp last_checked_at regardless of result
                await storage.update_source_listing_last_checked(checked_sl_uid)

                if r.status == ListingStatus.REMOVED:
                    await storage.mark_source_listing_off_market(checked_sl_uid, "removed")
                elif r.status == ListingStatus.LET_AGREED:
                    await storage.mark_source_listing_off_market(checked_sl_uid, "let_agreed")
                elif r.status == ListingStatus.ACTIVE:
                    await storage.mark_source_listing_active(checked_sl_uid)
                # UNKNOWN: no change to off-market flags

        # --- Derive golden record status ---
        # "Not active" means REMOVED or LET_AGREED
        def _is_inactive(s: ListingStatus) -> bool:
            return s in (ListingStatus.REMOVED, ListingStatus.LET_AGREED)

        per_property: dict[str, dict[tuple[str, str], ListingStatus]] = {}
        for r in results:
            per_property.setdefault(r.property_id, {})[(r.source, r.url)] = r.status

        marked_off = 0
        marked_returned = 0
        for prop in db_props:
            uid = prop["unique_id"]
            observed_statuses = per_property.get(uid, {})
            if not observed_statuses:
                continue
            source_statuses = {
                key: observed_statuses.get(key, ListingStatus.UNKNOWN) for key in expected[uid]
            }

            has_active = any(s == ListingStatus.ACTIVE for s in source_statuses.values())
            all_inactive = all(_is_inactive(s) for s in source_statuses.values())
            was_off_market = bool(prop.get("is_off_market"))

            # Prefer an explicit let-agreed observation over a missing URL.
            reason: str | None = None
            if all_inactive:
                reason = (
                    "let_agreed"
                    if ListingStatus.LET_AGREED in source_statuses.values()
                    else "removed"
                )

            if all_inactive:
                if not was_off_market:
                    await storage.mark_off_market(uid, reason=reason)
                    marked_off += 1
                    logger.info(
                        "property_confirmed_off_market",
                        unique_id=uid,
                        reason=reason,
                        sources=sorted({source for source, _ in source_statuses}),
                    )
            elif has_active and was_off_market:
                await storage.mark_returned_to_market(uid)
                marked_returned += 1
                logger.info(
                    "property_returned_to_market",
                    unique_id=uid,
                    active_sources=[
                        source
                        for (source, _), st in source_statuses.items()
                        if st == ListingStatus.ACTIVE
                    ],
                )

            # Stamp last_checked_at on golden record
            await storage.update_property_last_checked(uid)

        # Summary
        status_counts = {s.value: 0 for s in ListingStatus}
        for r in results:
            status_counts[r.status.value] += 1

        logger.info(
            "off_market_check_complete",
            status_counts=status_counts,
            marked_off=marked_off,
            marked_returned=marked_returned,
            by_source=batch.by_source,
            elapsed_s=check_elapsed,
            circuit_breakers_tripped=batch.circuit_breakers_tripped,
        )


@exclusive_runner
async def run_dedup_existing(settings: Settings) -> None:
    """Report established duplicate groups and preserve their histories.

    Only unused pending staging rows can be retired automatically. Established
    records keep their identity and history; ambiguous groups are reported.
    """
    async with PropertyStorage(settings.database_path) as storage:
        established = await storage.get_recent_properties_for_dedup(days=None)
        established_ids = {merged.unique_id for merged in established}
        # Reuse the retry queue: these are the pending rows this command can
        # safely retire, subject to the transaction's live history checks.
        all_properties = [*established, *await storage.pipeline.get_unenriched_properties()]
        if not all_properties:
            logger.info("dedup_no_properties_in_database")
            return

        logger.info("dedup_started", count=len(all_properties))

        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=settings.enable_image_hash_matching,
            data_dir=settings.data_dir,
        )
        dedup_results = await deduplicator.deduplicate_merged_async(all_properties)
        merged_count = 0
        retired_count = 0
        deferred_count = 0
        proposed_count = 0
        for merged in dedup_results:
            merged_urls = {str(url) for url in merged.source_urls.values()}
            group_ids = {
                original.unique_id
                for original in all_properties
                if merged_urls & {str(url) for url in original.source_urls.values()}
            }
            if len(group_ids) < 2:
                continue
            proposed_count += len(group_ids) - 1
            anchors = group_ids & established_ids
            # Preserve an established anchor even if a staging listing happened
            # to be observed earlier and won the in-memory canonical selection.
            anchor_id = next(iter(anchors)) if len(anchors) == 1 else merged.unique_id
            absorbed_into_this = sorted(group_ids - {anchor_id})
            try:
                retired = await storage.absorb_staging_properties(
                    anchor_id, merged, absorbed_ids=absorbed_into_this
                )
            except MergeBlockedError:
                deferred_count += len(absorbed_into_this)
                continue

            # Cache copies happen after the atomic database change.
            # Retain the original files for recovery; never delete merge inputs.
            for absorbed_id in absorbed_into_this:
                if settings.data_dir:
                    copied = copy_cached_images(settings.data_dir, absorbed_id, anchor_id)
                    if copied:
                        logger.info(
                            "dedup_images_copied",
                            from_id=absorbed_id,
                            to_id=anchor_id,
                            count=copied,
                        )
                logger.info(
                    "dedup_property_absorbed",
                    absorbed_id=absorbed_id,
                    anchor_id=anchor_id,
                )

            merged_count += 1
            retired_count += len(retired)

        if proposed_count == 0:
            logger.info("dedup_no_duplicates_found")
            return
        remaining = len(all_properties) - retired_count
        logger.info(
            "dedup_complete",
            merged_groups=merged_count,
            proposed_absorptions=proposed_count,
            absorbed=retired_count,
            deferred=deferred_count,
            remaining=remaining,
        )
