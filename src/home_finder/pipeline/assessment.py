"""Initial assessment admission, execution, and durable completion."""

import asyncio
from collections.abc import Awaitable, Callable
from itertools import zip_longest

import httpx

from home_finder.analysis.analyzer import APIUnavailableError, PropertyQualityAnalyzer
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import get_logger
from home_finder.models import (
    PropertyAggregate,
    PropertyQualityAnalysis,
    TransportMode,
)
from home_finder.models.notifications import NotificationPolicy
from home_finder.models.spend import SpendDeferred
from home_finder.pipeline import image_recovery
from home_finder.pipeline.cache_recovery import repair_merged_image_caches
from home_finder.pipeline.contracts import (
    AnalysisDisposition,
    AnalysisOutcome,
    CandidateBatch,
    InitialAssessmentSummary,
)
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent

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
CommuteInfo = tuple[int, TransportMode] | None
AssessmentCallback = Callable[
    [PropertyAggregate, CommuteInfo, PropertyQualityAnalysis | None],
    Awaitable[None],
]
_AnalyzeFn = Callable[
    [PropertyAggregate],
    Awaitable[AnalysisOutcome],
]


class _AnalysisExhausted(Exception):
    """An operation retry ceiling needs review, rather than another scheduled attempt."""

    def __init__(self, property_id: str) -> None:
        self.property_id = property_id
        super().__init__("paid_operation_attempt_limit")


_OnAnalysisResult = Callable[[AnalysisOutcome], Awaitable[None]]


async def process_initial_assessments(
    batch: CandidateBatch,
    settings: Settings,
    storage: PropertyStorage,
    on_result: AssessmentCallback,
    *,
    recorder: EventRecorder | None = None,
) -> InitialAssessmentSummary:
    """Run quality analysis, save each property, and invoke callback.

    Properties are saved to DB *before* analysis starts (with notification_status
    'pending_analysis'). If the process crashes mid-analysis, the next run picks
    them up via get_pending_analysis_properties().

    Args:
        batch: Candidates prepared for initial assessment.
        settings: Application settings.
        storage: Database storage.
        on_result: Callback invoked after assessment persistence, including skipped analysis.

    Returns:
        Counts of assessed, skipped, deferred, and failed work, plus returned usage.
    """
    summary = InitialAssessmentSummary()
    # Save all properties to DB before analysis (crash recovery checkpoint)
    proposed_ids = {m.unique_id for m in batch.candidates}
    persisted = await storage.processing.save_pre_analysis_properties(
        batch.candidates, batch.commute_lookup
    )
    if recorder is not None:
        for merged in batch.candidates:
            if merged.unique_id in persisted.refused:
                recorder.record(
                    PropertyEvent(
                        merged.unique_id,
                        merged.canonical.source.value,
                        "persistence_refused",
                        "analysis",
                        {"reason": "merge_review", "blockers": persisted.refused[merged.unique_id]},
                    )
                )
        await recorder.flush()
    accepted_candidates = persisted.accepted

    # Reset properties with fallback analysis (API failed previously)
    # so they become eligible for another initial assessment attempt.
    analysis_enabled = settings.enable_quality_filter and bool(
        settings.anthropic_api_key.get_secret_value()
    )
    reset_count = await storage.processing.reset_failed_analyses() if analysis_enabled else 0
    if reset_count:
        logger.info("reset_failed_analyses_for_retry", count=reset_count)

    # Load pending initial assessments from interrupted or deferred work.
    pending_from_prev = await storage.processing.get_pending_analysis_properties(
        exclude_ids=proposed_ids
    )
    retried = list(pending_from_prev)
    if retried:
        logger.info("retrying_pending_analysis_from_previous_run", count=len(retried))

    # Alternate recovery and discovery, taking older pending work first. Fresh
    # arrivals cannot consume every slot while retries wait indefinitely.
    all_to_analyze = [
        item
        for pair in zip_longest(retried, accepted_candidates)
        for item in pair
        if item is not None
    ]

    max_per_run = settings.max_initial_analyses_per_run
    if len(all_to_analyze) > max_per_run:
        logger.warning(
            "analysis_budget_cap_applied",
            total=len(all_to_analyze),
            cap=max_per_run,
            deferred=len(all_to_analyze) - max_per_run,
        )
        summary.deferred += len(all_to_analyze) - max_per_run
        all_to_analyze = all_to_analyze[:max_per_run]

    if not all_to_analyze:
        return summary

    await repair_merged_image_caches(
        storage, settings.data_dir, [m.unique_id for m in all_to_analyze]
    )
    if analysis_enabled:
        warn_if_spending_unconfigured(settings)

    # Ward lookup and evidence recovery need no work on a completely empty drain.
    await _lookup_wards(storage)

    if not analysis_enabled:
        logger.info("skipping_quality_analysis", reason="not_configured")
    else:
        retry_ids = {m.unique_id for m in retried}
        await image_recovery.restore_missing_analysis_images(
            [m for m in all_to_analyze if m.unique_id in retry_ids], settings, storage
        )

    async def _process_admitted_batch(analyzer: PropertyQualityAnalyzer | None) -> int:
        async def _analyze(
            merged: PropertyAggregate,
        ) -> AnalysisOutcome:
            if analyzer:
                try:
                    return await analyze_with_spend_disposition(analyzer, merged, settings, storage)
                except _AnalysisExhausted:
                    if recorder is not None:
                        recorder.record(
                            PropertyEvent(
                                merged.unique_id,
                                merged.canonical.source.value,
                                "analysis_exhausted",
                                "analysis",
                                {"reason": "paid_operation_attempt_limit"},
                            )
                        )
                    raise
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
                    await storage.processing.record_analysis_failure(merged.unique_id)
                    raise
            return AnalysisOutcome(merged, AnalysisDisposition.SKIPPED, reason="not_configured")

        async def _handle_result(outcome: AnalysisOutcome) -> None:
            merged, quality_analysis = outcome.candidate, outcome.assessment
            if outcome.disposition in (AnalysisDisposition.DEFERRED, AnalysisDisposition.FAILED):
                reason = outcome.reason
                failure_meta = (
                    quality_analysis.analysis_failure.model_dump(mode="json")
                    if quality_analysis is not None
                    and quality_analysis.analysis_failure is not None
                    else None
                )
                exhausted = False
                if quality_analysis is not None:
                    exhausted = await storage.processing.record_analysis_failure(merged.unique_id)
                event = "analysis_exhausted" if exhausted else "analysis_deferred"
                if exhausted:
                    reason = "initial_failure_limit"
                logger.info(
                    event,
                    property_id=merged.unique_id,
                    reason=reason,
                    failure=failure_meta,
                )
                if recorder is not None:
                    recorder.record(
                        PropertyEvent(
                            merged.unique_id,
                            merged.canonical.source.value,
                            event,
                            "analysis",
                            {
                                "reason": reason,
                                **({"failure": failure_meta} if failure_meta else {}),
                            },
                        )
                    )
                if outcome.disposition == AnalysisDisposition.DEFERRED:
                    summary.deferred += 1
                else:
                    summary.failed += 1
                return
            commute_info = batch.commute_lookup.get(merged.canonical.unique_id)
            await complete_initial_assessment(
                merged,
                quality_analysis,
                storage,
                notification_policy=settings.notification_policy,
            )
            await persist_estimated_floor_area(merged, quality_analysis, storage)
            # record analysis event
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
                        "assessment_completed"
                        if quality_analysis is not None
                        else "analysis_skipped",
                        "analysis",
                        meta,
                    )
                )
            await on_result(merged, commute_info, quality_analysis)
            if quality_analysis is None:
                summary.skipped += 1
            else:
                summary.assessed += 1

        async def _record_timeout(property_id: str) -> None:
            await storage.processing.record_analysis_failure(property_id)

        def count_failure() -> None:
            summary.failed += 1

        def count_deferral() -> None:
            summary.deferred += 1

        return await run_concurrent_analysis(
            all_to_analyze,
            _analyze,
            _handle_result,
            concurrency=analysis_concurrency(settings),
            per_property_timeout=settings.property_analysis_timeout,
            breaker_log_event="api_circuit_breaker_activated",
            error_log_event="property_processing_failed",
            on_timeout=_record_timeout if analyzer is not None else None,
            on_error=count_failure,
            on_deferred=count_deferral,
        )

    if analysis_enabled:
        async with create_quality_analyzer(settings, storage) as analyzer:
            await _process_admitted_batch(analyzer)
            summary.token_usage = analyzer.token_usage
    else:
        await _process_admitted_batch(None)

    if recorder is not None:
        await recorder.flush()

    return summary


def create_quality_analyzer(
    settings: Settings, storage: PropertyStorage
) -> PropertyQualityAnalyzer:
    """Use one provider configuration for initial assessments and reassessment."""
    return PropertyQualityAnalyzer(
        api_key=settings.anthropic_api_key.get_secret_value(),
        max_images=settings.quality_filter_max_images,
        enable_extended_thinking=settings.enable_extended_thinking,
        analysis_config=settings.analysis_config,
        spend_repository=storage.spend,
        spend_policy=settings.analysis_spend_policy,
    )


async def run_concurrent_analysis(
    items: list[PropertyAggregate],
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
        on_result: Persist an explicit assessed, skipped, deferred, or failed outcome.
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
        merged: PropertyAggregate,
    ) -> AnalysisOutcome:
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
                analysis = result.assessment
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
                outcome = await coro
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
            except _AnalysisExhausted as exc:
                logger.warning(
                    "analysis_exhausted",
                    property_id=exc.property_id,
                    reason="paid_operation_attempt_limit",
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

            await on_result(outcome)
            if outcome.disposition in (AnalysisDisposition.ASSESSED, AnalysisDisposition.SKIPPED):
                count += 1
    finally:
        # Ensure all child tasks are cleaned up on cancellation or any exit
        remaining = [t for t in tasks if not t.done()]
        if remaining:
            for t in remaining:
                t.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)

    return count


async def analyze_with_spend_disposition(
    analyzer: PropertyQualityAnalyzer,
    merged: PropertyAggregate,
    settings: Settings,
    storage: PropertyStorage,
) -> AnalysisOutcome:
    try:
        candidate, assessment = await analyzer.analyze_single_merged(
            merged, data_dir=settings.data_dir
        )
        return AnalysisOutcome.from_assessment(candidate, assessment)
    except SpendDeferred as exc:
        if exc.reason != "attempt_limit":
            raise
        await storage.processing.mark_analysis_exhausted(
            merged.unique_id, "paid_operation_attempt_limit"
        )
        raise _AnalysisExhausted(merged.unique_id) from exc


def analysis_concurrency(settings: Settings) -> int:
    """Queue work behind local slots instead of rejecting it at spend admission."""
    policy = settings.analysis_spend_policy
    if policy is None:
        return settings.quality_concurrency
    return min(settings.quality_concurrency, policy.max_in_flight)


def warn_if_spending_unconfigured(settings: Settings) -> None:
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


async def persist_estimated_floor_area(
    merged: PropertyAggregate,
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


async def complete_initial_assessment(
    merged: PropertyAggregate,
    quality_analysis: PropertyQualityAnalysis | None,
    storage: PropertyStorage,
    *,
    notification_policy: NotificationPolicy | None = None,
) -> None:
    """Persist an initial assessment and its notification eligibility decision.

    Called after quality analysis completes or is skipped. The property was saved to DB
    by save_pre_analysis_properties() before analysis started.
    """
    # Persist the selection alongside the completed assessment.
    await storage.processing.complete_analysis(
        merged.unique_id, quality_analysis, notification_policy=notification_policy
    )


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
