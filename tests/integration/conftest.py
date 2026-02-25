"""Pytest fixtures for integration tests."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage

# Crawlee state isolation fixtures (reset_crawlee_state, set_crawlee_storage_dir)
# live in the root tests/conftest.py so they're available project-wide.


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
        telegram_bot_token=SecretStr("fake:test-token"),
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
        traveltime_api_key=SecretStr(""),
    )
