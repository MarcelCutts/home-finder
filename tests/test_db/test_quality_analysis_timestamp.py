"""The analysis time shown on a listing is the time of the analysis it shows."""

from collections.abc import AsyncGenerator, Callable

import pytest
import pytest_asyncio

from home_finder.db.storage import PropertyStorage
from home_finder.models import MergedProperty, PropertyQualityAnalysis


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_reanalysis_moves_the_analysed_at_time(
    storage: PropertyStorage,
    make_merged_property: Callable[..., MergedProperty],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
) -> None:
    merged = make_merged_property()
    await storage.save_merged_property(merged)
    await storage.save_quality_analysis(merged.unique_id, make_quality_analysis(summary="First"))

    # Age the first analysis, as if it had been saved long ago.
    conn = await storage._get_connection()
    await conn.execute(
        "UPDATE quality_analyses SET created_at = ? WHERE property_unique_id = ?",
        ("2020-01-01T00:00:00+00:00", merged.unique_id),
    )
    await conn.commit()

    await storage.save_quality_analysis(merged.unique_id, make_quality_analysis(summary="Second"))
    prop = await storage.web.get_property_detail(merged.unique_id)
    assert prop is not None
    assert prop["quality_analysis"].summary == "Second"
    assert prop["analysed_at"] > "2025", "the date must belong to the analysis shown"
