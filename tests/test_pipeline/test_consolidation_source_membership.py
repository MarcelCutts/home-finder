"""Which candidates join a stored anchor, and when joining requests a reassessment.

These extend ``test_cross_run_consolidation.py`` with several sources in one run,
anchors without an assessment, repeated requests, partial re-observation, pending
enrichment retries, and candidates that must stay separate. ``make_property``
gives every listing the same address, coordinates, price and size unless a test
overrides one, so two listings from different portals match by default.
"""

from collections.abc import Callable

import pytest

from home_finder.db import PropertyStorage
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import Property, PropertyQualityAnalysis, PropertySource
from home_finder.pipeline.matching import consolidate_candidates
from tests.support.matching import as_aggregate
from tests.support.pipeline_run import (
    reanalysis_queue_ids,
    save_notified_anchor,
    stored_sources,
)


@pytest.fixture
def matcher() -> Deduplicator:
    return Deduplicator(enable_cross_platform=True)


@pytest.fixture
def assessment(make_quality_analysis: Callable[..., PropertyQualityAnalysis]):
    return make_quality_analysis(rating=4, summary="Assessment from an earlier run")


async def _requested_at(storage: PropertyStorage, unique_id: str) -> str | None:
    async with storage._read_connection() as conn:
        cursor = await conn.execute(
            "SELECT reanalysis_requested_at FROM quality_analyses WHERE property_unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return row[0]


async def test_two_new_sources_join_the_anchor_in_one_run(
    storage, matcher, make_property, assessment
):
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.OPENRENT, price_pcm=1800), assessment
    )
    zoopla = make_property(source=PropertySource.ZOOPLA, price_pcm=1850)
    rightmove = make_property(source=PropertySource.RIGHTMOVE, price_pcm=1800)

    result = await consolidate_candidates(
        matcher, [as_aggregate(zoopla), as_aggregate(rightmove)], storage, set()
    )

    # The two newcomers group first, then the group joins the anchor once.
    assert result.anchors_updated == 1
    assert result.unmatched_candidates == []
    assert await stored_sources(storage, anchor.unique_id) == {"openrent", "zoopla", "rightmove"}
    assert await reanalysis_queue_ids(storage) == [anchor.unique_id]
    stored = await storage.get_recent_properties_for_dedup(days=30)
    assert [(home.min_price, home.max_price) for home in stored] == [(1800, 1850)]


async def test_anchor_without_assessment_gains_source_without_a_reassessment_request(
    storage, matcher, make_property
):
    """A run interrupted before assessment leaves no row to flag for reassessment."""
    anchor = await save_notified_anchor(storage, make_property(source=PropertySource.RIGHTMOVE))

    result = await consolidate_candidates(
        matcher, [as_aggregate(make_property(source=PropertySource.ZOOPLA))], storage, set()
    )

    assert result.anchors_updated == 1
    assert result.unmatched_candidates == []
    assert await stored_sources(storage, anchor.unique_id) == {"rightmove", "zoopla"}
    assert await storage.get_quality_analysis(anchor.unique_id) is None
    assert await reanalysis_queue_ids(storage) == []


async def test_merge_into_requested_anchor_keeps_the_first_request_time(
    storage, matcher, make_property, assessment
):
    """Repeated requests coalesce into the pending one rather than restarting it."""
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.RIGHTMOVE), assessment
    )
    await storage.processing.request_reanalysis([anchor.unique_id])
    first_request = await _requested_at(storage, anchor.unique_id)
    assert first_request is not None

    result = await consolidate_candidates(
        matcher, [as_aggregate(make_property(source=PropertySource.ZOOPLA))], storage, set()
    )

    assert result.anchors_updated == 1
    assert await _requested_at(storage, anchor.unique_id) == first_request
    assert await reanalysis_queue_ids(storage) == [anchor.unique_id]


async def test_reobserving_known_sources_keeps_all_sources_without_reanalysis(
    storage, matcher, make_property, assessment
):
    """Regression: re-observing two of four known sources once requested a reassessment
    every run, because the comparison treated a subset of sources as a change."""
    openrent, rightmove, zoopla, onthemarket = (
        make_property(source=source)
        for source in (
            PropertySource.OPENRENT,
            PropertySource.RIGHTMOVE,
            PropertySource.ZOOPLA,
            PropertySource.ONTHEMARKET,
        )
    )
    anchor = await save_notified_anchor(storage, openrent, assessment)
    for newcomer in (rightmove, zoopla, onthemarket):
        result = await consolidate_candidates(matcher, [as_aggregate(newcomer)], storage, set())
        assert result.anchors_updated == 1
    all_sources = {"openrent", "rightmove", "zoopla", "onthemarket"}
    assert await stored_sources(storage, anchor.unique_id) == all_sources
    # Finish the request so it cannot mask a new one from the re-observation.
    await storage.processing.complete_reanalysis(anchor.unique_id, assessment)
    assert await reanalysis_queue_ids(storage) == []

    result = await consolidate_candidates(
        matcher, [as_aggregate(openrent), as_aggregate(rightmove)], storage, set()
    )

    assert result.anchors_updated == 1
    assert await reanalysis_queue_ids(storage) == []
    assert await stored_sources(storage, anchor.unique_id) == all_sources


async def test_same_price_and_size_at_another_address_stays_a_candidate(
    storage, matcher, make_property, assessment
):
    await save_notified_anchor(storage, make_property(source=PropertySource.RIGHTMOVE), assessment)
    elsewhere = make_property(
        source=PropertySource.ZOOPLA,
        postcode="E8 4AB",
        latitude=51.5520,
        longitude=-0.0610,
        address="45 Dalston Lane, Hackney, London",
    )

    result = await consolidate_candidates(matcher, [as_aggregate(elsewhere)], storage, set())

    assert result.anchors_updated == 0
    assert [home.unique_id for home in result.unmatched_candidates] == [elsewhere.unique_id]


async def test_same_address_with_a_different_bedroom_count_stays_a_candidate(
    storage, matcher, make_property, assessment
):
    await save_notified_anchor(
        storage, make_property(source=PropertySource.RIGHTMOVE, bedrooms=1), assessment
    )
    larger = make_property(source=PropertySource.ZOOPLA, bedrooms=2)

    result = await consolidate_candidates(matcher, [as_aggregate(larger)], storage, set())

    assert result.anchors_updated == 0
    assert [home.unique_id for home in result.unmatched_candidates] == [larger.unique_id]


async def test_second_listing_on_the_anchor_portal_stays_a_candidate(
    storage, matcher, make_property, assessment
):
    """Two listings on one portal are two adverts, so they are never matched."""
    await save_notified_anchor(
        storage, make_property(source=PropertySource.OPENRENT, source_id="or-anchor"), assessment
    )
    same_portal = make_property(source=PropertySource.OPENRENT, source_id="or-new")

    result = await consolidate_candidates(matcher, [as_aggregate(same_portal)], storage, set())

    assert result.anchors_updated == 0
    assert [home.unique_id for home in result.unmatched_candidates] == [same_portal.unique_id]


@pytest.mark.parametrize("both_pending", [False, True], ids=["one-pending", "both-pending"])
async def test_pending_duplicate_group_is_saved_and_donor_retries_retired(
    storage: PropertyStorage,
    matcher: Deduplicator,
    make_property: Callable[..., Property],
    both_pending: bool,
):
    first = make_property(source=PropertySource.OPENRENT)
    second = make_property(source=PropertySource.ZOOPLA)
    if both_pending:
        await storage.processing.save_unenriched_property(as_aggregate(first))
    await storage.processing.save_unenriched_property(as_aggregate(second))
    retries = await storage.processing.get_unenriched_properties()
    inputs = retries if both_pending else [as_aggregate(first), *retries]

    result = await consolidate_candidates(
        matcher, inputs, storage, {retry.unique_id for retry in retries}
    )
    assert len(result.unmatched_candidates) == 1
    await storage.processing.save_pre_analysis_properties(result.unmatched_candidates, {})

    assert await storage.processing.get_unenriched_properties() == []
    pending = await storage.processing.get_pending_analysis_properties()
    assert len(pending) == 1
    assert pending[0].source_urls == {first.source: first.url, second.source: second.url}
    async with storage._read_connection() as conn:
        cursor = await conn.execute("SELECT merged_id FROM source_listings")
        assert {row[0] for row in await cursor.fetchall()} == {pending[0].unique_id}
    # Replaying persistence after an interrupted analysis does not revive donors.
    await storage.processing.save_pre_analysis_properties(pending, {})
    assert await storage.processing.get_unenriched_properties() == []
