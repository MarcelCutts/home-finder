"""A newly matched portal's quote can reveal a lower price for the stored home.

Consolidation compares quotes only for sources the anchor did not have; changes
from an already known source are detected when observations are recorded. The
storage rules for recording a change are in ``tests/test_db/test_price_history.py``.
"""

from collections.abc import Callable

import pytest

from home_finder.db import PropertyStorage
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import PropertyQualityAnalysis, PropertySource
from home_finder.pipeline.matching import consolidate_candidates
from tests.support.matching import as_aggregate
from tests.support.pipeline_run import save_notified_anchor, stored_sources


@pytest.fixture
def matcher() -> Deduplicator:
    return Deduplicator(enable_cross_platform=True)


@pytest.fixture
def assessment(make_quality_analysis: Callable[..., PropertyQualityAnalysis]):
    return make_quality_analysis(rating=4)


async def _price_history(storage: PropertyStorage, unique_id: str) -> list[tuple]:
    async with storage._read_connection() as conn:
        cursor = await conn.execute(
            "SELECT old_price, new_price, change_amount, source "
            "FROM price_history WHERE property_unique_id = ?",
            (unique_id,),
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def test_lower_quote_from_new_portal_records_a_cross_platform_drop(
    storage, matcher, make_property, assessment
):
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.OPENRENT, price_pcm=1800), assessment
    )
    cheaper = make_property(source=PropertySource.ZOOPLA, price_pcm=1700)

    result = await consolidate_candidates(matcher, [as_aggregate(cheaper)], storage, set())

    assert result.anchors_updated == 1
    assert await _price_history(storage, anchor.unique_id) == [(1800, 1700, -100, "cross_platform")]
    async with storage._read_connection() as conn:
        cursor = await conn.execute(
            "SELECT price_pcm, price_drop_notified FROM properties WHERE unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
    # An unnotified drop is what the price-drop alert later picks up.
    assert tuple(row) == (1700, 0)


async def test_equal_quote_from_new_portal_records_no_change(
    storage, matcher, make_property, assessment
):
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.OPENRENT, price_pcm=1800), assessment
    )
    same_price = make_property(source=PropertySource.ZOOPLA, price_pcm=1800)

    result = await consolidate_candidates(matcher, [as_aggregate(same_price)], storage, set())

    assert result.anchors_updated == 1
    assert await _price_history(storage, anchor.unique_id) == []


async def test_higher_quote_widens_the_range_but_keeps_the_lower_price(
    storage, matcher, make_property, assessment
):
    """Another portal's higher quote cannot create an increase and then a false drop."""
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.OPENRENT, price_pcm=1800), assessment
    )
    dearer = make_property(source=PropertySource.ZOOPLA, price_pcm=2000)

    result = await consolidate_candidates(matcher, [as_aggregate(dearer)], storage, set())

    assert result.anchors_updated == 1
    assert await _price_history(storage, anchor.unique_id) == []
    tracked = await storage.get_property(anchor.unique_id)
    assert tracked is not None and tracked.property.price_pcm == 1800
    stored = await storage.get_recent_properties_for_dedup(days=30)
    assert [(home.min_price, home.max_price) for home in stored] == [(1800, 2000)]
    assert await storage.detect_and_record_price_change(anchor.unique_id, 1800) is None


async def test_two_new_portals_record_one_drop_to_the_lowest_quote(
    storage, matcher, make_property, assessment
):
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.OPENRENT, price_pcm=1800), assessment
    )
    zoopla = make_property(source=PropertySource.ZOOPLA, price_pcm=1700)
    rightmove = make_property(source=PropertySource.RIGHTMOVE, price_pcm=1750)

    result = await consolidate_candidates(
        matcher, [as_aggregate(zoopla), as_aggregate(rightmove)], storage, set()
    )

    assert result.anchors_updated == 1
    assert result.unmatched_candidates == []
    assert await _price_history(storage, anchor.unique_id) == [(1800, 1700, -100, "cross_platform")]


async def test_known_portal_quote_is_not_compared_again_during_consolidation(
    storage, matcher, make_property, assessment
):
    """Observation-time detection owns changes from a source the anchor already has."""
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.OPENRENT, price_pcm=1800), assessment
    )
    zoopla = make_property(source=PropertySource.ZOOPLA, price_pcm=1800)
    await consolidate_candidates(matcher, [as_aggregate(zoopla)], storage, set())
    assert await stored_sources(storage, anchor.unique_id) == {"openrent", "zoopla"}
    reduced = zoopla.model_copy(update={"price_pcm": 1700})

    result = await consolidate_candidates(matcher, [as_aggregate(reduced)], storage, set())

    assert result.anchors_updated == 1
    assert await _price_history(storage, anchor.unique_id) == []
