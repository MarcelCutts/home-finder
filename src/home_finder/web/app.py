"""FastAPI application factory with background pipeline scheduler."""

import asyncio
import contextlib
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import configure_logging, get_logger

logger = get_logger(__name__)

WEB_DIR: Final = Path(__file__).parent

PIPELINE_INITIAL_DELAY_SECONDS: Final = 30

# Module-level lock prevents overlapping pipeline runs
_pipeline_lock = asyncio.Lock()

# Jitter range (seconds) added to sleep interval to avoid scraping at fixed offsets
_JITTER_SECONDS: Final = 300


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://unpkg.com https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' https://*.zoocdn.com https://*.zoopla.com "
            "https://*.rmimg.com https://*.rightmove.co.uk "
            "https://*.onthemarket.com "
            "https://*.openrent.com https://*.openrent.co.uk "
            "https://*.basemaps.cartocdn.com https://unpkg.com data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response


async def _pipeline_loop(settings: Settings, interval_minutes: int) -> None:
    """Run the scraping pipeline on a recurring schedule."""
    from home_finder.main import run_pipeline

    # Initial delay so the web server can become responsive first
    logger.info("pipeline_scheduler_initial_delay", seconds=PIPELINE_INITIAL_DELAY_SECONDS)
    await asyncio.sleep(PIPELINE_INITIAL_DELAY_SECONDS)

    while True:
        if _pipeline_lock.locked():
            logger.warning("pipeline_still_running_skipping")
        else:
            async with _pipeline_lock:
                logger.info("pipeline_scheduler_running")
                try:
                    await run_pipeline(settings)
                except Exception:
                    logger.error("pipeline_scheduler_error", exc_info=True)

        jitter = random.uniform(-_JITTER_SECONDS, _JITTER_SECONDS)
        sleep_seconds = interval_minutes * 60 + jitter
        logger.info(
            "pipeline_scheduler_sleeping",
            minutes=interval_minutes,
            jitter_seconds=round(jitter),
        )
        await asyncio.sleep(sleep_seconds)


async def _register_telegram_webhook(settings: Settings) -> None:
    """Register the Telegram webhook URL so inline button callbacks are delivered."""
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        bot = Bot(
            token=settings.telegram_bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        webhook_url = f"{settings.web_base_url.rstrip('/')}/telegram/webhook"
        try:
            await bot.set_webhook(
                url=webhook_url,
                secret_token=settings.telegram_webhook_secret,
            )
            logger.info("telegram_webhook_registered", url=webhook_url)
        finally:
            await bot.session.close()
    except Exception:
        logger.warning("telegram_webhook_registration_failed", exc_info=True)


def _compute_static_version() -> str:
    """Return a cache-busting version string from the mtime of static assets."""
    static_dir = WEB_DIR / "static"
    mtime = 0.0
    for name in ("app.js", "style.css"):
        path = static_dir / name
        if path.exists():
            mtime = max(mtime, path.stat().st_mtime)
    return str(int(mtime))


def create_app(settings: Settings | None = None, *, run_pipeline: bool = True) -> FastAPI:
    """Create the FastAPI application.

    Args:
        settings: Application settings. Loaded from env if not provided.
        run_pipeline: Whether to start the background pipeline scheduler.
    """
    if settings is None:
        settings = Settings()

    configure_logging(json_output=False)

    storage = PropertyStorage(settings.database_path)
    pipeline_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal pipeline_task
        await storage.initialize()
        app.state.storage = storage
        app.state.settings = settings
        app.state.pipeline_lock = _pipeline_lock

        # Register Telegram webhook if both base URL and secret are configured
        if (
            settings.telegram_webhook_secret
            and settings.web_base_url
            and settings.telegram_bot_token.get_secret_value()
        ):
            await _register_telegram_webhook(settings)

        if run_pipeline:
            # Start background pipeline scheduler
            pipeline_task = asyncio.create_task(
                _pipeline_loop(settings, settings.pipeline_interval_minutes)
            )
            logger.info(
                "web_server_started",
                pipeline_interval=settings.pipeline_interval_minutes,
            )
        else:
            logger.info("web_server_started", pipeline="disabled")

        yield

        # Shutdown
        if pipeline_task:
            pipeline_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pipeline_task
        await storage.close()
        logger.info("web_server_stopped")

    app = FastAPI(title="Home Finder", lifespan=lifespan)

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # Mount static files
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    # Register routes
    from home_finder.web.routes import router

    app.include_router(router)

    # Register Telegram webhook handler (only active when secret is configured)
    from home_finder.web.telegram_webhook import router as webhook_router

    app.include_router(webhook_router)

    return app
