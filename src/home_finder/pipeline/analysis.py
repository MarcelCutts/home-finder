"""Pipeline analysis — quality analysis, reanalysis, and image management."""

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from itertools import zip_longest

import httpx

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters import PropertyQualityFilter
from home_finder.filters.quality import APIUnavailableError, TokenUsage
from home_finder.logging import get_logger
from home_finder.models import (
    MergedProperty,
    PropertyQualityAnalysis,
    TransportMode,
)
from home_finder.models.spend import SpendDeferred
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent
from home_finder.pipeline.runner import exclusive_runner
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


class _PropertyTimeoutError(Exception):
    """Raised when a single property's analysis exceeds its wall-clock timeout.

    Distinct from APIUnavailableError so it does NOT trigger the circuit breaker
    or cancel the batch — only the slow property is skipped.
    """

    def __init__(self, property_id: str) -> None:
        self.property_id = property_id
        super().__init__(f"Wall-clock timeout for property {property_id}")


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


class AnalysisDisposition(StrEnum):
    COMPLETED = "completed"
    DEFERRED = "deferred"
    FAILED = "failed"


_OnAnalysisResult = Callable[
    [MergedProperty, PropertyQualityAnalysis | None],
    Awaitable[AnalysisDisposition | None],
]


def _analysis_concurrency(settings: Settings) -> int:
    """Queue work behind local slots instead of rejecting it at spend admission."""
    policy = settings.analysis_spend_policy
    if policy is None:
        return settings.quality_concurrency
    return min(settings.quality_concurrency, policy.max_in_flight)


def _warn_if_spending_unconfigured(settings: Settings) -> None:
    if settings.analysis_spend_policy is None:
        logger.warning(
            "analysis_spending_not_configured",
            hint="Set HOME_FINDER_ANALYSIS_SPEND_POLICY; queued homes will remain unanalysed",
        )


_BEDROOM_SQM_RANGES: dict[int, tuple[float, float]] = {
    0: (12, 50),
    1: (20, 75),
    2: (35, 110),
    3: (50, 150),
}


async def _persist_estimated_floor_area(
    merged: MergedProperty,
    quality_analysis: PropertyQualityAnalysis | None,
    storage: PropertyStorage,
) -> None:
    """Persist Claude's floor area estimate if no scraped value exists."""
    if merged.floor_area_sqm is not None or quality_analysis is None:
        return
    space = quality_analysis.space
    if space and space.total_area_sqm and space.total_area_sqm > 0:
        sqm = space.total_area_sqm
        if 9.0 <= sqm <= 465.0:  # ~100-5000 sqft equivalent bounds
            # Bedroom plausibility check (warning only)
            bedrooms = merged.canonical.bedrooms
            expected = _BEDROOM_SQM_RANGES.get(min(bedrooms, 3), (35, 150))
            if not (expected[0] <= sqm <= expected[1]):
                logger.warning(
                    "floor_area_bedroom_mismatch",
                    sqm=sqm,
                    bedrooms=bedrooms,
                    expected_range=expected,
                    unique_id=merged.unique_id,
                )

            # Room areas may be partial or withheld. Only an excessive sum
            # contradicts the total; a smaller sum does not establish an error.
            if space.room_areas:
                room_sum = sum(r.area_sqm for r in space.room_areas if r.area_sqm)
                if room_sum - sqm > max(5.0, sqm * 0.15):
                    logger.warning(
                        "floor_area_sum_mismatch",
                        total=sqm,
                        room_sum=room_sum,
                        unique_id=merged.unique_id,
                    )

            await storage.update_floor_area(merged.unique_id, sqm, "estimated")


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
    await storage.pipeline.complete_analysis(merged.unique_id, quality_analysis)

    # Transition re-enriched properties into normal notification flow.
    # No-op for genuinely new properties (already handled by complete_analysis).
    await storage.pipeline.mark_enriched(merged.unique_id)


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
    per_property_timeout: float | None = None,
    on_error: Callable[[], None] | None = None,
    on_deferred: Callable[[], None] | None = None,
    on_timeout: Callable[[str], Awaitable[None]] | None = None,
    breaker_log_event: str = "api_circuit_breaker_activated",
    error_log_event: str = "analysis_failed",
) -> int:
    """Run quality analysis concurrently with circuit breaker protection.

    Executes analyze_fn for each item under a semaphore, using as_completed
    for streaming results. Provider failures stop queued work while already
    running requests finish accounting, without cancellation-created holds.

    Args:
        items: Properties to analyze.
        analyze_fn: Async function that analyzes a single property.
        on_result: Async callback invoked with (merged, analysis) for each success.
        per_property_timeout: Max wall-clock seconds per property (including SDK
            retries). None disables the timeout.
        on_error: Optional sync callback invoked on per-property errors.
        breaker_log_event: Log event name for circuit breaker activation.
        error_log_event: Log event name for per-property errors.

    Returns:
        Number of properties successfully processed.
    """
    semaphore = asyncio.Semaphore(concurrency)
    provider_unavailable = asyncio.Event()
    count = 0

    async def _bounded(
        merged: MergedProperty,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
        async with semaphore:
            if provider_unavailable.is_set():
                raise SpendDeferred("provider_unavailable")
            t0 = asyncio.get_running_loop().time()
            try:
                if per_property_timeout is not None:
                    result = await asyncio.wait_for(
                        analyze_fn(merged), timeout=per_property_timeout
                    )
                else:
                    result = await analyze_fn(merged)
                analysis = result[1]
                if analysis is not None and analysis.analysis_failure is not None:
                    if analysis.analysis_failure.kind == "unsupported_schema":
                        raise APIUnavailableError("unsupported_schema")
                    if analysis.analysis_failure.kind == "request_rejected":
                        # Generic 400s can describe account configuration or
                        # one listing's image. Preserve retry eligibility and
                        # let other homes run rather than starving the queue.
                        raise SpendDeferred("request_rejected")
                elapsed = asyncio.get_running_loop().time() - t0
                logger.info(
                    "property_analysis_duration",
                    property_id=merged.unique_id,
                    duration_s=round(elapsed, 1),
                )
                return result
            except APIUnavailableError:
                # Set before releasing the semaphore so no waiting property
                # starts while the result collector notices the failure.
                provider_unavailable.set()
                raise
            except TimeoutError:
                elapsed = asyncio.get_running_loop().time() - t0
                logger.warning(
                    "property_analysis_wall_clock_timeout",
                    property_id=merged.unique_id,
                    timeout_s=per_property_timeout,
                    duration_s=round(elapsed, 1),
                )
                raise _PropertyTimeoutError(merged.unique_id) from None

    tasks = [asyncio.create_task(_bounded(m)) for m in items]

    try:
        for coro in asyncio.as_completed(tasks):
            try:
                merged, quality_analysis = await coro
            except _PropertyTimeoutError as e:
                if on_timeout:
                    await on_timeout(e.property_id)
                logger.warning(
                    "property_skipped_wall_clock_timeout",
                    property_id=e.property_id,
                )
                if on_error:
                    on_error()
                continue
            except SpendDeferred as exc:
                logger.info("analysis_admission_deferred", reason=exc.reason)
                if on_deferred:
                    on_deferred()
                continue
            except APIUnavailableError:
                logger.warning(
                    breaker_log_event,
                    remaining_properties=sum(not task.done() for task in tasks),
                    processed=count,
                )
                if on_deferred:
                    on_deferred()
                continue
            except Exception:
                logger.error(error_log_event, exc_info=True)
                if on_error:
                    on_error()
                continue

            disposition = await on_result(merged, quality_analysis)
            if disposition in (None, AnalysisDisposition.COMPLETED):
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
    *,
    recorder: EventRecorder | None = None,
) -> tuple[int, TokenUsage | None]:
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
        Tuple of (number of properties processed, token usage or None).
    """
    # Save all properties to DB before analysis (crash recovery checkpoint)
    await storage.pipeline.save_pre_analysis_properties(pre.merged_to_process, pre.commute_lookup)

    # Reset properties with fallback analysis (API failed previously)
    # so they get re-analyzed this run
    use_quality = settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter
    reset_count = await storage.pipeline.reset_failed_analyses() if use_quality else 0
    if reset_count:
        logger.info("reset_failed_analyses_for_retry", count=reset_count)

    # Load any pending_analysis properties from previous crashed runs
    current_ids = {m.unique_id for m in pre.merged_to_process}
    pending_from_prev = await storage.pipeline.get_pending_analysis_properties(
        exclude_ids=current_ids
    )
    retried = list(pending_from_prev)
    if retried:
        logger.info("retrying_pending_analysis_from_previous_run", count=len(retried))

    # Alternate recovery and discovery, taking older pending work first. Fresh
    # arrivals cannot consume every slot while retries wait indefinitely.
    all_to_analyze = [
        item
        for pair in zip_longest(retried, pre.merged_to_process)
        for item in pair
        if item is not None
    ]

    max_per_run = settings.max_analysis_per_run
    if len(all_to_analyze) > max_per_run:
        logger.warning(
            "analysis_budget_cap_applied",
            total=len(all_to_analyze),
            cap=max_per_run,
            deferred=len(all_to_analyze) - max_per_run,
        )
        all_to_analyze = all_to_analyze[:max_per_run]

    if not all_to_analyze:
        return 0, None

    if use_quality:
        _warn_if_spending_unconfigured(settings)

    # Ward lookup and evidence recovery need no work on a completely empty drain.
    await _lookup_wards(storage)

    if not use_quality:
        logger.info("skipping_quality_analysis", reason="not_configured")
    else:
        retry_ids = {m.unique_id for m in retried}
        await _re_enrich_incomplete(
            [m for m in all_to_analyze if m.unique_id in retry_ids], settings, storage
        )

    async def _do_analysis(qf: PropertyQualityFilter | None) -> int:
        async def _analyze(
            merged: MergedProperty,
        ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
            if qf:
                try:
                    return await qf.analyze_single_merged(merged, data_dir=settings.data_dir)
                except SpendDeferred as exc:
                    if recorder is not None:
                        recorder.record(
                            PropertyEvent(
                                merged.unique_id,
                                merged.canonical.source.value,
                                "analysis_deferred",
                                "analysis",
                                {"reason": "spend_" + exc.reason},
                            )
                        )
                    raise
                except APIUnavailableError:
                    # Account/provider failure does not demonstrate a bad home.
                    raise
                except Exception:
                    await storage.pipeline.record_analysis_failure(merged.unique_id)
                    raise
            return merged, None

        async def _handle_result(
            merged: MergedProperty, quality_analysis: PropertyQualityAnalysis | None
        ) -> AnalysisDisposition:
            if qf is not None and (
                quality_analysis is None or quality_analysis.overall_rating is None
            ):
                reason = (
                    "missing_cached_images" if quality_analysis is None else "analysis_incomplete"
                )
                failure_meta = (
                    quality_analysis.analysis_failure.model_dump(mode="json")
                    if quality_analysis is not None
                    and quality_analysis.analysis_failure is not None
                    else None
                )
                if quality_analysis is not None:
                    await storage.pipeline.record_analysis_failure(merged.unique_id)
                logger.info(
                    "analysis_deferred",
                    property_id=merged.unique_id,
                    reason=reason,
                    failure=failure_meta,
                )
                if recorder is not None:
                    recorder.record(
                        PropertyEvent(
                            merged.unique_id,
                            merged.canonical.source.value,
                            "analysis_deferred",
                            "analysis",
                            {
                                "reason": reason,
                                **({"failure": failure_meta} if failure_meta else {}),
                            },
                        )
                    )
                return (
                    AnalysisDisposition.DEFERRED
                    if quality_analysis is None
                    else AnalysisDisposition.FAILED
                )
            commute_info = pre.commute_lookup.get(merged.canonical.unique_id)
            await _save_one(merged, commute_info, quality_analysis, storage)
            await _persist_estimated_floor_area(merged, quality_analysis, storage)
            await on_result(merged, commute_info, quality_analysis)

            # T4: record analysis event
            if recorder is not None:
                meta: dict[str, object]
                if quality_analysis and quality_analysis.overall_rating is not None:
                    meta = {"rating": quality_analysis.overall_rating}
                else:
                    meta = {"skipped": True}
                recorder.record(
                    PropertyEvent(
                        merged.canonical.unique_id,
                        merged.canonical.source.value,
                        "analyzed",
                        "analysis",
                        meta,
                    )
                )
            return AnalysisDisposition.COMPLETED

        return await _run_concurrent_analysis(
            all_to_analyze,
            _analyze,
            _handle_result,
            concurrency=_analysis_concurrency(settings),
            per_property_timeout=settings.property_analysis_timeout,
            breaker_log_event="api_circuit_breaker_activated",
            error_log_event="property_processing_failed",
            on_timeout=storage.pipeline.record_analysis_failure if qf is not None else None,
        )

    token_usage: TokenUsage | None = None
    if use_quality:
        async with PropertyQualityFilter(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
            analysis_config=settings.analysis_config,
            spend_repository=storage.spend,
            spend_policy=settings.analysis_spend_policy,
        ) as quality_filter:
            count = await _do_analysis(quality_filter)
            token_usage = quality_filter.token_usage
    else:
        count = await _do_analysis(None)

    if recorder is not None:
        await recorder.flush()

    return count, token_usage


async def _download_missing_images(
    merged: MergedProperty,
    detail_fetcher: DetailFetcher,
    data_dir: str,
    max_images: int,
) -> int:
    """Download gallery images and the floorplan missing from disk cache.

    Uses URLs already on the MergedProperty (loaded from DB by the reanalysis
    queue) — no detail-page fetching required.

    Returns:
        Number of images successfully downloaded.
    """
    has_fp = merged.floorplan is not None and is_valid_image_url(str(merged.floorplan.url))
    effective_max = max_images - (1 if has_fp else 0)
    gallery = [img for img in merged.images if img.image_type == "gallery"]
    to_check = gallery[:effective_max]
    if has_fp and merged.floorplan is not None:
        to_check.append(merged.floorplan)

    downloaded = 0
    for idx, img in enumerate(to_check):
        url_str = str(img.url)
        image_type = img.image_type
        if find_cached_file(data_dir, merged.unique_id, url_str, image_type):
            continue
        img_bytes = await detail_fetcher.download_image_bytes(url_str)
        if img_bytes:
            path = get_cached_image_path(data_dir, merged.unique_id, url_str, image_type, idx)
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

    Checks each property's gallery images and floorplan against the disk cache. For any
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
        gallery_urls = [str(img.url) for img in merged.images if img.image_type == "gallery"]
        has_fp = merged.floorplan is not None and is_valid_image_url(str(merged.floorplan.url))
        cached, expected = gallery_cache_coverage(
            settings.data_dir,
            merged.unique_id,
            gallery_urls,
            has_valid_floorplan=has_fp,
            max_images=settings.quality_filter_max_images,
        )
        missing_floorplan = (
            has_fp
            and merged.floorplan is not None
            and find_cached_file(
                settings.data_dir, merged.unique_id, str(merged.floorplan.url), "floorplan"
            )
            is None
        )
        if cached < expected or missing_floorplan:
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

    queue = (await storage.pipeline.get_reanalysis_queue())[: settings.max_analysis_per_run]
    if not queue:
        return 0

    _warn_if_spending_unconfigured(settings)
    queue = await _re_enrich_incomplete(queue, settings, storage)

    logger.info("reanalysis_queue_drain_started", count=len(queue))

    completed = 0

    async with PropertyQualityFilter(
        api_key=settings.anthropic_api_key.get_secret_value(),
        max_images=settings.quality_filter_max_images,
        enable_extended_thinking=settings.enable_extended_thinking,
        analysis_config=settings.analysis_config,
        spend_repository=storage.spend,
        spend_policy=settings.analysis_spend_policy,
    ) as quality_filter:

        async def _analyze(
            merged: MergedProperty,
        ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
            return await quality_filter.analyze_single_merged(merged, data_dir=settings.data_dir)

        async def _handle_result(
            merged: MergedProperty,
            quality_analysis: PropertyQualityAnalysis | None,
        ) -> AnalysisDisposition:
            nonlocal completed
            if quality_analysis is not None and quality_analysis.overall_rating is not None:
                await storage.pipeline.complete_reanalysis(merged.unique_id, quality_analysis)
                await _persist_estimated_floor_area(merged, quality_analysis, storage)
                completed += 1
                return AnalysisDisposition.COMPLETED
            logger.info(
                "reanalysis_deferred",
                property_id=merged.unique_id,
                reason="missing_cached_images"
                if quality_analysis is None
                else "analysis_incomplete",
            )
            return (
                AnalysisDisposition.DEFERRED
                if quality_analysis is None
                else AnalysisDisposition.FAILED
            )

        await _run_concurrent_analysis(
            queue,
            _analyze,
            _handle_result,
            concurrency=_analysis_concurrency(settings),
            per_property_timeout=settings.property_analysis_timeout,
            breaker_log_event="reanalysis_api_circuit_breaker",
            error_log_event="reanalysis_failed",
        )

    logger.info("reanalysis_queue_drain_complete", reanalyzed=completed, queued=len(queue))
    return completed


@exclusive_runner
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
            flagged = await storage.pipeline.request_reanalysis_by_filter(
                outcodes=outcodes, all_properties=reanalyze_all
            )
            target = "all properties" if reanalyze_all else f"outcodes {', '.join(outcodes or [])}"
            logger.info("reanalysis_properties_flagged", flagged=flagged, target=target)

        if request_only:
            return

        # Step 2: Load queue and re-enrich incomplete caches
        queue = (await storage.pipeline.get_reanalysis_queue())[: settings.max_analysis_per_run]

        if not queue:
            logger.info("reanalysis_queue_empty")
            return

        # Check configuration before any evidence download.
        use_quality = (
            settings.anthropic_api_key.get_secret_value() and settings.enable_quality_filter
        )
        if not use_quality:
            logger.error("quality_analysis_not_configured", hint="need ANTHROPIC_API_KEY")
            return

        _warn_if_spending_unconfigured(settings)
        queue = await _re_enrich_incomplete(queue, settings, storage)
        logger.info("reanalysis_started", count=len(queue))

        completed = 0
        failed = 0
        deferred = 0

        async with PropertyQualityFilter(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
            analysis_config=settings.analysis_config,
            spend_repository=storage.spend,
            spend_policy=settings.analysis_spend_policy,
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
            ) -> AnalysisDisposition:
                nonlocal completed, failed, deferred
                if quality_analysis is not None and quality_analysis.overall_rating is not None:
                    await storage.pipeline.complete_reanalysis(merged.unique_id, quality_analysis)
                    await _persist_estimated_floor_area(merged, quality_analysis, storage)
                    completed += 1
                    return AnalysisDisposition.COMPLETED
                elif quality_analysis is None:
                    deferred += 1
                    return AnalysisDisposition.DEFERRED
                else:
                    failed += 1
                    return AnalysisDisposition.FAILED

            def _count_error() -> None:
                nonlocal failed
                failed += 1

            def _count_deferred() -> None:
                nonlocal deferred
                deferred += 1

            await _run_concurrent_analysis(
                queue,
                _analyze,
                _handle_result,
                concurrency=_analysis_concurrency(settings),
                per_property_timeout=settings.property_analysis_timeout,
                on_error=_count_error,
                on_deferred=_count_deferred,
                breaker_log_event="reanalysis_api_circuit_breaker",
                error_log_event="reanalysis_failed",
            )

        logger.info("reanalysis_complete", completed=completed, failed=failed, deferred=deferred)
