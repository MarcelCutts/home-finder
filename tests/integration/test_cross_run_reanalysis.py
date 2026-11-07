"""A home notified in one run gains a source in a later run and is reassessed.

Boundary: consolidation against the stored anchor, the reassessment request it
records, and the reanalysis queue that a later full run, a dry run, or the
standalone processor drains. Collection, detail acquisition, the analyzer and the
Bot API are faked (``tests/support/pipeline_run.py``); storage and delivery
planning are real. Narrower consolidation cases live in ``tests/test_pipeline``.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import Property, PropertyQualityAnalysis, PropertySource
from home_finder.models.notifications import NotificationPolicy
from home_finder.pipeline.flow import run_dry_run, run_pipeline
from home_finder.pipeline.matching import consolidate_candidates
from home_finder.pipeline.reanalysis import process_reanalysis_queue
from tests.support.matching import as_aggregate
from tests.support.pipeline_run import (
    pipeline_boundaries,
    reanalysis_queue_ids,
    save_notified_anchor,
    stored_sources,
)


@pytest.fixture
def initial_assessment(make_quality_analysis: Callable[..., PropertyQualityAnalysis]):
    return make_quality_analysis(rating=4, summary="Initial assessment from the first run")


@pytest.fixture
def reassessment(make_quality_analysis: Callable[..., PropertyQualityAnalysis]):
    return make_quality_analysis(rating=5, summary="Reassessment with the new source's evidence")


@pytest.fixture
def analysis_settings(tmp_path: Path) -> Settings:
    """Analysis enabled, every assessed home eligible for delivery, and the database
    and image cache in tmp_path."""
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        database_path=str(tmp_path / "home_finder.db"),
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=True,
        notification_policy=NotificationPolicy(min_fit_score=0),
        require_floorplan=False,
        anthropic_api_key=SecretStr("test-key"),
    )


async def test_later_source_requests_reassessment_that_the_queue_completes(
    storage: PropertyStorage,
    analysis_settings: Settings,
    make_property: Callable[..., Property],
    initial_assessment: PropertyQualityAnalysis,
    reassessment: PropertyQualityAnalysis,
):
    # make_property gives both listings the same address, coordinates, price and size.
    rightmove = make_property(source=PropertySource.RIGHTMOVE)
    zoopla = make_property(source=PropertySource.ZOOPLA)
    anchor = await save_notified_anchor(storage, rightmove, initial_assessment)

    result = await consolidate_candidates(
        Deduplicator(enable_cross_platform=True), [as_aggregate(zoopla)], storage, set()
    )

    assert result.anchors_updated == 1
    assert result.unmatched_candidates == []
    assert await stored_sources(storage, anchor.unique_id) == {"rightmove", "zoopla"}
    assert await reanalysis_queue_ids(storage) == [anchor.unique_id]

    with pipeline_boundaries(assess=lambda merged, **kw: (merged, reassessment)) as boundaries:
        summary = await process_reanalysis_queue(analysis_settings, storage)

    assert summary.completed == 1
    (reassessed,) = [call.args[0] for call in boundaries.analyzer.analyze_single_merged.mock_calls]
    assert set(reassessed.sources) == {PropertySource.RIGHTMOVE, PropertySource.ZOOPLA}
    assert await reanalysis_queue_ids(storage) == []
    stored = await storage.get_quality_analysis(anchor.unique_id)
    assert stored is not None and stored.overall_rating == 5
    # Reassessment neither resets the sent status nor plans another delivery.
    tracked = await storage.get_property(anchor.unique_id)
    assert tracked is not None and tracked.notification_status.value == "sent"
    assert (await storage.deliveries.report())["counts"] == {}


async def test_second_run_merges_new_source_and_reassesses_the_delivered_home(
    analysis_settings: Settings,
    make_property: Callable[..., Property],
    initial_assessment: PropertyQualityAnalysis,
    reassessment: PropertyQualityAnalysis,
):
    rightmove = make_property(source=PropertySource.RIGHTMOVE, source_id="rm-e2e")
    zoopla = make_property(source=PropertySource.ZOOPLA, source_id="z-e2e")
    assessments = iter([initial_assessment, reassessment])

    with pipeline_boundaries(assess=lambda merged, **kw: (merged, next(assessments))) as boundaries:
        boundaries.scrape.side_effect = [([rightmove], []), ([zoopla], [])]
        await run_pipeline(analysis_settings)
        await run_pipeline(analysis_settings)

    first, second = [call.args[0] for call in boundaries.analyzer.analyze_single_merged.mock_calls]
    assert first.sources == (PropertySource.RIGHTMOVE,)
    assert set(second.sources) == {PropertySource.RIGHTMOVE, PropertySource.ZOOPLA}
    # Only the first run's discovery reaches the notifier.
    assert boundaries.notifier.send_merged_property_notification.call_count == 1
    async with PropertyStorage(analysis_settings.database_path) as storage:
        assert await storage.web.get_property_count() == 1
        assert await stored_sources(storage, rightmove.unique_id) == {"rightmove", "zoopla"}
        assert await reanalysis_queue_ids(storage) == []
        stored = await storage.get_quality_analysis(rightmove.unique_id)
        assert stored is not None and stored.summary == reassessment.summary
        assert (await storage.deliveries.report())["counts"] == {"complete": 1}


async def test_dry_run_reassesses_requested_home_without_delivery(
    analysis_settings: Settings,
    make_property: Callable[..., Property],
    initial_assessment: PropertyQualityAnalysis,
    reassessment: PropertyQualityAnalysis,
):
    anchor_listing = make_property(source=PropertySource.OPENRENT, source_id="anchor-dry")
    async with PropertyStorage(analysis_settings.database_path) as storage:
        anchor = await save_notified_anchor(storage, anchor_listing, initial_assessment)
        await storage.processing.request_reanalysis([anchor.unique_id])

    # The queue is processed even when collection finds nothing new.
    with pipeline_boundaries(assess=lambda merged, **kw: (merged, reassessment)) as boundaries:
        await run_dry_run(analysis_settings)

    boundaries.notifier.send_merged_property_notification.assert_not_awaited()
    async with PropertyStorage(analysis_settings.database_path) as storage:
        stored = await storage.get_quality_analysis(anchor.unique_id)
        assert stored is not None and stored.overall_rating == 5
        assert await reanalysis_queue_ids(storage) == []
        assert (await storage.deliveries.report())["counts"] == {}
