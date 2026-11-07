"""Storage fixtures shared by the pipeline tests."""

from collections.abc import AsyncIterator

import pytest

from home_finder.db import PropertyStorage


@pytest.fixture
async def storage() -> AsyncIterator[PropertyStorage]:
    """An empty in-memory database at the current schema version."""
    async with PropertyStorage(":memory:") as db:
        yield db
