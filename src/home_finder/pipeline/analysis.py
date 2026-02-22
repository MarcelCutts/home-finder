"""Pipeline analysis — quality analysis, reanalysis, and image management."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters import PropertyQualityFilter
from home_finder.filters.quality import APIUnavailableError
from home_finder.logging import get_logger
from home_finder.models import (
    SQM_PER_SQFT,
    MergedProperty,
    PropertyQualityAnalysis,
    TransportMode,
)
from home_finder.pipeline.stages import PreAnalysisResult
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.utils.image_cache import (
    find_cached_file,
    gallery_cache_coverage,
    get_cached_image_path,
    is_valid_image_url,
    save_image_bytes,
)

logger = get_logger(__name__)


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


async def _persist_estimated_floor_area(
    merged: MergedProperty,
    quality_analysis: PropertyQualityAnalysis | None,
    storage: PropertyStorage,
) -> None:
    """Persist Claude's floor area estimate if no scraped value exists."""
    if merged.floor_area_sqft is not None or quality_analysis is None:
        return
    space = quality_analysis.space
    if space and space.total_area_sqm and space.total_area_sqm > 0:
        sqft = round(space.total_area_sqm / SQM_PER_SQFT)
        if 100 <= sqft <= 5000:
            await storage.update_floor_area(merged.unique_id, sqft, "estimated")


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
    concurrency: int = 15,
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
    semaphore = asyncio.Semaphore(concurrency)
    count = 0

    async def _bounded(
        merged: MergedProperty,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
        async with semaphore:
            return await analyze_fn(merged)

    tasks = [asyncio.create_task(_bounded(m)) for m in items]

    try:
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
    finally:
        # Ensure all child tasks are cleaned up on cancellation or any exit
        remaining = [t for t in tasks if not t.done()]
        if remaining:
            for t in remaining:
                t.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)

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

    max_per_run = settings.max_analysis_per_run
    if len(all_to_analyze) > max_per_run:
        logger.warning(
            "analysis_budget_cap_applied",
            total=len(all_to_analyze),
            cap=max_per_run,
            deferred=len(all_to_analyze) - max_per_run,
        )
        all_to_analyze = all_to_analyze[:max_per_run]

    use_quality = settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter
    if not use_quality:
        logger.info("skipping_quality_analysis", reason="not_configured")

    async def _do_analysis(qf: PropertyQualityFilter | None) -> int:
        async def _analyze(
            merged: MergedProperty,
        ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
            if qf:
                return await qf.analyze_single_merged(merged, data_dir=settings.data_dir)
            return merged, None

        async def _handle_result(
            merged: MergedProperty, quality_analysis: PropertyQualityAnalysis | None
        ) -> None:
            commute_info = pre.commute_lookup.get(merged.canonical.unique_id)
            await _save_one(merged, commute_info, quality_analysis, storage)
            await _persist_estimated_floor_area(merged, quality_analysis, storage)
            await on_result(merged, commute_info, quality_analysis)

        return await _run_concurrent_analysis(
            all_to_analyze,
            _analyze,
            _handle_result,
            concurrency=settings.quality_concurrency,
            breaker_log_event="api_circuit_breaker_activated",
            error_log_event="property_processing_failed",
        )

    if use_quality:
        async with PropertyQualityFilter(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
        ) as quality_filter:
            count = await _do_analysis(quality_filter)
    else:
        count = await _do_analysis(None)

    return count


async def _download_missing_images(
    merged: MergedProperty,
    detail_fetcher: DetailFetcher,
    data_dir: str,
    max_images: int,
) -> int:
    """Download gallery images that are in the DB but missing from disk cache.

    Uses URLs already on the MergedProperty (loaded from DB by the reanalysis
    queue) — no detail-page fetching required.

    Returns:
        Number of images successfully downloaded.
    """
    has_fp = merged.floorplan is not None and is_valid_image_url(
        str(merged.floorplan.url)
    )
    effective_max = max_images - (1 if has_fp else 0)
    gallery = [img for img in merged.images if img.image_type == "gallery"]
    to_check = gallery[:effective_max]

    downloaded = 0
    for idx, img in enumerate(to_check):
        url_str = str(img.url)
        if find_cached_file(data_dir, merged.unique_id, url_str, "gallery"):
            continue
        img_bytes = await detail_fetcher.download_image_bytes(url_str)
        if img_bytes:
            path = get_cached_image_path(
                data_dir, merged.unique_id, url_str, "gallery", idx
            )
            save_image_bytes(path, img_bytes)
            downloaded += 1
        else:
            logger.warning(
                "missing_image_download_failed",
                property_id=merged.unique_id,
                url=url_str,
            )
    return downloaded


async def _re_enrich_incomplete(
    queue: list[MergedProperty],
    settings: Settings,
    storage: PropertyStorage,
) -> list[MergedProperty]:
    """Download missing cached images for reanalysis queue properties.

    Checks each property's gallery images against the disk cache. For any
    with gaps, downloads the missing files directly from the image URLs
    already stored on the MergedProperty (no detail-page re-scraping).

    Returns the queue unchanged — the same MergedProperty objects, but now
    with more complete disk caches so the analysis gate will accept them.
    """
    if not settings.data_dir:
        return queue

    # Find properties with incomplete caches
    incomplete: list[MergedProperty] = []
    for merged in queue:
        gallery_urls = [
            str(img.url) for img in merged.images if img.image_type == "gallery"
        ]
        has_fp = merged.floorplan is not None and is_valid_image_url(
            str(merged.floorplan.url)
        )
        cached, expected = gallery_cache_coverage(
            settings.data_dir,
            merged.unique_id,
            gallery_urls,
            has_valid_floorplan=has_fp,
            max_images=settings.quality_filter_max_images,
        )
        if cached < expected:
            incomplete.append(merged)

    if not incomplete:
        return queue

    logger.info("downloading_missing_cache_images", count=len(incomplete))

    async with DetailFetcher(
        max_gallery_images=settings.quality_filter_max_images,
        proxy_url=settings.proxy_url,
    ) as detail_fetcher:
        total_downloaded = 0
        for merged in incomplete:
            downloaded = await _download_missing_images(
                merged,
                detail_fetcher,
                settings.data_dir,
                settings.quality_filter_max_images,
            )
            total_downloaded += downloaded

    logger.info(
        "missing_image_downloads_complete",
        properties=len(incomplete),
        images_downloaded=total_downloaded,
    )

    return queue


async def _drain_reanalysis_queue(
    settings: Settings,
    storage: PropertyStorage,
) -> int:
    """Process any pending re-analyses (e.g. from cross-run merges).

    Uses the same concurrent analysis pattern as the main pipeline.
    Saves via complete_reanalysis() which preserves notification_status='sent'.

    Args:
        settings: Application settings.
        storage: Database storage.

    Returns:
        Number of properties re-analyzed.
    """
    if not settings.enable_quality_filter or not settings.anthropic_api_key.get_secret_value():
        return 0

    queue = await storage.get_reanalysis_queue()
    if not queue:
        return 0

    queue = await _re_enrich_incomplete(queue, settings, storage)

    logger.info("reanalysis_queue_drain_started", count=len(queue))

    completed = 0

    async with PropertyQualityFilter(
        api_key=settings.anthropic_api_key.get_secret_value(),
        max_images=settings.quality_filter_max_images,
        enable_extended_thinking=settings.enable_extended_thinking,
    ) as quality_filter:
        async def _analyze(
            merged: MergedProperty,
        ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
            return await quality_filter.analyze_single_merged(
                merged, data_dir=settings.data_dir
            )

        async def _handle_result(
            merged: MergedProperty,
            quality_analysis: PropertyQualityAnalysis | None,
        ) -> None:
            nonlocal completed
            if quality_analysis:
                await storage.complete_reanalysis(merged.unique_id, quality_analysis)
                await _persist_estimated_floor_area(merged, quality_analysis, storage)
                completed += 1

        await _run_concurrent_analysis(
            queue,
            _analyze,
            _handle_result,
            concurrency=settings.quality_concurrency,
            breaker_log_event="reanalysis_api_circuit_breaker",
            error_log_event="reanalysis_failed",
        )

    logger.info("reanalysis_queue_drain_complete", reanalyzed=completed, queued=len(queue))
    return completed


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
    async with PropertyStorage(settings.database_path) as storage:
        # Step 1: Flag properties if outcodes or --all provided
        if outcodes or reanalyze_all:
            flagged = await storage.request_reanalysis_by_filter(
                outcodes=outcodes, all_properties=reanalyze_all
            )
            target = "all properties" if reanalyze_all else f"outcodes {', '.join(outcodes or [])}"
            logger.info("reanalysis_properties_flagged", flagged=flagged, target=target)

        if request_only:
            return

        # Step 2: Load queue and re-enrich incomplete caches
        queue = await storage.get_reanalysis_queue()

        if not queue:
            logger.info("reanalysis_queue_empty")
            return

        queue = await _re_enrich_incomplete(queue, settings, storage)

        # Step 3: Cost estimate
        est_cost = len(queue) * 0.06
        logger.info("reanalysis_started", count=len(queue), est_cost=f"${est_cost:.2f}")

        # Step 4: Run quality analysis
        use_quality = (
            settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter
        )
        if not use_quality:
            logger.error("quality_analysis_not_configured", hint="need ANTHROPIC_API_KEY")
            return

        completed = 0
        failed = 0

        async with PropertyQualityFilter(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
        ) as quality_filter:
            async def _analyze(
                merged: MergedProperty,
            ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
                return await quality_filter.analyze_single_merged(
                    merged, data_dir=settings.data_dir
                )

            async def _handle_result(
                merged: MergedProperty,
                quality_analysis: PropertyQualityAnalysis | None,
            ) -> None:
                nonlocal completed, failed
                if quality_analysis:
                    await storage.complete_reanalysis(merged.unique_id, quality_analysis)
                    await _persist_estimated_floor_area(merged, quality_analysis, storage)
                    completed += 1
                else:
                    failed += 1

            def _count_error() -> None:
                nonlocal failed
                failed += 1

            await _run_concurrent_analysis(
                queue,
                _analyze,
                _handle_result,
                concurrency=settings.quality_concurrency,
                on_error=_count_error,
                breaker_log_event="reanalysis_api_circuit_breaker",
                error_log_event="reanalysis_failed",
            )

        logger.info("reanalysis_complete", completed=completed, failed=failed)
