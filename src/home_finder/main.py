"""Command-line parsing and dispatch for Home Finder."""

import argparse
import asyncio
import sys

from home_finder.config import Settings
from home_finder.logging import configure_logging, get_logger
from home_finder.models import PropertySource
from home_finder.pipeline.collection import run_scrape_only
from home_finder.pipeline.commands import (
    run_backfill_commute,
    run_check_off_market,
    run_dedup_existing,
)
from home_finder.pipeline.flow import run_pipeline
from home_finder.pipeline.reanalysis import run_reanalysis
from home_finder.pipeline.run_lock import runner_lock
from home_finder.utils.image_cache import backfill_thumbnails

logger = get_logger(__name__)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Home Finder - Multi-platform London rental property scraper"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without sending Telegram notifications",
    )
    modes.add_argument(
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
    modes.add_argument(
        "--backfill-commute",
        action="store_true",
        help="Backfill commute data for properties with coordinates but no commute_minutes",
    )
    modes.add_argument(
        "--dedup-existing",
        action="store_true",
        help="Retire duplicate pending staging rows while preserving established homes",
    )
    modes.add_argument(
        "--generate-thumbnails",
        action="store_true",
        help="Generate missing thumbnails for all cached gallery images",
    )
    modes.add_argument(
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
    modes.add_argument(
        "--check-off-market",
        action="store_true",
        help="Check active properties for off-market removal signals via URL spot-check",
    )
    modes.add_argument(
        "--serve",
        action="store_true",
        help="Start the dashboard; collection requires --with-pipeline",
    )
    scheduler = parser.add_mutually_exclusive_group()
    scheduler.add_argument(
        "--with-pipeline",
        action="store_true",
        help="With --serve: explicitly enable the background collection scheduler",
    )
    scheduler.add_argument(
        "--no-pipeline",
        action="store_true",
        help="With --serve: dashboard only (the default; retained for existing commands)",
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

    if (args.with_pipeline or args.no_pipeline) and not args.serve:
        parser.error("--with-pipeline and --no-pipeline require --serve")
    if (args.outcode is not None or args.request_only or args.reanalyze_all) and not args.reanalyze:
        parser.error("--outcode, --request-only and --all require --reanalyze")
    if args.outcode is not None and args.reanalyze_all:
        parser.error("choose either --outcode or --all")
    collection = not any(
        (
            args.backfill_commute,
            args.dedup_existing,
            args.generate_thumbnails,
            args.reanalyze,
            args.check_off_market,
            args.serve,
        )
    )
    if args.max_per_scraper is not None:
        if not collection:
            parser.error("--max-per-scraper requires pipeline collection or --scrape-only")
        if args.max_per_scraper < 1:
            parser.error("--max-per-scraper must be positive")
    if args.full_scrape and (not collection or args.scrape_only):
        parser.error("--full-scrape is only supported by pipeline collection")
    only_scrapers: set[str] | None = None
    if args.scrapers is not None:
        if not (collection or args.check_off_market):
            parser.error("--scrapers requires collection or --check-off-market")
        valid = {source.value for source in PropertySource}
        only_scrapers = {source.strip().lower() for source in args.scrapers.split(",")}
        if only_scrapers - valid:
            parser.error(f"--scrapers must contain only: {', '.join(sorted(valid))}")

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

        app = create_app(settings, run_pipeline=args.with_pipeline)
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
