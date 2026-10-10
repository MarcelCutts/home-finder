"""Pre-analysis pipeline stages — filtering, enrichment, dedup, commute."""

import time
from dataclasses import dataclass, field

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.merge_safety import MergeBlockedError
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
    CommuteResult,
    CommuteState,
    MergedProperty,
    Property,
    SearchCriteria,
    TransportMode,
)
from home_finder.models.commute import best_measured_commutes, commute_minutes_projection
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent
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

    @property
    def collection_status(self) -> str:
        """Separate a partial/failed acquisition from a healthy empty result."""
        attempted = [m for m in self.scraper_metrics if m.areas_attempted > 0]
        if all(m.is_healthy for m in attempted):
            return "completed"
        if any(m.properties_found or m.is_healthy for m in attempted):
            return "partial"
        return "failed"


async def _cross_run_deduplicate(
    deduplicator: Deduplicator,
    merged_to_notify: list[MergedProperty],
    storage: PropertyStorage,
    re_enrichment_ids: set[str],
    *,
    data_dir: str = "",
    recorder: EventRecorder | None = None,
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
    # Ensure source_listings exist for new properties (may already exist
    # from Step 1b; upsert is idempotent).  This is needed so that
    # update_merged_sources can rebuild from source_listings.
    await storage.upsert_source_listings([mp.canonical for mp in merged_to_notify])

    db_anchors = await storage.get_recent_properties_for_dedup(days=30)
    logger.info("loaded_dedup_anchors", anchor_count=len(db_anchors))

    # Map each anchor's source URLs to its DB unique_id so we can detect
    # merges even when the deduplicator picks a different canonical.
    anchor_url_to_ids: dict[str, set[str]] = {}
    anchor_by_id: dict[str, MergedProperty] = {}
    for anchor in db_anchors:
        anchor_by_id[anchor.canonical.unique_id] = anchor
        for url in anchor.source_urls.values():
            anchor_url_to_ids.setdefault(str(url), set()).add(anchor.canonical.unique_id)

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
    retired_staging_ids: set[str] = set()
    represented_new_ids: set[str] = set()
    deferred_ids: set[str] = set()
    new_by_id = {mp.unique_id: mp for mp in merged_to_notify}
    for merged in dedup_results:
        # Check if this result involves any DB anchor (by URL match)
        matched_anchor_ids: set[str] = set()
        for url in merged.source_urls.values():
            matched_anchor_ids.update(anchor_url_to_ids.get(str(url), set()))
        candidate_ids = {
            new_url_to_unique_id[str(url)]
            for url in merged.source_urls.values()
            if str(url) in new_url_to_unique_id
        }
        if len(matched_anchor_ids) > 1:
            deferred_ids.update(candidate_ids)
            logger.warning(
                "canonical_merge_deferred",
                anchor_ids=sorted(matched_anchor_ids),
                absorbed_ids=sorted(candidate_ids),
                reason="multiple_established_anchors",
            )
            continue
        matched_anchor_id = next(iter(matched_anchor_ids), None)

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

            # Track which new IDs were absorbed into this anchor
            absorbed_ids = sorted(candidate_ids - {matched_anchor_id})

            # Check history, move all source memberships and retire pending rows
            # under one write lock. A refused group leaves every identity intact.
            try:
                retired = await storage.absorb_staging_properties(
                    matched_anchor_id, merged, absorbed_ids=absorbed_ids
                )
            except MergeBlockedError:
                deferred_ids.update(candidate_ids)
                continue
            represented_new_ids.update(candidate_ids)
            retired_staging_ids.update(retired)
            for aid in absorbed_ids:
                absorbed_to_anchor[aid] = matched_anchor_id
            anchors_updated += 1

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
                    lowest_new_price = min(original_anchor.canonical.price_pcm, *new_source_prices)
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

                # The merge transaction already queued assessed anchors before
                # any fallible filesystem copy or price follow-up.
                logger.info(
                    "cross_run_reanalysis_flagged",
                    anchor_id=matched_anchor_id,
                    added_sources=[s.value for s in truly_new_sources],
                    original_sources=[s.value for s in original_anchor.sources],
                )
        else:
            genuinely_new.append(merged)
            represented_new_ids.update(candidate_ids)

    # A refused or ambiguous group remains independently processable. Never infer
    # deletion from absence in a deduplicator result (including a missing retry).
    unrepresented_ids = new_by_id.keys() - represented_new_ids
    genuinely_new.extend(new_by_id[uid] for uid in sorted(unrepresented_ids))
    deferred_ids.update(unrepresented_ids)
    genuinely_new_ids = {m.unique_id for m in genuinely_new}
    consumed_retries = re_enrichment_ids & retired_staging_ids

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
        if uid in deferred_ids:
            entry["fate"] = "retained_for_review"
        elif uid in genuinely_new_ids:
            entry["fate"] = "genuinely_new"
        elif uid in consumed_retries:
            entry["fate"] = "consumed_retry"
            entry["anchor"] = absorbed_to_anchor.get(uid, "")
        elif uid in absorbed_to_anchor:
            entry["fate"] = "merged_into_anchor"
            entry["anchor"] = absorbed_to_anchor[uid]
        else:
            entry["fate"] = "represented_in_new_group"
        fates.append(entry)

    merged_into_anchor_count = sum(1 for f in fates if f["fate"] == "merged_into_anchor")
    logger.info(
        "cross_run_property_fates",
        total=len(fates),
        genuinely_new=len(genuinely_new_ids),
        merged_into_anchor=merged_into_anchor_count,
        consumed_retry=len(consumed_retries),
        retained_for_review=len(deferred_ids),
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

    # T4: record dedup events
    if recorder is not None:
        for fate_entry in fates:
            fate = str(fate_entry["fate"])
            uid = str(fate_entry["id"])
            src = str(fate_entry["source"])
            if fate == "genuinely_new":
                recorder.record(PropertyEvent(uid, src, "dedup_passed", "dedup"))
            elif fate == "merged_into_anchor":
                recorder.record(
                    PropertyEvent(
                        uid,
                        src,
                        "duplicate_merged",
                        "dedup",
                        {"anchor_id": str(fate_entry.get("anchor", ""))},
                    )
                )
            elif fate == "consumed_retry":
                recorder.record(
                    PropertyEvent(
                        uid,
                        src,
                        "duplicate_merged",
                        "dedup",
                        {
                            "anchor_id": str(fate_entry.get("anchor", "")),
                            "reason": "consumed_retry",
                        },
                    )
                )
            elif fate == "retained_for_review":
                recorder.record(
                    PropertyEvent(uid, src, "dedup_deferred", "dedup", {"reason": fate})
                )
        await recorder.flush()

    return CrossRunDedupResult(genuinely_new=genuinely_new, anchors_updated=anchors_updated)


def _run_criteria_and_location_filters(
    properties: list[Property],
    criteria: SearchCriteria,
    search_areas: list[str],
    *,
    recorder: EventRecorder | None = None,
    progress: PreAnalysisResult | None = None,
) -> tuple[list[Property], int, int] | None:
    """Apply criteria and location filters.

    Returns None if nothing passes, or (filtered, criteria_count, location_count).
    """
    logger.info("pipeline_started", phase="criteria_filtering")
    criteria_filter = CriteriaFilter(criteria)
    filtered = criteria_filter.filter_properties(properties)
    criteria_count = len(filtered)
    if progress is not None:
        progress.criteria_filtered_count = criteria_count
    logger.info(
        "criteria_filter_summary", matched=len(filtered), by_source=_source_counts(filtered)
    )

    # T4: record criteria pass/drop events
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
        return None

    logger.info("pipeline_started", phase="location_filtering")
    location_filter = LocationFilter(search_areas, strict=False)
    pre_location = filtered
    filtered = location_filter.filter_properties(filtered)
    location_count = len(filtered)
    if progress is not None:
        progress.location_filtered_count = location_count
    logger.info(
        "location_filter_summary", matched=len(filtered), by_source=_source_counts(filtered)
    )

    # T4: record location pass/drop events
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
        return None

    return filtered, criteria_count, location_count


async def _load_unenriched(
    storage: PropertyStorage,
    settings: Settings,
) -> tuple[list[MergedProperty], set[str]]:
    """Load unenriched properties for retry, clearing stale image caches."""
    max_attempts = settings.max_enrichment_attempts
    unenriched = await storage.pipeline.get_unenriched_properties(max_attempts=max_attempts)
    unenriched = unenriched[: settings.max_analysis_per_run]
    re_enrichment_ids: set[str] = set()
    if unenriched:
        retry_stats = await storage.pipeline.get_unenriched_retry_stats(max_attempts=max_attempts)
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
    *,
    recorder: EventRecorder | None = None,
) -> tuple[list[MergedProperty], dict[str, tuple[int, TransportMode]], int]:
    """Annotate an advisory commute decision, retaining every input property.

    The count is measured journeys within the limit, not properties admitted or
    a monotonic funnel stage. Missing/error/unreachable results are unknown.
    Known over-limit measurements remain useful, but never remove a home.
    """
    results: list[CommuteResult] = []
    configured = bool(settings.traveltime_app_id and settings.traveltime_api_key)
    if configured:
        assert settings.traveltime_api_key is not None
        commute_filter = CommuteFilter(
            app_id=settings.traveltime_app_id,
            api_key=settings.traveltime_api_key.get_secret_value(),
            destination_postcode=criteria.destination_postcode,
        )
        merged = await commute_filter.geocode_properties(merged)
        for mode in criteria.transport_modes:
            mode_results = await commute_filter.filter_properties(
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
        logger.info("skipping_commute_filter", reason="not_configured")

    selected = best_measured_commutes(results)
    commute_lookup = commute_minutes_projection(results)
    within_count = sum(r.within_limit is True for r in selected.values())
    over_count = sum(r.within_limit is False for r in selected.values())
    unknown_count = len(merged) - len(selected)
    logger.info(
        "commute_filter_summary",
        policy="advisory",
        within_limit=within_count,
        over_limit=over_count,
        unknown=unknown_count,
        retained=len(merged),
        dropped=0,
        by_source=_source_counts(merged),
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

    return merged, commute_lookup, within_count


async def _run_enrichment(
    merged: list[MergedProperty],
    settings: Settings,
    storage: PropertyStorage,
    *,
    recorder: EventRecorder | None = None,
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
        await storage.pipeline.save_unenriched_property(failed)

    max_attempts = settings.max_enrichment_attempts
    expired = await storage.pipeline.expire_unenriched(max_attempts=max_attempts)

    logger.info(
        "enrichment_summary",
        enriched=len(enrichment_result.enriched),
        failed=len(enrichment_result.failed),
        expired=expired,
        with_floorplan=sum(1 for m in enriched if m.floorplan),
        with_images=sum(1 for m in enriched if m.images),
        by_source=_source_counts(enriched),
    )

    # T4: record enrichment events
    if recorder is not None:
        for m in enrichment_result.enriched:
            recorder.record(
                PropertyEvent(
                    m.canonical.unique_id,
                    m.canonical.source.value,
                    "enriched",
                    "enrichment",
                    {"gallery_count": len(m.images), "has_floorplan": bool(m.floorplan)},
                )
            )
        for m in enrichment_result.failed:
            recorder.record(
                PropertyEvent(
                    m.canonical.unique_id,
                    m.canonical.source.value,
                    "enrichment_failed",
                    "enrichment",
                    {"error": "enrichment_failure"},
                )
            )
        await recorder.flush()

    if not enriched:
        logger.info("no_enriched_properties")
        return None

    return enriched


async def _run_post_enrichment(
    merged: list[MergedProperty],
    storage: PropertyStorage,
    settings: Settings,
    re_enrichment_ids: set[str],
    *,
    recorder: EventRecorder | None = None,
    commute_lookup: dict[str, tuple[int, TransportMode]] | None = None,
    progress: PreAnalysisResult | None = None,
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
        cross_run_deduplicator,
        merged,
        storage,
        re_enrichment_ids,
        data_dir=settings.data_dir,
        recorder=recorder,
    )
    merged_to_notify = dedup_result.genuinely_new
    anchors_updated = dedup_result.anchors_updated
    if progress is not None:
        progress.anchors_updated = anchors_updated
        progress.post_dedup_count = len(merged_to_notify)
        progress.post_floorplan_count = len(merged_to_notify)

    if not merged_to_notify:
        logger.info("no_new_properties_after_cross_run_dedup")
        return None

    genuinely_new_count = len(merged_to_notify)

    if settings.require_floorplan:
        before_count = len(merged_to_notify)
        pre_floorplan = list(merged_to_notify)
        merged_to_notify = filter_by_floorplan(
            merged_to_notify,
            min_gallery_for_photo_inference=settings.min_gallery_for_photo_inference,
        )
        if progress is not None:
            progress.post_floorplan_count = len(merged_to_notify)
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

        # Compute passed/dropped sets (shared by save + T4 recorder)
        passed_ids = {m.unique_id for m in merged_to_notify}
        dropped = [m for m in pre_floorplan if m.unique_id not in passed_ids]

        # Save floorplan-dropped properties so they don't get re-enriched
        if dropped and commute_lookup is not None:
            await storage.pipeline.save_dropped_properties(dropped, commute_lookup)

        # T4: record floorplan gate events
        if recorder is not None:
            for m in pre_floorplan:
                uid = m.canonical.unique_id
                src = m.canonical.source.value
                if uid in passed_ids:
                    recorder.record(PropertyEvent(uid, src, "floorplan_passed", "floorplan"))
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
    recorder: EventRecorder | None = None,
    progress: PreAnalysisResult | None = None,
) -> PreAnalysisResult | None:
    """Run the pre-analysis pipeline: scrape, filter, deduplicate, enrich.

    Returns None if pipeline ends early (no properties at any stage).
    Quality analysis is handled separately by the caller for concurrency.
    """
    progress = progress if progress is not None else PreAnalysisResult()
    timings = progress.stage_timings
    active_phase = "scraping_seconds"

    t0 = time.monotonic()
    try:
        # Step 1: Scrape all platforms
        t0 = time.monotonic()
        scrape_result = await _run_scrape(
            settings,
            storage,
            max_per_scraper=max_per_scraper,
            only_scrapers=only_scrapers,
            full_scrape=full_scrape,
            metrics_out=progress.scraper_metrics,
        )
        timings["scraping_seconds"] = time.monotonic() - t0
        if scrape_result is None:
            all_properties: list[Property] = []
        else:
            all_properties, scraper_metrics = scrape_result
            progress.scraper_metrics = scraper_metrics
        progress.scraped_count = max(
            len(all_properties), sum(m.properties_found for m in progress.scraper_metrics)
        )

        # Step 1b: Record all scraped listings in source_listings
        sl_count = await storage.upsert_source_listings(all_properties)
        logger.info("source_listings_upserted", count=sl_count)

        # Step 2: Criteria + location filters
        active_phase = "filtering_seconds"
        t0 = time.monotonic()
        criteria = settings.get_search_criteria()
        search_areas = settings.get_search_areas()
        filter_result = _run_criteria_and_location_filters(
            all_properties, criteria, search_areas, recorder=recorder, progress=progress
        )
        if filter_result is None:
            filtered: list[Property] = []
            criteria_filtered_count = progress.criteria_filtered_count
            location_filtered_count = progress.location_filtered_count
        else:
            filtered, criteria_filtered_count, location_filtered_count = filter_result
        if recorder is not None:
            await recorder.flush()

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
        progress.new_property_count = new_property_count
        logger.info(
            "new_property_summary", new_count=len(new_merged), by_source=_source_counts(new_merged)
        )

        # T4: record new property filter events
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

        # Step 4: Load unenriched retries
        unenriched, re_enrichment_ids = await _load_unenriched(storage, settings)
        timings["filtering_seconds"] = time.monotonic() - t0
        if not new_merged and not unenriched:
            logger.info("no_new_properties")
            return None

        # Step 5: Enrichment
        active_phase = "enrichment_seconds"
        t0 = time.monotonic()
        merged_to_enrich = list(new_merged) + list(unenriched)
        enriched = await _run_enrichment(merged_to_enrich, settings, storage, recorder=recorder)
        if enriched is None:
            timings["enrichment_seconds"] = time.monotonic() - t0
            return None
        progress.enriched_count = len(enriched)

        # Step 6: Geocode + commute (after enrichment so all properties have full coords)
        geocoded, commute_lookup, commute_within_limit_count = await _geocode_and_compute_commute(
            enriched, criteria, settings, recorder=recorder
        )
        progress.commute_lookup = commute_lookup
        progress.commute_within_limit_count = commute_within_limit_count
        if not geocoded:
            logger.info("no_properties_after_enrichment")
            timings["enrichment_seconds"] = time.monotonic() - t0
            return None

        # Step 7: Post-enrichment dedup + floorplan gate
        # Use geocoded list (not enriched) so coordinates from geocoding are preserved
        post_result = await _run_post_enrichment(
            geocoded,
            storage,
            settings,
            re_enrichment_ids,
            recorder=recorder,
            commute_lookup=commute_lookup,
            progress=progress,
        )
        timings["enrichment_seconds"] = time.monotonic() - t0
        if post_result is None:
            return None

        final_merged, anchors_updated, post_dedup_count, post_floorplan_count = post_result
        progress.merged_to_process = final_merged
        progress.anchors_updated = anchors_updated
        progress.criteria_filtered_count = criteria_filtered_count
        progress.location_filtered_count = location_filtered_count
        progress.post_dedup_count = post_dedup_count
        progress.post_floorplan_count = post_floorplan_count
        return progress
    finally:
        timings[active_phase] = time.monotonic() - t0
        progress.scraped_count = max(
            progress.scraped_count, sum(m.properties_found for m in progress.scraper_metrics)
        )
