"""Prepare candidates: collect, select, enrich, assess commute, and consolidate."""

import time
from itertools import zip_longest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.enrichment.commute import CommuteAssessor
from home_finder.enrichment.details import enrich_merged_properties
from home_finder.filters import CriteriaFilter, LocationFilter
from home_finder.filters.media import filter_by_floorplan, is_floorplan_exempt
from home_finder.logging import get_logger
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import (
    CommuteResult,
    CommuteState,
    PropertyAggregate,
    SearchCriteria,
    SourceListing,
    TransportMode,
)
from home_finder.models.commute import best_measured_commutes, commute_minutes_projection
from home_finder.pipeline.cache_recovery import repair_merged_image_caches
from home_finder.pipeline.collection import collect_listings, count_by_source
from home_finder.pipeline.contracts import (
    CandidateAdmission,
    CandidateBatch,
    CandidateProgress,
    CommuteAssessments,
    FilteredListings,
)
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent
from home_finder.pipeline.matching import consolidate_candidates
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.utils.image_cache import clear_image_cache

logger = get_logger(__name__)


async def prepare_candidates(
    settings: Settings,
    storage: PropertyStorage,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
    recorder: EventRecorder | None = None,
    progress: CandidateProgress | None = None,
) -> CandidateBatch:
    """Return candidates ready for initial assessment, including an empty batch.

    Received observations are retained before selection. Pending enrichment is
    admitted alongside discovery; empty discovery never ends the whole run.
    The supplied progress snapshot remains available on failure/cancellation.
    """
    progress = progress if progress is not None else CandidateProgress()
    batch = CandidateBatch(progress=progress)
    timings = progress.stage_timings
    active_phase = "collection_seconds"

    t0 = time.monotonic()
    try:
        # Scrape all platforms
        t0 = time.monotonic()
        scrape_result = await collect_listings(
            settings,
            storage,
            max_per_scraper=max_per_scraper,
            only_scrapers=only_scrapers,
            full_scrape=full_scrape,
            metrics_out=progress.scraper_metrics,
        )
        timings["collection_seconds"] = time.monotonic() - t0
        all_properties = scrape_result.listings
        progress.scraper_metrics = scrape_result.scraper_metrics
        progress.observed_listing_count = max(
            len(all_properties), sum(m.properties_found for m in progress.scraper_metrics)
        )

        # Record all scraped listings in source_listings
        sl_count = await storage.upsert_source_listings(all_properties)
        logger.info("source_listings_upserted", count=sl_count)

        # Criteria + location filters
        active_phase = "filtering_seconds"
        t0 = time.monotonic()
        criteria = settings.get_search_criteria()
        search_areas = settings.get_search_areas()
        filter_result = _select_listings(
            all_properties, criteria, search_areas, recorder=recorder, progress=progress
        )
        filtered = filter_result.listings
        if recorder is not None:
            await recorder.flush()

        # Form single-source aggregates, then select newly discovered listings.
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
            by_source=count_by_source(merged_properties),
        )

        # Detect price changes for already-known properties
        await _detect_price_changes(merged_properties, storage)

        logger.info("pipeline_started", phase="new_property_filter")
        new_merged = await storage.filter_new_merged(merged_properties)
        new_listing_count = len(new_merged)
        progress.new_listing_count = new_listing_count
        logger.info(
            "new_property_summary", new_count=len(new_merged), by_source=count_by_source(new_merged)
        )

        # record new property filter events
        if recorder is not None:
            new_ids = {m.canonical.unique_id for m in new_merged}
            for m in merged_properties:
                uid = m.canonical.unique_id
                src = m.canonical.source.value
                if uid in new_ids:
                    recorder.record(PropertyEvent(uid, src, "new_property_passed", "new_property"))
                else:
                    recorder.record(
                        PropertyEvent(
                            uid,
                            src,
                            "duplicate_known",
                            "new_property",
                            {"reason": "already_seen"},
                        )
                    )
            await recorder.flush()

        # Load unenriched retries
        unenriched, re_enrichment_ids = await _load_pending_enrichment(storage, settings)
        timings["filtering_seconds"] = time.monotonic() - t0
        if not new_merged and not unenriched:
            logger.info("no_new_properties")
            return batch

        # Enrichment
        active_phase = "preparation_seconds"
        t0 = time.monotonic()
        merged_to_enrich = await _prepare_enrichment_batch(
            new_merged, unenriched, storage, settings, recorder=recorder
        )
        enriched = await _enrich_candidates(merged_to_enrich, settings, storage, recorder=recorder)
        if not enriched:
            timings["preparation_seconds"] = time.monotonic() - t0
            return batch
        progress.media_ready_count = len(enriched)

        # Assess commute after detail acquisition, which may supply missing coordinates.
        commutes = await _assess_commutes(enriched, criteria, settings, recorder=recorder)
        geocoded = commutes.candidates
        batch.commute_lookup = commutes.measured_commutes
        progress.commute_within_limit_count = commutes.within_limit_count
        if not geocoded:
            logger.info("no_properties_after_enrichment")
            timings["preparation_seconds"] = time.monotonic() - t0
            return batch

        # Consolidate matching evidence, then apply media admission.
        # Use geocoded list (not enriched) so coordinates from geocoding are preserved
        post_result = await _consolidate_and_admit(
            geocoded,
            storage,
            settings,
            re_enrichment_ids,
            recorder=recorder,
            commute_lookup=batch.commute_lookup,
            progress=progress,
        )
        timings["preparation_seconds"] = time.monotonic() - t0
        batch.candidates = post_result.candidates
        progress.prepared_candidate_count = len(batch.candidates)
        return batch
    finally:
        timings[active_phase] = time.monotonic() - t0
        progress.observed_listing_count = max(
            progress.observed_listing_count,
            sum(m.properties_found for m in progress.scraper_metrics),
        )


def _select_listings(
    properties: list[SourceListing],
    criteria: SearchCriteria,
    search_areas: list[str],
    *,
    recorder: EventRecorder | None = None,
    progress: CandidateProgress | None = None,
) -> FilteredListings:
    """Apply criteria and location filters.

    Returns an explicit empty selection when no listings pass.
    """
    logger.info("pipeline_started", phase="criteria_filtering")
    criteria_filter = CriteriaFilter(criteria)
    filtered = criteria_filter.filter_properties(properties)
    criteria_count = len(filtered)
    if progress is not None:
        progress.criteria_passed_count = criteria_count
    logger.info(
        "criteria_filter_summary", matched=len(filtered), by_source=count_by_source(filtered)
    )

    # record criteria pass/drop events
    if recorder is not None:
        filtered_ids = {p.unique_id for p in filtered}
        for prop in properties:
            if prop.unique_id in filtered_ids:
                recorder.record(
                    PropertyEvent(prop.unique_id, prop.source.value, "criteria_passed", "criteria")
                )
            else:
                recorder.record(
                    PropertyEvent(
                        prop.unique_id,
                        prop.source.value,
                        "criteria_dropped",
                        "criteria",
                        {"price": prop.price_pcm, "bedrooms": prop.bedrooms},
                    )
                )

    if not filtered:
        logger.info("no_properties_match_criteria")
        return FilteredListings(criteria_passed_count=criteria_count)

    logger.info("pipeline_started", phase="location_filtering")
    location_filter = LocationFilter(search_areas, strict=False)
    pre_location = filtered
    filtered = location_filter.filter_properties(filtered)
    location_count = len(filtered)
    if progress is not None:
        progress.location_passed_count = location_count
    logger.info(
        "location_filter_summary", matched=len(filtered), by_source=count_by_source(filtered)
    )

    # record location pass/drop events
    if recorder is not None:
        filtered_ids = {p.unique_id for p in filtered}
        for prop in pre_location:
            if prop.unique_id in filtered_ids:
                recorder.record(
                    PropertyEvent(prop.unique_id, prop.source.value, "location_passed", "location")
                )
            else:
                recorder.record(
                    PropertyEvent(
                        prop.unique_id,
                        prop.source.value,
                        "location_dropped",
                        "location",
                        {"postcode": prop.postcode},
                    )
                )

    if not filtered:
        logger.info("no_properties_in_search_areas")
        return FilteredListings(criteria_passed_count=criteria_count)

    return FilteredListings(filtered, criteria_count, location_count)


async def _load_pending_enrichment(
    storage: PropertyStorage,
    settings: Settings,
) -> tuple[list[PropertyAggregate], set[str]]:
    """Load the pending backlog without consuming slots or clearing cache assets."""
    max_attempts = settings.max_enrichment_attempts
    unenriched = await storage.processing.get_unenriched_properties(max_attempts=max_attempts)
    re_enrichment_ids: set[str] = set()
    if unenriched:
        retry_stats = await storage.processing.get_unenriched_retry_stats(max_attempts=max_attempts)
        oldest_days = max(
            (_days_since(m.canonical.first_seen.isoformat()) for m in unenriched), default=0
        )
        logger.info(
            "loaded_unenriched_for_retry",
            count=len(unenriched),
            by_source=count_by_source(unenriched),
            by_attempts=retry_stats.get("by_attempts", {}),
            oldest_days=oldest_days,
        )
        re_enrichment_ids = {m.unique_id for m in unenriched}
    return unenriched, re_enrichment_ids


async def _prepare_enrichment_batch(
    new: list[PropertyAggregate],
    pending: list[PropertyAggregate],
    storage: PropertyStorage,
    settings: Settings,
    *,
    recorder: EventRecorder | None = None,
) -> list[PropertyAggregate]:
    """Persist discovery, then share a finite batch between recovery and new homes."""
    for merged in new:
        await storage.processing.save_unenriched_property(merged, attempted=False)

    # Same fairness policy as paid analysis: oldest pending first, alternating
    # with discovery. A stream of fresh results cannot starve recovery.
    candidates = list(
        {
            item.unique_id: item
            for pair in zip_longest(pending, new)
            for item in pair
            if item is not None
        }.values()
    )
    limit = settings.max_enrichment_per_run
    selected, deferred = candidates[:limit], candidates[limit:]
    if deferred:
        logger.info(
            "enrichment_run_limit",
            selected=len(selected),
            deferred=len(deferred),
            limit=limit,
        )
        if recorder is not None:
            for merged in deferred:
                recorder.record(
                    PropertyEvent(
                        merged.unique_id,
                        merged.canonical.source.value,
                        "enrichment_deferred",
                        "enrichment",
                        {"reason": "run_limit", "limit": limit},
                    )
                )
            await recorder.flush()

    retry_ids = {m.unique_id for m in pending}
    for merged in selected:
        if merged.unique_id in retry_ids and settings.data_dir:
            clear_image_cache(settings.data_dir, merged.unique_id)
    return selected


async def _enrich_candidates(
    merged: list[PropertyAggregate],
    settings: Settings,
    storage: PropertyStorage,
    *,
    recorder: EventRecorder | None = None,
) -> list[PropertyAggregate]:
    """Acquire details, persist retry state, and return candidates with usable media."""
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

    enriched = enrichment_result.with_usable_media

    for candidate in enrichment_result.without_usable_media:
        await storage.processing.save_unenriched_property(candidate)

    max_attempts = settings.max_enrichment_attempts
    expired = await storage.processing.expire_unenriched(max_attempts=max_attempts)

    logger.info(
        "enrichment_summary",
        media_ready=len(enrichment_result.with_usable_media),
        missing_media=len(enrichment_result.without_usable_media),
        expired=expired,
        with_floorplan=sum(1 for m in enriched if m.floorplan),
        with_images=sum(1 for m in enriched if m.images),
        by_source=count_by_source(enriched),
    )

    # record enrichment events
    if recorder is not None:
        for m in enrichment_result.with_usable_media:
            recorder.record(
                PropertyEvent(
                    m.canonical.unique_id,
                    m.canonical.source.value,
                    "enriched",
                    "enrichment",
                    {"gallery_count": len(m.images), "has_floorplan": bool(m.floorplan)},
                )
            )
        for m in enrichment_result.without_usable_media:
            recorder.record(
                PropertyEvent(
                    m.canonical.unique_id,
                    m.canonical.source.value,
                    "enrichment_deferred",
                    "enrichment",
                    {"reason": "missing_usable_media"},
                )
            )
        await recorder.flush()

    return enriched


async def _assess_commutes(
    merged: list[PropertyAggregate],
    criteria: SearchCriteria,
    settings: Settings,
    *,
    recorder: EventRecorder | None = None,
) -> CommuteAssessments:
    """Annotate an advisory commute decision, retaining every input property.

    The count is measured journeys within the limit, not properties admitted or
    a monotonic funnel stage. Missing/error/unreachable results are unknown.
    Known over-limit measurements remain useful, but never remove a home.
    """
    results: list[CommuteResult] = []
    configured = bool(settings.traveltime_app_id and settings.traveltime_api_key)
    if configured:
        assert settings.traveltime_api_key is not None
        commute_assessor = CommuteAssessor(
            app_id=settings.traveltime_app_id,
            api_key=settings.traveltime_api_key.get_secret_value(),
            destination_postcode=criteria.destination_postcode,
        )
        merged = await commute_assessor.geocode_properties(merged)
        for mode in criteria.transport_modes:
            mode_results = await commute_assessor.assess_commutes(
                [m.canonical for m in merged],
                max_minutes=criteria.max_commute_minutes,
                transport_mode=mode,
            )
            by_id = {r.property_id: r for r in mode_results if r.transport_mode == mode}
            for m in merged:
                # Defensive completeness when an adapter omits an input.
                results.append(
                    by_id.get(m.unique_id)
                    or CommuteResult(
                        property_id=m.unique_id,
                        destination_postcode=criteria.destination_postcode,
                        transport_mode=mode,
                        limit_seconds=criteria.max_commute_minutes * 60,
                        state=CommuteState.INVALID_RESPONSE,
                    )
                )
    else:
        logger.info("skipping_commute_assessment", reason="not_configured")

    selected = best_measured_commutes(results)
    commute_lookup = commute_minutes_projection(results)
    within_count = sum(r.within_limit is True for r in selected.values())
    over_count = sum(r.within_limit is False for r in selected.values())
    unknown_count = len(merged) - len(selected)
    logger.info(
        "commute_assessment_summary",
        policy="advisory",
        within_limit=within_count,
        over_limit=over_count,
        unknown=unknown_count,
        retained=len(merged),
        dropped=0,
        by_source=count_by_source(merged),
    )

    if recorder is not None:
        for m in merged:
            chosen = selected.get(m.unique_id)
            states = {
                r.transport_mode.value: r.state.value
                for r in results
                if r.property_id == m.unique_id
            }
            event_type = (
                "commute_passed"
                if chosen and chosen.within_limit is True
                else "commute_over_limit"
                if chosen
                else "commute_unknown"
            )
            metadata: dict[str, object] = {
                "policy": "advisory",
                "retained": True,
                "mode_states": states,
                "within_limit": chosen.within_limit if chosen else None,
            }
            if chosen:
                metadata.update(
                    {
                        "minutes": chosen.travel_time_minutes,
                        "mode": chosen.transport_mode.value,
                    }
                )
            elif not configured or not criteria.transport_modes:
                metadata["reason"] = "not_configured"
            recorder.record(
                PropertyEvent(
                    m.unique_id,
                    m.canonical.source.value,
                    event_type,
                    "commute",
                    metadata,
                )
            )
        await recorder.flush()

    return CommuteAssessments(merged, commute_lookup, within_count)


async def _consolidate_and_admit(
    merged: list[PropertyAggregate],
    storage: PropertyStorage,
    settings: Settings,
    re_enrichment_ids: set[str],
    *,
    recorder: EventRecorder | None = None,
    commute_lookup: dict[str, tuple[int, TransportMode]] | None = None,
    progress: CandidateProgress | None = None,
) -> CandidateAdmission:
    """Consolidate candidates and apply floorplan/photo admission, retaining counts."""
    logger.info("pipeline_started", phase="deduplication_merge")
    # Cross-run dedup always uses image hashing for same-building disambiguation,
    # independent of the global enable_image_hash_matching flag. By this point
    # both sides have cached gallery images on disk.
    cross_run_deduplicator = Deduplicator(
        enable_cross_platform=True,
        enable_image_hashing=True,
        data_dir=settings.data_dir,
    )
    dedup_result = await consolidate_candidates(
        cross_run_deduplicator,
        merged,
        storage,
        re_enrichment_ids,
        data_dir=settings.data_dir,
        recorder=recorder,
    )
    analysis_candidates = dedup_result.unmatched_candidates
    anchors_updated = dedup_result.anchors_updated
    if progress is not None:
        progress.anchors_updated = anchors_updated
        progress.unmatched_candidate_count = len(analysis_candidates)
        progress.media_admitted_count = len(analysis_candidates)

    if not analysis_candidates:
        logger.info("no_new_properties_after_cross_run_dedup")
        return CandidateAdmission(anchors_updated=anchors_updated)

    unmatched_count = len(analysis_candidates)

    if settings.require_floorplan:
        before_count = len(analysis_candidates)
        pre_floorplan = list(analysis_candidates)
        analysis_candidates = filter_by_floorplan(
            analysis_candidates,
            min_gallery_for_photo_inference=settings.min_gallery_for_photo_inference,
        )
        if progress is not None:
            progress.media_admitted_count = len(analysis_candidates)
        photo_inference_eligible = sum(
            1
            for m in analysis_candidates
            if m.floorplan is None and not is_floorplan_exempt(m.sources)
        )
        logger.info(
            "floorplan_filter",
            before=before_count,
            after=len(analysis_candidates),
            dropped=before_count - len(analysis_candidates),
            photo_inference_eligible=photo_inference_eligible,
            by_source=count_by_source(analysis_candidates),
        )

        # Compute passed/dropped sets (shared by save + event recorder)
        passed_ids = {m.unique_id for m in analysis_candidates}
        dropped = [m for m in pre_floorplan if m.unique_id not in passed_ids]
        refused: dict[str, dict[str, list[str]]] = {}

        # Save floorplan-dropped properties so they don't get re-enriched
        if dropped and commute_lookup is not None:
            persisted = await storage.processing.save_dropped_properties(dropped, commute_lookup)
            refused = persisted.refused
            await repair_merged_image_caches(
                storage, settings.data_dir, [m.unique_id for m in persisted.accepted]
            )

        # record floorplan gate events
        if recorder is not None:
            for m in pre_floorplan:
                uid = m.canonical.unique_id
                src = m.canonical.source.value
                if uid in passed_ids:
                    recorder.record(PropertyEvent(uid, src, "floorplan_passed", "floorplan"))
                elif uid in refused:
                    recorder.record(
                        PropertyEvent(
                            uid,
                            src,
                            "persistence_refused",
                            "floorplan",
                            {"reason": "merge_review", "blockers": refused[uid]},
                        )
                    )
                else:
                    recorder.record(
                        PropertyEvent(
                            uid,
                            src,
                            "floorplan_dropped",
                            "floorplan",
                            {"reason": "no_floorplan"},
                        )
                    )
            await recorder.flush()

        if not analysis_candidates:
            logger.info("no_properties_with_floorplans")
            return CandidateAdmission(
                anchors_updated=anchors_updated, unmatched_candidate_count=unmatched_count
            )

    retry_input = sum(1 for m in merged if m.unique_id in re_enrichment_ids)
    logger.info(
        "post_enrichment_funnel",
        enriched_input=len(merged),
        retry_input=retry_input,
        new_input=len(merged) - retry_input,
        after_cross_run_dedup=unmatched_count,
        anchors_updated=anchors_updated,
        after_floorplan_gate=len(analysis_candidates),
    )

    return CandidateAdmission(
        analysis_candidates, anchors_updated, unmatched_count, len(analysis_candidates)
    )


async def _detect_price_changes(
    merged_list: list[PropertyAggregate],
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
