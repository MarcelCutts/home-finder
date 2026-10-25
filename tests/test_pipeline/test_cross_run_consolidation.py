"""Consolidation across observations preserves identity and requests fresh assessment."""

from datetime import UTC, datetime, timedelta

import pytest

from home_finder.db import PropertyStorage
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import NotificationStatus, PropertySource
from home_finder.pipeline.matching import consolidate_candidates


@pytest.fixture
async def storage():
    async with PropertyStorage(":memory:") as db:
        yield db


@pytest.fixture
def matching_listings(make_merged_property):
    shared = {
        "price_pcm": 2000,
        "bedrooms": 2,
        "address": "Flat 4, 123 Mare Street, Hackney",
        "postcode": "E8 3RH",
        "latitude": 51.5465,
        "longitude": -0.0553,
    }
    anchor = make_merged_property(
        sources=(PropertySource.OPENRENT,),
        source_id="original",
        **shared,
        first_seen=datetime.now(UTC) - timedelta(days=14),
    )
    incoming = make_merged_property(
        sources=(PropertySource.ZOOPLA,),
        source_id="later",
        **shared,
        first_seen=datetime.now(UTC) - timedelta(days=7),
    )
    return anchor, incoming


async def test_new_source_joins_the_stored_anchor(storage, matching_listings):
    anchor, incoming = matching_listings
    await storage.save_merged_property(anchor)

    result = await consolidate_candidates(
        Deduplicator(enable_cross_platform=True), [incoming], storage, set()
    )

    assert result.unmatched_candidates == []
    assert result.anchors_updated == 1
    stored = await storage.get_recent_properties_for_dedup(days=30)
    assert len(stored) == 1
    assert stored[0].unique_id == anchor.unique_id
    assert set(stored[0].sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}
    assert stored[0].source_urls[PropertySource.ZOOPLA] == incoming.canonical.url


async def test_distinct_home_remains_a_candidate(storage, matching_listings, make_merged_property):
    anchor, _ = matching_listings
    await storage.save_merged_property(anchor)
    different = make_merged_property(
        sources=(PropertySource.ZOOPLA,),
        source_id="different",
        price_pcm=1800,
        bedrooms=1,
        address="45 Kingsland Road, E8 2PB",
        postcode="E8 2PB",
        latitude=51.5491,
        longitude=-0.0764,
    )

    result = await consolidate_candidates(
        Deduplicator(enable_cross_platform=True), [different], storage, set()
    )

    assert [candidate.unique_id for candidate in result.unmatched_candidates] == [
        different.unique_id
    ]
    assert result.anchors_updated == 0
    assert await storage.web.get_property_count() == 1


async def test_new_source_updates_the_stored_price_range(storage, matching_listings):
    anchor, incoming = matching_listings
    incoming = incoming.model_copy(
        update={
            "canonical": incoming.canonical.model_copy(update={"price_pcm": 1950}),
            "min_price": 1950,
            "max_price": 1950,
        }
    )
    await storage.save_merged_property(anchor)

    await consolidate_candidates(
        Deduplicator(enable_cross_platform=True), [incoming], storage, set()
    )

    stored = await storage.get_recent_properties_for_dedup(days=30)
    assert len(stored) == 1
    assert (stored[0].min_price, stored[0].max_price) == (1950, 2000)


async def test_full_postcode_match_leaves_an_outcode_only_anchor_independent(
    storage, matching_listings, make_merged_property
):
    openrent, zoopla = matching_listings
    outcode_only = make_merged_property(
        sources=(PropertySource.RIGHTMOVE,),
        source_id="outcode-only",
        price_pcm=2000,
        bedrooms=2,
        address="Mare Street, Hackney",
        postcode="E8",
        latitude=None,
        longitude=None,
    )
    await storage.save_merged_property(outcode_only)
    matcher = Deduplicator(enable_cross_platform=True)

    first = await consolidate_candidates(matcher, [zoopla], storage, set())
    assert [candidate.unique_id for candidate in first.unmatched_candidates] == [zoopla.unique_id]
    await storage.save_merged_property(first.unmatched_candidates[0])

    second = await consolidate_candidates(matcher, [openrent], storage, set())

    assert second.unmatched_candidates == []
    assert second.anchors_updated == 1
    stored = {
        home.unique_id: home for home in await storage.get_recent_properties_for_dedup(days=30)
    }
    assert set(stored) == {outcode_only.unique_id, zoopla.unique_id}
    assert stored[outcode_only.unique_id].sources == (PropertySource.RIGHTMOVE,)
    assert set(stored[zoopla.unique_id].sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}


async def test_source_merge_preserves_sent_status_without_creating_another_delivery(
    storage, matching_listings
):
    anchor, incoming = matching_listings
    await storage.save_merged_property(anchor)
    await storage.mark_notified(anchor.unique_id)
    before = await storage.deliveries.report()

    await consolidate_candidates(
        Deduplicator(enable_cross_platform=True), [incoming], storage, set()
    )

    stored = await storage.get_property(anchor.unique_id)
    assert stored.notification_status == NotificationStatus.SENT
    assert (await storage.deliveries.report())["counts"] == before["counts"] == {}


async def test_new_source_preserves_assessment_and_requests_reanalysis(
    storage, matching_listings, make_quality_analysis
):
    anchor, incoming = matching_listings
    assessment = make_quality_analysis(summary="Existing assessment")
    await storage.save_merged_property(anchor)
    await storage.save_quality_analysis(anchor.unique_id, assessment)
    assert await storage.processing.get_reanalysis_queue() == []
    stored_assessment = await storage.get_quality_analysis(anchor.unique_id)
    assert stored_assessment is not None

    await consolidate_candidates(
        Deduplicator(enable_cross_platform=True), [incoming], storage, set()
    )

    assert await storage.get_quality_analysis(anchor.unique_id) == stored_assessment
    queue = await storage.processing.get_reanalysis_queue()
    assert [home.unique_id for home in queue] == [anchor.unique_id]
    assert set(queue[0].sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}


@pytest.mark.parametrize("repeat_both_sources", [False, True], ids=["one-source", "both-sources"])
async def test_repeat_observations_do_not_request_another_reanalysis(
    storage, matching_listings, make_quality_analysis, repeat_both_sources
):
    anchor, incoming = matching_listings
    assessment = make_quality_analysis()
    await storage.save_merged_property(anchor)
    await storage.save_quality_analysis(anchor.unique_id, assessment)
    matcher = Deduplicator(enable_cross_platform=True)
    await consolidate_candidates(matcher, [incoming], storage, set())
    assert len(await storage.processing.get_reanalysis_queue()) == 1
    # Finish the earlier request so it cannot mask a new request on a repeat observation.
    await storage.processing.complete_reanalysis(anchor.unique_id, assessment)
    assert await storage.processing.get_reanalysis_queue() == []
    stored_assessment = await storage.get_quality_analysis(anchor.unique_id)
    assert stored_assessment is not None
    observations = [anchor, incoming] if repeat_both_sources else [anchor]

    result = await consolidate_candidates(matcher, observations, storage, set())

    assert result.unmatched_candidates == []
    assert await storage.processing.get_reanalysis_queue() == []
    assert await storage.get_quality_analysis(anchor.unique_id) == stored_assessment
    assert await storage.web.get_property_count() == 1
