"""One reassessment queue processor shared by scheduled runs and the CLI."""

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import get_logger
from home_finder.models import PropertyAggregate
from home_finder.pipeline import image_recovery
from home_finder.pipeline.assessment import (
    analysis_concurrency,
    analyze_with_spend_disposition,
    create_quality_analyzer,
    persist_estimated_floor_area,
    run_concurrent_analysis,
    warn_if_spending_unconfigured,
)
from home_finder.pipeline.contracts import AnalysisDisposition, AnalysisOutcome, ReanalysisSummary
from home_finder.pipeline.run_lock import exclusive_runner, recover_abandoned_spend

logger = get_logger(__name__)


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
            flagged = await storage.processing.request_reanalysis_by_filter(
                outcodes=outcodes, all_properties=reanalyze_all
            )
            target = "all properties" if reanalyze_all else f"outcodes {', '.join(outcodes or [])}"
            logger.info("reanalysis_properties_flagged", flagged=flagged, target=target)

        if request_only:
            return

        await recover_abandoned_spend(storage)

        summary = await process_reanalysis_queue(settings, storage)
        logger.info(
            "reanalysis_complete",
            completed=summary.completed,
            failed=summary.failed,
            deferred=summary.deferred,
        )


async def process_reanalysis_queue(
    settings: Settings,
    storage: PropertyStorage,
) -> ReanalysisSummary:
    """Refresh requested assessments while preserving existing notification status.

    The queue has its own explicit per-run allowance. Both entry points use
    this processor, including cache restoration, spend admission, and outcomes.
    """
    summary = ReanalysisSummary()
    if not settings.enable_quality_filter or not settings.anthropic_api_key.get_secret_value():
        logger.info("quality_analysis_not_configured")
        return summary

    requested = await storage.processing.get_reanalysis_queue()
    queue = requested[: settings.max_reanalyses_per_run]
    summary.deferred = len(requested) - len(queue)
    if not queue:
        logger.info("reanalysis_queue_empty")
        return summary

    warn_if_spending_unconfigured(settings)
    queue = await image_recovery.restore_missing_analysis_images(queue, settings, storage)

    logger.info("reanalysis_queue_drain_started", count=len(queue))

    async with create_quality_analyzer(settings, storage) as analyzer:

        async def _analyze(
            merged: PropertyAggregate,
        ) -> AnalysisOutcome:
            return await analyze_with_spend_disposition(analyzer, merged, settings, storage)

        async def _handle_result(outcome: AnalysisOutcome) -> None:
            merged, quality_analysis = outcome.candidate, outcome.assessment
            if outcome.disposition == AnalysisDisposition.ASSESSED:
                assert quality_analysis is not None
                await storage.processing.complete_reanalysis(merged.unique_id, quality_analysis)
                await persist_estimated_floor_area(merged, quality_analysis, storage)
                summary.completed += 1
                return
            logger.info(
                "reanalysis_deferred",
                property_id=merged.unique_id,
                reason=outcome.reason,
            )
            if outcome.disposition == AnalysisDisposition.DEFERRED:
                summary.deferred += 1
            else:
                summary.failed += 1

        def count_failure() -> None:
            summary.failed += 1

        def count_deferral() -> None:
            summary.deferred += 1

        await run_concurrent_analysis(
            queue,
            _analyze,
            _handle_result,
            on_error=count_failure,
            on_deferred=count_deferral,
            concurrency=analysis_concurrency(settings),
            per_property_timeout=settings.property_analysis_timeout,
            breaker_log_event="reanalysis_api_circuit_breaker",
            error_log_event="reanalysis_failed",
        )

    logger.info(
        "reanalysis_queue_drain_complete",
        reanalyzed=summary.completed,
        failed=summary.failed,
        deferred=summary.deferred,
        queued=len(queue),
    )
    return summary
