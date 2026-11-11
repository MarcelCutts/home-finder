"""Storage fixtures shared by the persistence tests."""

from collections.abc import AsyncIterator

import pytest

from home_finder.db.storage import PropertyStorage


@pytest.fixture
async def storage() -> AsyncIterator[PropertyStorage]:
    """An empty in-memory database at the current schema version."""
    async with PropertyStorage(":memory:") as db:
        yield db
