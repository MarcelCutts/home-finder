"""Pre-analysis pipeline stages — filtering, enrichment, dedup, commute."""

import time
from dataclasses import dataclass, field

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters import (
    CommuteFilter,
    CriteriaFilter,
    Deduplicator,
    LocationFilter,
    enrich_merged_properties,
    filter_by_floorplan,
)
from home_finder.filters.detail_enrichment import is_floorplan_exempt
from home_finder.logging import get_logger
from home_finder.models import (
    MergedProperty,
    Property,
    SearchCriteria,
    TransportMode,
)
from home_finder.pipeline.scraping import ScraperMetrics, _run_scrape, _source_counts
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.utils.image_cache import (
    clear_image_cache,
    copy_cached_images,
)

logger = get_logger(__name__)


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
    stage_timings: dict[str, float] = field(default_factory=dict)
    # T2: per-scraper performance metrics
    scraper_metrics: list[ScraperMetrics] = field(default_factory=list)
    # T3: funnel counts
    criteria_filtered_count: int = 0
    location_filtered_count: int = 0
    new_property_count: int = 0
    commute_within_limit_count: int = 0
    post_dedup_count: int = 0
    post_floorplan_count: int = 0


async def _cross_run_deduplicate(
    deduplicator: Deduplicator,
    merged_to_notify: list[MergedProperty],
    storage: PropertyStorage,
    re_enrichment_ids: set[str],
    *,
    data_dir: str = "",
) -> CrossRunDedupResult:
    """Deduplicate new properties against recent DB anchors (cross-run detection).

    Loads recent DB properties as anchors so a property appearing on platform B
    today can be matched against platform A stored last week. Updates anchors
    that gained new sources and cleans up consumed retry rows.

    When a cross-run merge adds a new source to an anchor, the anchor is flagged
    for quality re-analysis (new images/descriptions warrant a fresh look) and
    cached images are copied from the new source to the anchor's cache directory.

    Args:
        deduplicator: Deduplicator instance for cross-platform matching.
        merged_to_notify: New/re-enriched properties to check.
        storage: Database storage for anchor lookup and updates.
        re_enrichment_ids: IDs of properties being re-enriched (for cleanup).
        data_dir: Base data directory for image cache operations.

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

    # Map source URLs from new properties to their original unique_id so we
    # can copy cached images from the new source to the anchor after merge.
    new_url_to_unique_id: dict[str, str] = {}
    new_id_to_price: dict[str, int] = {}
    for mp in merged_to_notify:
        for url in mp.source_urls.values():
            new_url_to_unique_id[str(url)] = mp.canonical.unique_id
        new_id_to_price[mp.canonical.unique_id] = mp.canonical.price_pcm

    # Combine new properties with DB anchors for dedup comparison
    combined_for_dedup = merged_to_notify + db_anchors
    dedup_results = await deduplicator.deduplicate_merged_async(combined_for_dedup)

    # Split: anchors that gained new sources vs genuinely new properties
    genuinely_new: list[MergedProperty] = []
    anchors_updated = 0
    seen_anchor_ids: set[str] = set()
    absorbed_to_anchor: dict[str, str] = {}  # new_id -> anchor_id
    for merged in dedup_results:
        # Check if this result involves any DB anchor (by URL match)
        matched_anchor_id: str | None = None
        for url in merged.source_urls.values():
            aid = anchor_url_to_id.get(str(url))
            if aid is not None:
                matched_anchor_id = aid
                break

        if matched_anchor_id is not None:
            if matched_anchor_id in seen_anchor_ids:
                continue
            seen_anchor_ids.add(matched_anchor_id)

            # Check if any new property was actually merged into this anchor
            # (vs the anchor just passing through dedup unchanged)
            new_property_merged = any(
                str(url) in new_url_to_unique_id for url in merged.source_urls.values()
            )
            if not new_property_merged:
                continue

            original_anchor = anchor_by_id[matched_anchor_id]

            # Always update metadata (URLs, descriptions, prices)
            await storage.update_merged_sources(matched_anchor_id, merged)
            anchors_updated += 1

            # Track which new IDs were absorbed into this anchor
            absorbed_ids = [
                new_url_to_unique_id[str(url)]
                for url in merged.source_urls.values()
                if str(url) in new_url_to_unique_id
            ]
            for aid in absorbed_ids:
                absorbed_to_anchor[aid] = matched_anchor_id

            new_sources_added = [
                s.value for s in set(merged.sources) - set(original_anchor.sources)
            ]
            logger.info(
                "cross_run_anchor_match",
                anchor_id=matched_anchor_id,
                absorbed_ids=absorbed_ids,
                anchor_postcode=original_anchor.canonical.postcode,
                anchor_price=original_anchor.canonical.price_pcm,
                anchor_sources=[s.value for s in original_anchor.sources],
                new_sources_added=new_sources_added,
            )

            # Only copy images + reanalyze when genuinely new sources added
            truly_new_sources = set(merged.sources) - set(original_anchor.sources)
            if truly_new_sources:
                # Detect cross-platform price drops: if a new source lists at
                # a lower price than the anchor's DB price, record the change.
                # _detect_price_changes (Step 3b) uses single-source unique_ids
                # that don't match DB anchors, so this is the only path for
                # cross-platform price detection.
                new_source_prices: list[int] = []
                for url in merged.source_urls.values():
                    url_str = str(url)
                    nid = new_url_to_unique_id.get(url_str)
                    if nid is not None and nid in new_id_to_price:
                        new_source_prices.append(new_id_to_price[nid])
                if new_source_prices:
                    lowest_new_price = min(new_source_prices)
                    change = await storage.detect_and_record_price_change(
                        matched_anchor_id,
                        lowest_new_price,
                        source="cross_platform",
                    )
                    if change is not None and change < 0:
                        logger.info(
                            "cross_platform_price_drop",
                            anchor_id=matched_anchor_id,
                            change=change,
                            new_price=lowest_new_price,
                        )

                # Copy cached images from new source(s) to anchor directory
                if data_dir:
                    original_urls = {str(u) for u in original_anchor.source_urls.values()}
                    for url in merged.source_urls.values():
                        url_str = str(url)
                        if url_str not in original_urls and url_str in new_url_to_unique_id:
                            new_source_id = new_url_to_unique_id[url_str]
                            copy_cached_images(data_dir, new_source_id, matched_anchor_id)

                # Flag for quality re-analysis — new source brings new images/descriptions
                await storage.request_reanalysis([matched_anchor_id])
                logger.info(
                    "cross_run_reanalysis_flagged",
                    anchor_id=matched_anchor_id,
                    added_sources=[s.value for s in truly_new_sources],
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
        if data_dir:
            clear_image_cache(data_dir, uid)
        logger.debug("consumed_retry_cleaned", unique_id=uid)

    # Build per-property fates for observability
    fates: list[dict[str, str | int]] = []
    for mp in merged_to_notify:
        uid = mp.canonical.unique_id
        entry: dict[str, str | int] = {
            "id": uid,
            "source": mp.canonical.source.value,
            "price": mp.canonical.price_pcm,
            "postcode": mp.canonical.postcode or "",
        }
        if uid in genuinely_new_ids:
            entry["fate"] = "genuinely_new"
        elif uid in consumed_retries:
            entry["fate"] = "consumed_retry"
            entry["anchor"] = absorbed_to_anchor.get(uid, "")
        elif uid in absorbed_to_anchor:
            entry["fate"] = "merged_into_anchor"
            entry["anchor"] = absorbed_to_anchor[uid]
        else:
            entry["fate"] = "merged_into_anchor"
        fates.append(entry)

    merged_into_anchor_count = sum(1 for f in fates if f["fate"] == "merged_into_anchor")
    logger.info(
        "cross_run_property_fates",
        total=len(fates),
        genuinely_new=len(genuinely_new_ids),
        merged_into_anchor=merged_into_anchor_count,
        consumed_retry=len(consumed_retries),
        fates=fates,
    )

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


def _run_criteria_and_location_filters(
    properties: list[Property],
    criteria: SearchCriteria,
    search_areas: list[str],
) -> tuple[list[Property], int, int] | None:
    """Apply criteria and location filters.

    Returns None if nothing passes, or (filtered, criteria_count, location_count).
    """
    logger.info("pipeline_started", phase="criteria_filtering")
    criteria_filter = CriteriaFilter(criteria)
    filtered = criteria_filter.filter_properties(properties)
    criteria_count = len(filtered)
    logger.info(
        "criteria_filter_summary", matched=len(filtered), by_source=_source_counts(filtered)
    )

    if not filtered:
        logger.info("no_properties_match_criteria")
        return None

    logger.info("pipeline_started", phase="location_filtering")
    location_filter = LocationFilter(search_areas, strict=False)
    filtered = location_filter.filter_properties(filtered)
    location_count = len(filtered)
    logger.info(
        "location_filter_summary", matched=len(filtered), by_source=_source_counts(filtered)
    )

    if not filtered:
        logger.info("no_properties_in_search_areas")
        return None

    return filtered, criteria_count, location_count


async def _load_unenriched(
    storage: PropertyStorage,
    settings: Settings,
) -> tuple[list[MergedProperty], set[str]]:
    """Load unenriched properties for retry, clearing stale image caches."""
    max_attempts = settings.max_enrichment_attempts
    unenriched = await storage.get_unenriched_properties(max_attempts=max_attempts)
    re_enrichment_ids: set[str] = set()
    if unenriched:
        retry_stats = await storage.get_unenriched_retry_stats(max_attempts=max_attempts)
        oldest_days = max(
            (_days_since(m.canonical.first_seen.isoformat()) for m in unenriched), default=0
        )
        logger.info(
            "loaded_unenriched_for_retry",
            count=len(unenriched),
            by_source=_source_counts(unenriched),
            by_attempts=retry_stats.get("by_attempts", {}),
            oldest_days=oldest_days,
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
) -> tuple[list[MergedProperty], dict[str, tuple[int, TransportMode]], int]:
    """Geocode properties and compute commute times via TravelTime API.

    This is NOT a hard filter — it returns all properties (geocoded) plus a
    lookup of commute times for those within the configured limit.  Properties
    without coordinates or beyond the commute limit are still returned so that
    downstream steps receive geocoded data.  The only filtering is an early-exit
    gate: if TravelTime is configured and zero properties are reachable, the
    caller can abort the pipeline.

    Returns:
        Tuple of (all properties with geocoded coordinates, commute lookup
        mapping unique_id -> (minutes, transport_mode) for reachable properties,
        count of properties within commute limit).
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

        merged_with_coords = [m for m in merged if m.canonical.latitude and m.canonical.longitude]
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

        within_limit = [m for m in merged_with_coords if m.canonical.unique_id in commute_lookup]
        notify_count = len(within_limit) + len(merged_without_coords)

        logger.info(
            "commute_filter_summary",
            within_limit=notify_count,
            total_checked=len(merged_with_coords),
            without_coords=len(merged_without_coords),
            by_source=_source_counts(merged),
        )

        if not within_limit and not merged_without_coords:
            return [], commute_lookup, 0
    else:
        notify_count = len(merged)
        logger.info("skipping_commute_filter", reason="no_traveltime_credentials")

    return merged, commute_lookup, notify_count


async def _run_enrichment(
    merged: list[MergedProperty],
    settings: Settings,
    storage: PropertyStorage,
) -> list[MergedProperty] | None:
    """Enrich with detail page data and handle failures. Returns None if nothing enriched."""
    logger.info("pipeline_started", phase="detail_enrichment")
    async with DetailFetcher(
        max_gallery_images=settings.quality_filter_max_images,
        proxy_url=settings.proxy_url,
    ) as detail_fetcher:
        enrichment_result = await enrich_merged_properties(
            merged,
            detail_fetcher,
            data_dir=settings.data_dir,
            storage=storage,
        )

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
    storage: PropertyStorage,
    settings: Settings,
    re_enrichment_ids: set[str],
) -> tuple[list[MergedProperty], int, int, int] | None:
    """Cross-run dedup and floorplan gate. Returns None if nothing remains.

    Returns (merged_to_notify, anchors_updated, post_dedup_count, post_floorplan_count).
    """
    logger.info("pipeline_started", phase="deduplication_merge")
    # Cross-run dedup always uses image hashing for same-building disambiguation,
    # independent of the global enable_image_hash_matching flag. By this point
    # both sides have cached gallery images on disk.
    cross_run_deduplicator = Deduplicator(
        enable_cross_platform=True,
        enable_image_hashing=True,
        data_dir=settings.data_dir,
    )
    dedup_result = await _cross_run_deduplicate(
        cross_run_deduplicator, merged, storage, re_enrichment_ids, data_dir=settings.data_dir
    )
    merged_to_notify = dedup_result.genuinely_new
    anchors_updated = dedup_result.anchors_updated

    if not merged_to_notify:
        logger.info("no_new_properties_after_cross_run_dedup")
        return None

    genuinely_new_count = len(merged_to_notify)

    if settings.require_floorplan:
        before_count = len(merged_to_notify)
        merged_to_notify = filter_by_floorplan(
            merged_to_notify,
            min_gallery_for_photo_inference=settings.min_gallery_for_photo_inference,
        )
        photo_inference_eligible = sum(
            1
            for m in merged_to_notify
            if m.floorplan is None and not is_floorplan_exempt(m.sources)
        )
        logger.info(
            "floorplan_filter",
            before=before_count,
            after=len(merged_to_notify),
            dropped=before_count - len(merged_to_notify),
            photo_inference_eligible=photo_inference_eligible,
            by_source=_source_counts(merged_to_notify),
        )

        if not merged_to_notify:
            logger.info("no_properties_with_floorplans")
            return None

    retry_input = sum(1 for m in merged if m.unique_id in re_enrichment_ids)
    logger.info(
        "post_enrichment_funnel",
        enriched_input=len(merged),
        retry_input=retry_input,
        new_input=len(merged) - retry_input,
        after_cross_run_dedup=genuinely_new_count,
        anchors_updated=anchors_updated,
        after_floorplan_gate=len(merged_to_notify),
    )

    return merged_to_notify, anchors_updated, genuinely_new_count, len(merged_to_notify)


async def _detect_price_changes(
    merged_list: list[MergedProperty],
    storage: PropertyStorage,
) -> None:
    """Detect and record price changes for already-known properties.

    Compares the scraped price against the DB price for each property.
    Only records a change if the property exists in the DB with a different price.
    """
    drops = 0
    increases = 0
    for merged in merged_list:
        change = await storage.detect_and_record_price_change(
            merged.unique_id,
            merged.canonical.price_pcm,
            source=merged.canonical.source.value,
        )
        if change is not None:
            if change < 0:
                drops += 1
            else:
                increases += 1
    if drops or increases:
        logger.info("price_changes_detected", drops=drops, increases=increases)


def _days_since(iso_str: str | None) -> int:
    """Return integer days since ISO datetime, or 0 if invalid."""
    if not iso_str:
        return 0
    from datetime import UTC, datetime

    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - dt).days)
    except (ValueError, TypeError):
        return 0


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
    timings: dict[str, float] = {}
    scraper_metrics: list[ScraperMetrics] = []

    # Step 1: Scrape all platforms
    t0 = time.monotonic()
    scrape_result = await _run_scrape(
        settings,
        storage,
        max_per_scraper=max_per_scraper,
        only_scrapers=only_scrapers,
        full_scrape=full_scrape,
    )
    timings["scraping_seconds"] = time.monotonic() - t0
    if scrape_result is None:
        return None
    all_properties, scraper_metrics = scrape_result

    # Step 2: Criteria + location filters
    t0 = time.monotonic()
    criteria = settings.get_search_criteria()
    search_areas = settings.get_search_areas()
    filter_result = _run_criteria_and_location_filters(all_properties, criteria, search_areas)
    if filter_result is None:
        timings["filtering_seconds"] = time.monotonic() - t0
        return None
    filtered, criteria_filtered_count, location_filtered_count = filter_result

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

    # Step 3b: Detect price changes for already-known properties
    await _detect_price_changes(merged_properties, storage)

    logger.info("pipeline_started", phase="new_property_filter")
    new_merged = await storage.filter_new_merged(merged_properties)
    new_property_count = len(new_merged)
    logger.info(
        "new_property_summary", new_count=len(new_merged), by_source=_source_counts(new_merged)
    )

    # Step 4: Load unenriched retries
    unenriched, re_enrichment_ids = await _load_unenriched(storage, settings)
    timings["filtering_seconds"] = time.monotonic() - t0
    if not new_merged and not unenriched:
        logger.info("no_new_properties")
        return None

    # Step 5: Enrichment
    t0 = time.monotonic()
    merged_to_enrich = list(new_merged) + list(unenriched)
    enriched = await _run_enrichment(merged_to_enrich, settings, storage)
    if enriched is None:
        timings["enrichment_seconds"] = time.monotonic() - t0
        return None

    # Step 6: Geocode + commute (after enrichment so all properties have full coords)
    geocoded, commute_lookup, commute_within_limit_count = await _geocode_and_compute_commute(
        enriched, criteria, settings
    )
    if not geocoded:
        logger.info("no_properties_within_commute_limit")
        timings["enrichment_seconds"] = time.monotonic() - t0
        return None

    # Step 7: Post-enrichment dedup + floorplan gate
    # Use geocoded list (not enriched) so coordinates from geocoding are preserved
    post_result = await _run_post_enrichment(geocoded, storage, settings, re_enrichment_ids)
    timings["enrichment_seconds"] = time.monotonic() - t0
    if post_result is None:
        return None

    final_merged, anchors_updated, post_dedup_count, post_floorplan_count = post_result
    return PreAnalysisResult(
        merged_to_process=final_merged,
        commute_lookup=commute_lookup,
        scraped_count=len(all_properties),
        enriched_count=len(enriched),
        anchors_updated=anchors_updated,
        stage_timings=timings,
        scraper_metrics=scraper_metrics,
        criteria_filtered_count=criteria_filtered_count,
        location_filtered_count=location_filtered_count,
        new_property_count=new_property_count,
        commute_within_limit_count=commute_within_limit_count,
        post_dedup_count=post_dedup_count,
        post_floorplan_count=post_floorplan_count,
    )
