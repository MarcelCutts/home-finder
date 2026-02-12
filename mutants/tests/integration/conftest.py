"""Pytest fixtures for integration tests.

Handles Crawlee state isolation between tests to prevent event loop conflicts.
"""

import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio

from home_finder.config import Settings
from home_finder.db import PropertyStorage


@pytest.fixture(autouse=True)
def reset_crawlee_state() -> Generator[None, None, None]:
    """Reset Crawlee's global state between tests.

    Crawlee caches service locators and storage clients that are bound to
    specific event loops. Without resetting this state, tests running with
    different event loops will fail with "attached to a different event loop".

    This follows the pattern used in Crawlee's own test suite.
    """
    # Reset before test
    _clear_crawlee_caches()

    yield

    # Reset after test
    _clear_crawlee_caches()


def _clear_crawlee_caches() -> None:
    """Clear Crawlee's internal caches and state."""
    try:
        from crawlee._service_locator import service_locator

        # Reset the service locator to clear cached clients bound to old event loops
        service_locator._configuration = None
        service_locator._event_manager = None
        service_locator._storage_client = None
    except (ImportError, AttributeError):
        pass

    try:
        from crawlee.storages import KeyValueStore

        # Clear KeyValueStore cache
        if hasattr(KeyValueStore, "_cache"):
            KeyValueStore._cache.clear()
        if hasattr(KeyValueStore, "_cache_by_id"):
            KeyValueStore._cache_by_id.clear()
        if hasattr(KeyValueStore, "_cache_by_name"):
            KeyValueStore._cache_by_name.clear()
    except (ImportError, AttributeError):
        pass

    try:
        from crawlee.statistics import Statistics

        # Clear Statistics instance cache
        if hasattr(Statistics, "_instance"):
            Statistics._instance = None
    except (ImportError, AttributeError):
        pass

    try:
        from crawlee.crawlers import BasicCrawler

        # Clear BasicCrawler class-level cache
        if hasattr(BasicCrawler, "_running_crawlers"):
            BasicCrawler._running_crawlers.clear()
    except (ImportError, AttributeError):
        pass


@pytest.fixture(autouse=True)
def set_crawlee_storage_dir(tmp_path: Path) -> Generator[None, None, None]:
    """Use a temporary directory for Crawlee storage during tests.

    This prevents tests from interfering with each other through shared storage.
    """
    old_value = os.environ.get("CRAWLEE_STORAGE_DIR")
    os.environ["CRAWLEE_STORAGE_DIR"] = str(tmp_path / "crawlee_storage")

    yield

    if old_value is not None:
        os.environ["CRAWLEE_STORAGE_DIR"] = old_value
    else:
        os.environ.pop("CRAWLEE_STORAGE_DIR", None)


@pytest_asyncio.fixture
async def in_memory_storage() -> AsyncGenerator[PropertyStorage, None]:
    """In-memory SQLite storage for integration tests."""
    storage = PropertyStorage(":memory:")
    await storage.initialize()
    yield storage
    await storage.close()


@pytest.fixture
def test_settings() -> Settings:
    """Settings configured for integration testing (no real APIs)."""
    return Settings(
        telegram_bot_token="fake:test-token",
        telegram_chat_id=0,
        database_path=":memory:",
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=False,
        require_floorplan=False,
        traveltime_app_id="",
        traveltime_api_key="",
    )
