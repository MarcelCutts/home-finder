"""Main entry point for the home finder scraper."""

import argparse
import asyncio
import contextlib
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict

import structlog

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import configure_logging, get_logger
from home_finder.models import (
    MergedProperty,
    PropertyQualityAnalysis,
    PropertySource,
)
from home_finder.notifiers import TelegramNotifier
from home_finder.pipeline.analysis import (
    _CommInfo,
    _drain_reanalysis_queue,
    _OnResult,
    _run_quality_and_save,
    run_reanalysis,
)
from home_finder.pipeline.commands import (
    run_backfill_commute,
    run_check_off_market,
    run_dedup_existing,
)
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent
from home_finder.pipeline.runner import exclusive_runner, runner_lock
from home_finder.pipeline.scraping import (
    scrape_all_platforms,
)
from home_finder.pipeline.stages import (
    PreAnalysisResult,
    _run_pre_analysis_pipeline,
)
from home_finder.utils.async_tasks import finish_before_cancelling, finish_cleanup
from home_finder.utils.image_cache import backfill_thumbnails

logger = get_logger(__name__)


def _make_notify_callback(
    notifier: TelegramNotifier,
    storage: PropertyStorage,
    counter: list[int],
    *,
    recorder: EventRecorder | None = None,
) -> _OnResult:
    """Build the on_result callback for live pipeline runs (sends Telegram)."""

    async def _notify(
        merged: MergedProperty,
        commute_info: _CommInfo,
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
    results: list[tuple[MergedProperty, _CommInfo, PropertyQualityAnalysis | None]],
) -> _OnResult:
    """Build the on_result callback for dry runs (accumulates for summary)."""

    async def _accumulate(
        merged: MergedProperty,
        commute_info: _CommInfo,
        quality_analysis: PropertyQualityAnalysis | None,
    ) -> None:
        results.append((merged, commute_info, quality_analysis))

    return _accumulate


def _log_dry_run_summary(
    processed: list[tuple[MergedProperty, _CommInfo, PropertyQualityAnalysis | None]],
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
    """Run the full scraping and notification pipeline.

    Quality analysis runs concurrently (bounded by semaphore), and each
    property is saved + notified as soon as its analysis completes.

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
            await _run_pipeline_with_storage(
                settings,
                own_storage,
                max_per_scraper=max_per_scraper,
                only_scrapers=only_scrapers,
                full_scrape=full_scrape,
                dry_run=dry_run,
            )


async def _record_pre_analysis_progress(
    storage: PropertyStorage, run_id: int, progress: PreAnalysisResult
) -> None:
    """Persist the last known counts, even when the next stage never starts."""
    await storage.pipeline.update_pipeline_run(
        run_id,
        scraped_count=max(
            progress.scraped_count, sum(m.properties_found for m in progress.scraper_metrics)
        ),
        new_count=len(progress.merged_to_process),
        enriched_count=progress.enriched_count,
        anchors_updated=progress.anchors_updated,
        criteria_filtered_count=progress.criteria_filtered_count,
        location_filtered_count=progress.location_filtered_count,
        new_property_count=progress.new_property_count,
        commute_within_limit_count=progress.commute_within_limit_count,
        post_dedup_count=progress.post_dedup_count,
        post_floorplan_count=progress.post_floorplan_count,
        **progress.stage_timings,
    )
    await storage.pipeline.save_scraper_runs(run_id, [asdict(m) for m in progress.scraper_metrics])


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
        run_id = await storage.pipeline.create_pipeline_run()
        logger.info("pipeline_run_created", run_id=run_id)

    try:
        async with _pipeline_resources() as stack:
            with _pipeline_phase("setup"):
                try:
                    await finish_before_cancelling(create_run())
                finally:
                    structlog.contextvars.bind_contextvars(run_id=run_id)
                assert run_id is not None
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

            with _pipeline_phase("notification_recovery") as phase:
                if dry_run:
                    phase.update(outcome="skipped", reason="dry_run")
                else:
                    assert notifier is not None
                    await storage.deliveries.recover()
                    recovered_count = await notifier.deliver_pending()
                    phase["completed"] = recovered_count
                    logger.info("notification_recovery_complete", completed=recovered_count)

            progress = PreAnalysisResult()
            with _pipeline_phase("pre_analysis") as phase:
                primary_error: BaseException | None = None
                try:
                    pre = await _run_pre_analysis_pipeline(
                        settings,
                        storage,
                        max_per_scraper=max_per_scraper,
                        only_scrapers=only_scrapers,
                        full_scrape=full_scrape,
                        recorder=recorder,
                        progress=progress,
                    )
                    if pre is not None:
                        progress = pre
                    phase.update(outcome=progress.collection_status, scraped=progress.scraped_count)
                except BaseException as exc:
                    primary_error = exc
                    raise
                finally:
                    await finish_cleanup(
                        _record_pre_analysis_progress(storage, run_id, progress),
                        primary_error=primary_error,
                        operation_name="pre_analysis_accounting",
                    )
            if pre is None:
                # Discovery is one producer of work. Existing pending analysis,
                # reanalysis and price-drop delivery still need their drainers.
                pre = progress

            logger.info(
                "pipeline_started",
                phase="quality_analysis_and_save" if dry_run else "quality_analysis_and_notify",
                count=len(pre.merged_to_process),
                dry_run=dry_run,
            )

            # Build callback
            notified_counter = [0]  # mutable counter for live mode
            dry_run_results: list[
                tuple[MergedProperty, _CommInfo, PropertyQualityAnalysis | None]
            ] = []

            if dry_run:
                on_result = _make_accumulate_callback(dry_run_results)
            else:
                assert notifier is not None
                on_result = _make_notify_callback(
                    notifier, storage, notified_counter, recorder=recorder
                )

            t_analysis = time.monotonic()
            analysis_counts: dict[str, int | float] = {}
            with _pipeline_phase("quality_analysis_and_save") as phase:
                primary_error = None
                try:
                    analyzed_count, token_usage = await _run_quality_and_save(
                        pre, settings, storage, on_result, recorder=recorder
                    )
                    analysis_counts["analyzed_count"] = analyzed_count
                    phase["processed"] = analyzed_count
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
                        storage.pipeline.update_pipeline_run(
                            run_id,
                            analysis_seconds=time.monotonic() - t_analysis,
                            notified_count=notified_counter[0],
                            **analysis_counts,
                        ),
                        primary_error=primary_error,
                        operation_name="quality_analysis_accounting",
                    )

            # Re-analyze cross-run merges that gained new source data
            with _pipeline_phase("reanalysis") as phase:
                primary_error = None
                try:
                    reanalyzed_count = await _drain_reanalysis_queue(settings, storage)
                    phase["processed"] = reanalyzed_count
                except BaseException as exc:
                    primary_error = exc
                    raise
                finally:
                    await finish_cleanup(
                        storage.pipeline.update_pipeline_run(
                            run_id, analysis_seconds=time.monotonic() - t_analysis
                        ),
                        primary_error=primary_error,
                        operation_name="reanalysis_accounting",
                    )

            delivery_count = 0
            with _pipeline_phase("notification_delivery") as phase:
                if dry_run:
                    phase.update(outcome="skipped", reason="dry_run")
                else:
                    assert notifier is not None
                    t_notify = time.monotonic()
                    primary_error = None
                    try:
                        delivery_count = await notifier.deliver_pending()
                        phase["completed"] = delivery_count
                        if delivery_count:
                            logger.info("queued_notifications_sent", count=delivery_count)
                    except BaseException as exc:
                        primary_error = exc
                        raise
                    finally:
                        await finish_cleanup(
                            storage.pipeline.update_pipeline_run(
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
                await storage.pipeline.cleanup_old_events(settings.event_retention_runs)
            await finish_before_cancelling(
                storage.pipeline.complete_pipeline_run(run_id, progress.collection_status)
            )

        logger.info(
            "pipeline_complete",
            status=progress.collection_status,
            analyzed=analyzed_count,
            notified=notified_counter[0],
            reanalyzed=reanalyzed_count,
            queued_deliveries=delivery_count,
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
                await storage.pipeline.complete_pipeline_run(
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


async def run_dry_run(
    settings: Settings,
    **kwargs: object,
) -> None:
    """Deprecated: use run_pipeline(settings, dry_run=True)."""
    await run_pipeline(settings, dry_run=True, **kwargs)  # type: ignore[arg-type]


@exclusive_runner
async def run_scrape_only(
    settings: Settings,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
) -> None:
    """Run scraping only and print results (no filtering, storage, or notifications).

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
        only_scrapers: If set, only run these scrapers (by source value).
    """
    criteria = settings.get_search_criteria()
    search_areas = settings.get_search_areas()

    logger.info("scrape_only_started")
    all_properties, _metrics = await scrape_all_platforms(
        min_price=criteria.min_price,
        max_price=criteria.max_price,
        min_bedrooms=criteria.min_bedrooms,
        max_bedrooms=criteria.max_bedrooms,
        search_areas=search_areas,
        furnish_types=settings.get_furnish_types(),
        min_bathrooms=settings.min_bathrooms,
        include_let_agreed=settings.include_let_agreed,
        max_per_scraper=max_per_scraper,
        proxy_url=settings.proxy_url,
        only_scrapers=only_scrapers,
        zoopla_max_areas=settings.zoopla_max_areas_per_run,
    )

    logger.info("scrape_only_complete", count=len(all_properties))

    for prop in all_properties:
        logger.info(
            "scraped_property",
            source=prop.source.value,
            title=prop.title,
            price_pcm=prop.price_pcm,
            bedrooms=prop.bedrooms,
            address=prop.address,
            postcode=prop.postcode,
            url=str(prop.url),
        )


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Home Finder - Multi-platform London rental property scraper"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without sending Telegram notifications",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Only scrape and print properties (no filtering, storage, or notifications)",
    )
    parser.add_argument(
        "--max-per-scraper",
        type=int,
        default=None,
        help="Limit properties per scraper (for faster dev/test runs)",
    )
    parser.add_argument(
        "--scrapers",
        type=str,
        default=None,
        help="Comma-separated list of scrapers to run (e.g. openrent,zoopla). "
        "Options: openrent, rightmove, zoopla, onthemarket",
    )
    parser.add_argument(
        "--backfill-commute",
        action="store_true",
        help="Backfill commute data for properties with coordinates but no commute_minutes",
    )
    parser.add_argument(
        "--dedup-existing",
        action="store_true",
        help="Retroactively merge duplicate properties already in the database",
    )
    parser.add_argument(
        "--generate-thumbnails",
        action="store_true",
        help="Generate missing thumbnails for all cached gallery images",
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Re-run quality analysis on flagged properties",
    )
    parser.add_argument(
        "--outcode",
        type=str,
        default=None,
        help="Comma-separated outcodes to flag for re-analysis (e.g. E2,E8). Use with --reanalyze",
    )
    parser.add_argument(
        "--request-only",
        action="store_true",
        help="Only flag properties for re-analysis, don't run analysis. Use with --reanalyze",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="reanalyze_all",
        help="Flag ALL analyzed properties for re-analysis. Use with --reanalyze",
    )
    parser.add_argument(
        "--check-off-market",
        action="store_true",
        help="Check active properties for off-market removal signals via URL spot-check",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start web server with background pipeline scheduler",
    )
    parser.add_argument(
        "--no-pipeline",
        action="store_true",
        help="With --serve: start web server only, skip background pipeline",
    )
    parser.add_argument(
        "--full-scrape",
        action="store_true",
        help="Compatibility flag; collection continues through known listings within normal bounds",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging for troubleshooting",
    )
    args = parser.parse_args()

    # Configure logging
    import logging

    configure_logging(json_output=False, level=logging.DEBUG if args.debug else logging.INFO)

    try:
        settings = Settings()
    except Exception as e:
        logger.error(
            "failed_to_load_settings",
            error=str(e),
            hint="Ensure .env has HOME_FINDER_TELEGRAM_BOT_TOKEN and HOME_FINDER_TELEGRAM_CHAT_ID",
            exc_info=True,
        )
        sys.exit(1)

    logger.info(
        "starting_home_finder",
        min_price=settings.min_price,
        max_price=settings.max_price,
        min_bedrooms=settings.min_bedrooms,
        max_bedrooms=settings.max_bedrooms,
        destination=settings.destination_postcode,
        max_commute=settings.max_commute_minutes,
        dry_run=args.dry_run,
        scrape_only=args.scrape_only,
    )

    # Parse --scrapers into a set of source values
    only_scrapers: set[str] | None = None
    if args.scrapers:
        valid = {s.value for s in PropertySource}
        only_scrapers = {s.strip().lower() for s in args.scrapers.split(",")}
        unknown = only_scrapers - valid
        if unknown:
            logger.error(
                "unknown_scrapers",
                unknown=sorted(unknown),
                valid=sorted(valid),
            )
            sys.exit(1)

    if args.backfill_commute:
        asyncio.run(run_backfill_commute(settings))
    elif args.dedup_existing:
        asyncio.run(run_dedup_existing(settings))
    elif args.generate_thumbnails:
        with runner_lock(settings.database_path) as acquired:
            if not acquired:
                logger.info("runner_skipped", operation="thumbnails", reason="already_running")
                return
            generated, skipped, deleted = backfill_thumbnails(settings.data_dir)
            logger.info(
                "thumbnails_backfilled",
                generated=generated,
                skipped=skipped,
                corrupt_deleted=deleted,
            )
    elif args.reanalyze:
        outcodes = [o.strip().upper() for o in args.outcode.split(",")] if args.outcode else None
        asyncio.run(
            run_reanalysis(
                settings,
                outcodes=outcodes,
                reanalyze_all=args.reanalyze_all,
                request_only=args.request_only,
            )
        )
    elif args.check_off_market:
        asyncio.run(run_check_off_market(settings, only_scrapers=only_scrapers))
    elif args.serve:
        import uvicorn

        from home_finder.web.app import create_app

        app = create_app(settings, run_pipeline=not args.no_pipeline)
        uvicorn.run(app, host=settings.web_host, port=settings.web_port, log_level="info")
    elif args.scrape_only:
        asyncio.run(
            run_scrape_only(
                settings, max_per_scraper=args.max_per_scraper, only_scrapers=only_scrapers
            )
        )
    elif args.dry_run:
        asyncio.run(
            run_pipeline(
                settings,
                max_per_scraper=args.max_per_scraper,
                only_scrapers=only_scrapers,
                full_scrape=args.full_scrape,
                dry_run=True,
            )
        )
    else:
        asyncio.run(
            run_pipeline(
                settings,
                max_per_scraper=args.max_per_scraper,
                only_scrapers=only_scrapers,
                full_scrape=args.full_scrape,
            )
        )


if __name__ == "__main__":
    main()
