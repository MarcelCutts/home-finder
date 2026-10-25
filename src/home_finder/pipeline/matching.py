"""Coordinate listing matching and durable consolidation into stored anchors."""

from home_finder.db import PropertyStorage
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.logging import get_logger
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import PropertyAggregate
from home_finder.pipeline.collection import count_by_source
from home_finder.pipeline.contracts import ConsolidationResult
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent
from home_finder.utils.image_cache import (
    copy_cached_images,
)

logger = get_logger(__name__)


async def consolidate_candidates(
    deduplicator: Deduplicator,
    analysis_candidates: list[PropertyAggregate],
    storage: PropertyStorage,
    re_enrichment_ids: set[str],
    *,
    data_dir: str = "",
    recorder: EventRecorder | None = None,
) -> ConsolidationResult:
    """Deduplicate new properties against recent DB anchors (cross-run detection).

    Loads recent DB properties as anchors so a property appearing on platform B
    today can be matched against platform A stored last week. Updates anchors
    that gained new sources and cleans up consumed retry rows.

    When a cross-run merge adds a new source to an anchor, the anchor is flagged
    for quality re-analysis (new images/descriptions warrant a fresh look) and
    cached images are copied from the new source to the anchor's cache directory.

    Args:
        deduplicator: Deduplicator instance for cross-platform matching.
        analysis_candidates: New/re-enriched properties to check.
        storage: Database storage for anchor lookup and updates.
        re_enrichment_ids: IDs of properties being re-enriched (for cleanup).
        data_dir: Base data directory for image cache operations.

    Returns:
        ConsolidationResult with unmatched properties and update count.
    """
    # Consolidation rebuilds projections from source rows. Retain these
    # inputs even when collection has not already persisted them.
    await storage.upsert_source_listings([mp.canonical for mp in analysis_candidates])

    db_anchors = await storage.get_recent_properties_for_dedup(days=30)
    recovering_input_ids = {merged.unique_id for merged in analysis_candidates} & re_enrichment_ids
    # A recovering anchor is the current input, not a second copy of itself.
    # Unselected recovery anchors still retain their identity in dedup lookup.
    db_anchors = [anchor for anchor in db_anchors if anchor.unique_id not in recovering_input_ids]
    logger.info("loaded_dedup_anchors", anchor_count=len(db_anchors))

    # Map each anchor's source URLs to its DB unique_id so we can detect
    # merges even when the deduplicator picks a different canonical.
    anchor_url_to_ids: dict[str, set[str]] = {}
    anchor_by_id: dict[str, PropertyAggregate] = {}
    for anchor in db_anchors:
        anchor_by_id[anchor.canonical.unique_id] = anchor
        for url in anchor.source_urls.values():
            anchor_url_to_ids.setdefault(str(url), set()).add(anchor.canonical.unique_id)

    # Map source URLs from new properties to their original unique_id so we
    # can copy cached images from the new source to the anchor after merge.
    new_url_to_unique_id: dict[str, str] = {}
    new_id_to_price: dict[str, int] = {}
    for mp in analysis_candidates:
        for url in mp.source_urls.values():
            new_url_to_unique_id[str(url)] = mp.canonical.unique_id
        new_id_to_price[mp.canonical.unique_id] = mp.canonical.price_pcm

    # Combine new properties with DB anchors for dedup comparison
    combined_for_dedup = analysis_candidates + db_anchors
    dedup_results = await deduplicator.deduplicate_merged_async(combined_for_dedup)

    # Split: anchors that gained new sources vs unmatched properties
    unmatched_candidates: list[PropertyAggregate] = []
    anchors_updated = 0
    seen_anchor_ids: set[str] = set()
    absorbed_to_anchor: dict[str, str] = {}  # new_id -> anchor_id
    retired_staging_ids: set[str] = set()
    represented_new_ids: set[str] = set()
    deferred_ids: set[str] = set()
    new_by_id = {mp.unique_id: mp for mp in analysis_candidates}
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

            # Refresh cached media and compare prices when another platform joins.
            truly_new_sources = set(merged.sources) - set(original_anchor.sources)
            if truly_new_sources:
                # Detect cross-platform price drops: if a new source lists at
                # a lower price than the anchor's DB price, record the change.
                # Observation-time price checks use source listing IDs, so a
                # newly matched platform needs this comparison against
                # the stored anchor.
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
            unmatched_candidates.append(merged)
            represented_new_ids.update(candidate_ids)

    # A refused or ambiguous group remains independently processable. Never infer
    # deletion from absence in a deduplicator result (including a missing retry).
    unrepresented_ids = new_by_id.keys() - represented_new_ids
    unmatched_candidates.extend(new_by_id[uid] for uid in sorted(unrepresented_ids))
    deferred_ids.update(unrepresented_ids)
    genuinely_new_ids = {m.unique_id for m in unmatched_candidates}
    consumed_retries = re_enrichment_ids & retired_staging_ids

    # Build per-property fates for observability
    fates: list[dict[str, str | int]] = []
    for mp in analysis_candidates:
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
            entry["fate"] = "unmatched_candidates"
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
        unmatched_candidates=len(genuinely_new_ids),
        merged_into_anchor=merged_into_anchor_count,
        consumed_retry=len(consumed_retries),
        retained_for_review=len(deferred_ids),
        fates=fates,
    )

    logger.info(
        "deduplication_merge_summary",
        dedup_input=len(combined_for_dedup),
        dedup_output=len(dedup_results),
        unmatched_candidates=len(unmatched_candidates),
        anchors_updated=anchors_updated,
        consumed_retries=len(consumed_retries),
        multi_source_count=sum(1 for m in unmatched_candidates if len(m.sources) > 1),
        by_source=count_by_source(unmatched_candidates),
    )

    # record dedup events
    if recorder is not None:
        for fate_entry in fates:
            fate = str(fate_entry["fate"])
            uid = str(fate_entry["id"])
            src = str(fate_entry["source"])
            if fate == "unmatched_candidates":
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

    return ConsolidationResult(
        unmatched_candidates=unmatched_candidates, anchors_updated=anchors_updated
    )
