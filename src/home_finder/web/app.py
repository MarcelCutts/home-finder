"""FastAPI application factory with background pipeline scheduler."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import configure_logging, get_logger

logger = get_logger(__name__)

WEB_DIR = Path(__file__).parent

PIPELINE_INITIAL_DELAY_SECONDS = 30


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


async def _pipeline_loop(settings: Settings, interval_minutes: int) -> None:
    """Run the scraping pipeline on a recurring schedule."""
    from home_finder.main import run_pipeline

    # Initial delay so the web server can become responsive first
    logger.info("pipeline_scheduler_initial_delay", seconds=PIPELINE_INITIAL_DELAY_SECONDS)
    await asyncio.sleep(PIPELINE_INITIAL_DELAY_SECONDS)

    while True:
        logger.info("pipeline_scheduler_running")
        try:
            await run_pipeline(settings)
        except Exception:
            logger.error("pipeline_scheduler_error", exc_info=True)
        logger.info("pipeline_scheduler_sleeping", minutes=interval_minutes)
        await asyncio.sleep(interval_minutes * 60)


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

    return app
