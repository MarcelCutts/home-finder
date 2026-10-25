"""Collection, assessment, recovery, and delivery in execution order."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict

import structlog

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import get_logger
from home_finder.models import (
    PropertyAggregate,
    PropertyQualityAnalysis,
)
from home_finder.notifiers import TelegramNotifier
from home_finder.pipeline.assessment import (
    AssessmentCallback,
    CommuteInfo,
    process_initial_assessments,
)
from home_finder.pipeline.cache_recovery import repair_merged_image_caches
from home_finder.pipeline.candidates import prepare_candidates
from home_finder.pipeline.contracts import CandidateProgress
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent
from home_finder.pipeline.reanalysis import process_reanalysis_queue
from home_finder.pipeline.run_lock import exclusive_runner, recover_abandoned_spend
from home_finder.utils.async_tasks import finish_before_cancelling, finish_cleanup

logger = get_logger(__name__)


@exclusive_runner
async def run_pipeline(
    settings: Settings,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
    dry_run: bool = False,
    storage: PropertyStorage | None = None,
) -> None:
    """Collect evidence, prepare candidates, process assessments, and drain deliveries.

    Initial assessments run concurrently; each durable result may trigger an
    immediate delivery attempt. Deferred work remains queued for later runs.

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
        only_scrapers: If set, only run these scrapers (by source value).
        full_scrape: Compatibility flag; collection no longer stops at known IDs.
        dry_run: If True, save to DB but skip Telegram notifications.
        storage: Optional pre-existing storage instance (e.g. from web server).
            When provided, the caller owns the connection lifecycle.
            When None (CLI mode), a fresh storage is created and closed.
    """
    if storage is not None:
        await recover_abandoned_spend(storage)
        await _run_pipeline_with_storage(
            settings,
            storage,
            max_per_scraper=max_per_scraper,
            only_scrapers=only_scrapers,
            full_scrape=full_scrape,
            dry_run=dry_run,
        )
    else:
        async with PropertyStorage(settings.database_path) as own_storage:
            await recover_abandoned_spend(own_storage)
            await _run_pipeline_with_storage(
                settings,
                own_storage,
                max_per_scraper=max_per_scraper,
                only_scrapers=only_scrapers,
                full_scrape=full_scrape,
                dry_run=dry_run,
            )


async def _run_pipeline_with_storage(
    settings: Settings,
    storage: PropertyStorage,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
    dry_run: bool = False,
) -> None:
    """Pipeline body that operates on a provided storage instance."""
    run_id: int | None = None
    context_tokens = structlog.contextvars.bind_contextvars(run_id=None, dry_run=dry_run)

    async def create_run() -> None:
        nonlocal run_id
        # Retain the ID even if cancellation arrives while creation commits.
        run_id = await storage.runs.create_pipeline_run()
        logger.info("pipeline_run_created", run_id=run_id)

    try:
        async with _pipeline_resources() as stack:
            with _pipeline_phase("setup"):
                try:
                    await finish_cleanup(
                        create_run(), primary_error=None, operation_name="pipeline_run_creation"
                    )
                finally:
                    structlog.contextvars.bind_contextvars(run_id=run_id)
                assert run_id is not None
                await repair_merged_image_caches(storage, settings.data_dir)
                notifier = None
                if not dry_run:
                    notifier = await stack.enter_async_context(
                        TelegramNotifier(
                            bot_token=settings.telegram_bot_token.get_secret_value(),
                            chat_id=settings.telegram_chat_id,
                            web_base_url=settings.web_base_url,
                            data_dir=settings.data_dir,
                            delivery_repository=storage.deliveries,
                        )
                    )
                recorder = await stack.enter_async_context(EventRecorder(storage, run_id))

            with _pipeline_phase("delivery_recovery") as phase:
                if dry_run:
                    phase.update(outcome="skipped", reason="dry_run")
                else:
                    assert notifier is not None
                    await storage.deliveries.recover()
                    recovered_count = await notifier.deliver_pending()
                    phase["completed"] = recovered_count
                    logger.info("delivery_recovery_complete", completed=recovered_count)

            progress = CandidateProgress()
            with _pipeline_phase("candidate_preparation") as phase:
                primary_error: BaseException | None = None
                try:
                    batch = await prepare_candidates(
                        settings,
                        storage,
                        max_per_scraper=max_per_scraper,
                        only_scrapers=only_scrapers,
                        full_scrape=full_scrape,
                        recorder=recorder,
                        progress=progress,
                    )
                    progress = batch.progress
                    phase.update(
                        outcome=progress.collection_status, scraped=progress.observed_listing_count
                    )
                except BaseException as exc:
                    primary_error = exc
                    raise
                finally:
                    await finish_cleanup(
                        _record_candidate_progress(storage, run_id, progress),
                        primary_error=primary_error,
                        operation_name="candidate_preparation_accounting",
                    )
            # An empty candidate batch still permits pending assessments,
            # requested reanalysis, and queued delivery to advance.

            logger.info(
                "pipeline_started",
                phase="initial_assessment",
                count=len(batch.candidates),
                dry_run=dry_run,
            )

            callback_delivery_count = [0]  # mutable counter for live mode
            dry_run_results: list[
                tuple[PropertyAggregate, CommuteInfo, PropertyQualityAnalysis | None]
            ] = []

            if dry_run:
                on_result = _make_accumulate_callback(dry_run_results)
            else:
                assert notifier is not None
                on_result = _make_notify_callback(
                    notifier, storage, callback_delivery_count, recorder=recorder
                )

            t_analysis = time.monotonic()
            analysis_counts: dict[str, int | float] = {}
            with _pipeline_phase("initial_assessment") as phase:
                primary_error = None
                try:
                    assessment = await process_initial_assessments(
                        batch, settings, storage, on_result, recorder=recorder
                    )
                    processed_count = assessment.processed_count
                    analysis_counts["analyzed_count"] = processed_count
                    phase.update(
                        processed=processed_count,
                        assessed=assessment.assessed,
                        skipped=assessment.skipped,
                        deferred=assessment.deferred,
                        failed=assessment.failed,
                    )
                    token_usage = assessment.token_usage
                    if token_usage is not None:
                        analysis_counts.update(
                            total_input_tokens=token_usage.input_tokens,
                            total_output_tokens=token_usage.output_tokens,
                            total_cache_read_tokens=token_usage.cache_read_tokens,
                            total_cache_creation_tokens=token_usage.cache_creation_tokens,
                            estimated_cost_usd=token_usage.estimated_cost_usd,
                        )
                except BaseException as exc:
                    primary_error = exc
                    raise
                finally:
                    # Save known totals before reanalysis or delivery can fail.
                    # Interrupted/reanalysis spend is reconciled in the ledger.
                    await finish_cleanup(
                        storage.runs.update_pipeline_run(
                            run_id,
                            analysis_seconds=time.monotonic() - t_analysis,
                            notified_count=callback_delivery_count[0],
                            **analysis_counts,
                        ),
                        primary_error=primary_error,
                        operation_name="initial_assessment_accounting",
                    )

            # Process reassessments requested by source merges or the user.
            with _pipeline_phase("reanalysis") as phase:
                primary_error = None
                try:
                    reanalysis = await process_reanalysis_queue(settings, storage)
                    reanalyzed_count = reanalysis.completed
                    phase.update(
                        processed=reanalysis.completed,
                        failed=reanalysis.failed,
                        deferred=reanalysis.deferred,
                    )
                except BaseException as exc:
                    primary_error = exc
                    raise
                finally:
                    await finish_cleanup(
                        storage.runs.update_pipeline_run(
                            run_id, analysis_seconds=time.monotonic() - t_analysis
                        ),
                        primary_error=primary_error,
                        operation_name="reanalysis_accounting",
                    )

            final_drain_completed = 0
            with _pipeline_phase("delivery_drain") as phase:
                if dry_run:
                    phase.update(outcome="skipped", reason="dry_run")
                else:
                    assert notifier is not None
                    t_notify = time.monotonic()
                    primary_error = None
                    try:
                        final_drain_completed = await notifier.deliver_pending()
                        phase["completed"] = final_drain_completed
                        if final_drain_completed:
                            logger.info("queued_notifications_sent", count=final_drain_completed)
                    except BaseException as exc:
                        primary_error = exc
                        raise
                    finally:
                        await finish_cleanup(
                            storage.runs.update_pipeline_run(
                                run_id, notification_seconds=time.monotonic() - t_notify
                            ),
                            primary_error=primary_error,
                            operation_name="notification_accounting",
                        )

            if dry_run:
                _log_dry_run_summary(dry_run_results)

        # Finalize only after recorder flushing and notifier cleanup succeed.
        with _pipeline_phase("finalization"):
            if not dry_run:
                await storage.runs.cleanup_old_events(settings.event_retention_runs)
            await finish_cleanup(
                storage.runs.complete_pipeline_run(run_id, progress.collection_status),
                primary_error=None,
                operation_name="pipeline_run_completion",
            )

        logger.info(
            "pipeline_complete",
            status=progress.collection_status,
            initial_assessments_processed=processed_count,
            assessed=assessment.assessed,
            analysis_skipped=assessment.skipped,
            callback_deliveries_completed=callback_delivery_count[0],
            reanalyzed=reanalyzed_count,
            final_drain_completed_deliveries=final_drain_completed,
        )
    except (asyncio.CancelledError, Exception) as exc:
        cancelled = isinstance(exc, asyncio.CancelledError)
        status = "cancelled" if cancelled else "failed"
        error_message = None if cancelled else str(exc)
        finalized = False
        if run_id is not None:

            async def finalize_failure() -> None:
                nonlocal finalized
                assert run_id is not None
                await storage.runs.complete_pipeline_run(
                    run_id, status, error_message=error_message
                )
                finalized = True

            try:
                await finish_before_cancelling(finalize_failure())
            except asyncio.CancelledError:
                # Preserve the original exception after repeated cancellation.
                if not finalized:
                    logger.warning("pipeline_finalization_cancelled", status=status)
            except Exception:
                logger.exception("pipeline_finalization_failed", status=status)
        if cancelled:
            logger.warning("pipeline_cancelled", status=status, finalized=finalized)
        else:
            logger.exception("pipeline_failed", status=status, finalized=finalized)
        raise
    finally:
        structlog.contextvars.reset_contextvars(**context_tokens)


def _make_notify_callback(
    notifier: TelegramNotifier,
    storage: PropertyStorage,
    counter: list[int],
    *,
    recorder: EventRecorder | None = None,
) -> AssessmentCallback:
    """Build the on_result callback for live pipeline runs (sends Telegram)."""

    async def _notify(
        merged: PropertyAggregate,
        commute_info: CommuteInfo,
        quality_analysis: PropertyQualityAnalysis | None,
    ) -> None:
        # Skip notification for properties with no images — not useful
        if not merged.images and not merged.canonical.image_url:
            logger.info("notification_deferred", property_id=merged.unique_id, reason="no_images")
            if recorder is not None:
                recorder.record(
                    PropertyEvent(
                        merged.unique_id,
                        merged.canonical.source.value,
                        "notification_deferred",
                        "notification",
                        {"reason": "no_images"},
                    )
                )
            return

        commute_minutes = commute_info[0] if commute_info else None
        transport_mode = commute_info[1] if commute_info else None

        success = await notifier.send_merged_property_notification(
            merged,
            commute_minutes=commute_minutes,
            transport_mode=transport_mode,
            quality_analysis=quality_analysis,
        )

        uid = merged.canonical.unique_id
        src = merged.canonical.source.value
        if success:
            counter[0] += 1
            if recorder is not None:
                recorder.record(PropertyEvent(uid, src, "notified", "notification"))
        else:
            if recorder is not None:
                delivery = await storage.deliveries.get(f"discovery:{uid}")
                recorder.record(
                    PropertyEvent(
                        uid,
                        src,
                        "notification_deferred",
                        "notification",
                        {
                            "delivery_state": delivery["state"] if delivery else "missing_intent",
                            "reason": delivery["reason"] if delivery else None,
                        },
                    )
                )

        await asyncio.sleep(1)  # Telegram rate limit

    return _notify


def _make_accumulate_callback(
    results: list[tuple[PropertyAggregate, CommuteInfo, PropertyQualityAnalysis | None]],
) -> AssessmentCallback:
    """Build the on_result callback for dry runs (accumulates for summary)."""

    async def _accumulate(
        merged: PropertyAggregate,
        commute_info: CommuteInfo,
        quality_analysis: PropertyQualityAnalysis | None,
    ) -> None:
        results.append((merged, commute_info, quality_analysis))

    return _accumulate


def _log_dry_run_summary(
    processed: list[tuple[PropertyAggregate, CommuteInfo, PropertyQualityAnalysis | None]],
) -> None:
    """Log a structured summary of dry-run results."""
    for merged, commute_info, quality_analysis in processed:
        prop = merged.canonical
        commute_minutes = commute_info[0] if commute_info else None
        transport_mode = commute_info[1] if commute_info else None

        log_kw: dict[str, object] = {
            "sources": [s.value for s in merged.sources],
            "title": prop.title,
            "price_pcm": prop.price_pcm,
            "bedrooms": prop.bedrooms,
            "address": prop.address,
            "url": str(prop.url),
        }
        if prop.postcode:
            log_kw["postcode"] = prop.postcode
        if merged.price_varies:
            log_kw["min_price"] = merged.min_price
            log_kw["max_price"] = merged.max_price
        if commute_minutes is not None:
            log_kw["commute_minutes"] = commute_minutes
            log_kw["transport_mode"] = transport_mode.value if transport_mode else None
        if merged.images or merged.floorplan:
            log_kw["images"] = len(merged.images)
            log_kw["has_floorplan"] = bool(merged.floorplan)
        if quality_analysis:
            log_kw["summary"] = quality_analysis.summary
            if quality_analysis.value and quality_analysis.value.rating:
                log_kw["value_rating"] = quality_analysis.value.rating

        logger.info("dry_run_property", **log_kw)

    logger.info("dry_run_summary", count=len(processed))


async def _record_candidate_progress(
    storage: PropertyStorage, run_id: int, progress: CandidateProgress
) -> None:
    """Persist the last known counts, even when the next stage never starts."""
    await storage.runs.update_pipeline_run(run_id, **progress.run_columns())
    await storage.runs.save_scraper_runs(run_id, [asdict(m) for m in progress.scraper_metrics])


@contextlib.contextmanager
def _pipeline_phase(phase: str) -> Iterator[dict[str, int | float | str]]:
    """Log execution outcomes; completed does not mean every property succeeded."""
    started = time.monotonic()
    summary: dict[str, int | float | str] = {"outcome": "completed"}
    logger.debug("pipeline_phase_started", phase=phase)
    try:
        yield summary
    except asyncio.CancelledError:
        summary["outcome"] = "cancelled"
        raise
    except Exception as exc:
        summary.update(outcome="failed", error_type=type(exc).__name__)
        raise
    finally:
        logger.debug(
            "pipeline_phase_finished",
            phase=phase,
            elapsed_seconds=time.monotonic() - started,
            **summary,
        )


@contextlib.asynccontextmanager
async def _pipeline_resources() -> AsyncIterator[contextlib.AsyncExitStack]:
    """Close all pipeline resources without masking a body failure/cancellation."""
    stack = contextlib.AsyncExitStack()
    primary_error: BaseException | None = None
    try:
        yield stack
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        await finish_cleanup(
            stack.__aexit__(
                type(primary_error) if primary_error is not None else None,
                primary_error,
                primary_error.__traceback__ if primary_error is not None else None,
            ),
            primary_error=primary_error,
            operation_name="pipeline_resources",
        )


async def run_dry_run(
    settings: Settings,
    **kwargs: object,
) -> None:
    """Deprecated: use run_pipeline(settings, dry_run=True)."""
    await run_pipeline(settings, dry_run=True, **kwargs)  # type: ignore[arg-type]
