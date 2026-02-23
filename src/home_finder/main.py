"""Main entry point for the home finder scraper."""

import argparse
import asyncio
import contextlib
import sys
import time
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
from home_finder.pipeline.scraping import (
    scrape_all_platforms,
)
from home_finder.pipeline.stages import (
    _days_since,
    _run_pre_analysis_pipeline,
)
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
            await storage.mark_notified(merged.unique_id)
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
            await storage.mark_notified(uid)
            counter[0] += 1
            if recorder is not None:
                recorder.record(PropertyEvent(uid, src, "notified", "notification"))
        else:
            await storage.mark_notification_failed(uid)
            if recorder is not None:
                recorder.record(
                    PropertyEvent(uid, src, "notification_failed", "notification")
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


async def run_pipeline(
    settings: Settings,
    *,
    max_per_scraper: int | None = None,
    only_scrapers: set[str] | None = None,
    full_scrape: bool = False,
    dry_run: bool = False,
) -> None:
    """Run the full scraping and notification pipeline.

    Quality analysis runs concurrently (bounded by semaphore), and each
    property is saved + notified as soon as its analysis completes.

    Args:
        settings: Application settings.
        max_per_scraper: Maximum properties per scraper (None for unlimited).
        only_scrapers: If set, only run these scrapers (by source value).
        full_scrape: Disable early-stop pagination (scrape all pages).
        dry_run: If True, save to DB but skip Telegram notifications.
    """
    async with PropertyStorage(settings.database_path) as storage:
        # Pipeline run tracking — only in live mode
        run_id: int | None = None
        if not dry_run:
            run_id = await storage.create_pipeline_run()
            structlog.contextvars.bind_contextvars(run_id=run_id)
            logger.info("pipeline_run_created", run_id=run_id)

        # Notifier context — real TelegramNotifier in live mode, nullcontext for dry run
        notifier_cm: contextlib.AbstractAsyncContextManager[TelegramNotifier | None]
        if dry_run:
            notifier_cm = contextlib.nullcontext(None)
        else:
            notifier_cm = TelegramNotifier(
                bot_token=settings.telegram_bot_token.get_secret_value(),
                chat_id=settings.telegram_chat_id,
                web_base_url=settings.web_base_url,
                data_dir=settings.data_dir,
            )

        # T4: EventRecorder for property audit trail
        recorder_cm: contextlib.AbstractAsyncContextManager[EventRecorder | None]
        if run_id is not None:
            recorder_cm = EventRecorder(storage, run_id)
        else:
            recorder_cm = contextlib.nullcontext(None)

        async with notifier_cm as notifier, recorder_cm as recorder:
            try:
                # Step 0: Retry unsent notifications (live mode only)
                if not dry_run and notifier is not None:
                    unsent = await storage.get_unsent_notifications()
                    if unsent:
                        logger.info("retrying_unsent_notifications", count=len(unsent))
                        for tracked in unsent:
                            success = await notifier.send_property_notification(
                                tracked.property,
                                commute_minutes=tracked.commute_minutes,
                                transport_mode=tracked.transport_mode,
                            )
                            if success:
                                await storage.mark_notified(tracked.property.unique_id)
                                logger.info(
                                    "retry_notification_sent",
                                    unique_id=tracked.property.unique_id,
                                )
                            else:
                                logger.warning(
                                    "retry_notification_failed",
                                    unique_id=tracked.property.unique_id,
                                )
                            await asyncio.sleep(1)

                pre = await _run_pre_analysis_pipeline(
                    settings,
                    storage,
                    max_per_scraper=max_per_scraper,
                    only_scrapers=only_scrapers,
                    full_scrape=full_scrape,
                    recorder=recorder,
                )
                if pre is None:
                    if dry_run:
                        logger.info("dry_run_no_new_properties")
                    elif run_id is not None:
                        await storage.complete_pipeline_run(run_id, "completed")
                    return

                if not dry_run and run_id is not None:
                    await storage.update_pipeline_run(
                        run_id,
                        scraped_count=pre.scraped_count,
                        new_count=len(pre.merged_to_process),
                        enriched_count=pre.enriched_count,
                        anchors_updated=pre.anchors_updated,
                        criteria_filtered_count=pre.criteria_filtered_count,
                        location_filtered_count=pre.location_filtered_count,
                        new_property_count=pre.new_property_count,
                        commute_within_limit_count=pre.commute_within_limit_count,
                        post_dedup_count=pre.post_dedup_count,
                        post_floorplan_count=pre.post_floorplan_count,
                        **pre.stage_timings,
                    )
                    if pre.scraper_metrics:
                        await storage.save_scraper_runs(
                            run_id, [asdict(m) for m in pre.scraper_metrics]
                        )

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
                analyzed_count, token_usage = await _run_quality_and_save(
                    pre, settings, storage, on_result, recorder=recorder
                )

                # Re-analyze cross-run merges that gained new source data
                reanalyzed_count = await _drain_reanalysis_queue(settings, storage)
                analysis_seconds = time.monotonic() - t_analysis

                if dry_run:
                    _log_dry_run_summary(dry_run_results)
                    logger.info(
                        "dry_run_complete",
                        saved=analyzed_count,
                        reanalyzed=reanalyzed_count,
                    )
                else:
                    assert notifier is not None
                    assert run_id is not None
                    notified_count = notified_counter[0]

                    # Send price drop notifications
                    t_notify = time.monotonic()
                    price_drops = await storage.get_unsent_price_drops()
                    price_drop_count = 0
                    for drop in price_drops:
                        success = await notifier.send_price_drop_notification(
                            title=drop["title"],
                            postcode=drop.get("postcode") or "",
                            old_price=drop["old_price"],
                            new_price=drop["new_price"],
                            unique_id=drop["unique_id"],
                            days_listed=_days_since(drop.get("first_seen")),
                        )
                        if success:
                            await storage.mark_price_drop_notified(drop["unique_id"])
                            price_drop_count += 1
                            await asyncio.sleep(1)  # Telegram rate limit
                    if price_drop_count:
                        logger.info("price_drop_notifications_sent", count=price_drop_count)
                    notification_seconds = time.monotonic() - t_notify

                    cost_kwargs: dict[str, int | float] = {}
                    if token_usage is not None:
                        cost_kwargs = {
                            "total_input_tokens": token_usage.input_tokens,
                            "total_output_tokens": token_usage.output_tokens,
                            "total_cache_read_tokens": token_usage.cache_read_tokens,
                            "total_cache_creation_tokens": token_usage.cache_creation_tokens,
                            "estimated_cost_usd": token_usage.estimated_cost_usd,
                        }
                    await storage.update_pipeline_run(
                        run_id,
                        analyzed_count=analyzed_count,
                        notified_count=notified_count,
                        analysis_seconds=analysis_seconds,
                        notification_seconds=notification_seconds,
                        **cost_kwargs,
                    )
                    await storage.complete_pipeline_run(run_id, "completed")

                    # T4: prune old property events
                    await storage.cleanup_old_events(settings.event_retention_runs)

                    logger.info(
                        "pipeline_complete",
                        notified=notified_count,
                        reanalyzed=reanalyzed_count,
                        price_drops=price_drop_count,
                    )

            except asyncio.CancelledError:
                if run_id is not None:
                    logger.warning("pipeline_cancelled", run_id=run_id)
                    await storage.complete_pipeline_run(run_id, "cancelled")
                raise
            except Exception as exc:
                if run_id is not None:
                    await storage.complete_pipeline_run(run_id, "failed", error_message=str(exc))
                raise
            finally:
                if not dry_run:
                    structlog.contextvars.clear_contextvars()


async def run_dry_run(
    settings: Settings,
    **kwargs: object,
) -> None:
    """Deprecated: use run_pipeline(settings, dry_run=True)."""
    await run_pipeline(settings, dry_run=True, **kwargs)  # type: ignore[arg-type]


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
        help="Disable pagination early-stop — scrape all pages"
        " even if properties are already in DB",
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
