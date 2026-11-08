"""A reassessment request stays queued until a reassessment completes.

``process_reanalysis_queue`` is shared by full runs and the CLI. Its counting and
early returns are tested through the CLI in ``test_reanalysis.py``; these tests
assert which requests remain afterwards.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import Property, PropertyQualityAnalysis
from home_finder.pipeline.reanalysis import process_reanalysis_queue
from tests.support.pipeline_run import (
    pipeline_boundaries,
    reanalysis_queue_ids,
    save_notified_anchor,
)


def _settings(tmp_path: Path, *, analysis_enabled: bool) -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        # The image cache lives beside the database, so keep both in tmp_path.
        database_path=str(tmp_path / "home_finder.db"),
        search_areas="e8",
        enable_quality_filter=analysis_enabled,
        anthropic_api_key=SecretStr("test-key"),
    )


async def _request_reassessments(
    storage: PropertyStorage,
    make_property: Callable[..., Property],
    assessment: PropertyQualityAnalysis,
    count: int,
) -> list[str]:
    ids = []
    for index in range(count):
        listing = make_property(postcode=f"E8 {index}RH", price_pcm=1800 + index * 100)
        anchor = await save_notified_anchor(storage, listing, assessment)
        ids.append(anchor.unique_id)
    await storage.processing.request_reanalysis(ids)
    return ids


async def test_disabled_analysis_leaves_requests_queued(
    storage, make_property, make_quality_analysis, tmp_path
):
    requested = await _request_reassessments(
        storage, make_property, make_quality_analysis(rating=4), count=1
    )

    with pipeline_boundaries() as boundaries:
        summary = await process_reanalysis_queue(
            _settings(tmp_path, analysis_enabled=False), storage
        )

    assert (summary.completed, summary.failed, summary.deferred) == (0, 0, 0)
    boundaries.analyzer_factory.assert_not_called()
    assert await reanalysis_queue_ids(storage) == requested


async def test_failed_reassessment_stays_queued_while_others_complete(
    storage, make_property, make_quality_analysis, tmp_path
):
    requested = await _request_reassessments(
        storage, make_property, make_quality_analysis(rating=4), count=3
    )
    failing_id = requested[1]
    reassessment = make_quality_analysis(rating=5)

    def assess(merged, **kwargs):
        if merged.unique_id == failing_id:
            raise RuntimeError("API timeout")
        return merged, reassessment

    with pipeline_boundaries(assess=assess):
        summary = await process_reanalysis_queue(
            _settings(tmp_path, analysis_enabled=True), storage
        )

    assert (summary.completed, summary.failed) == (2, 1)
    assert await reanalysis_queue_ids(storage) == [failing_id]
    ratings = {uid: (await storage.get_quality_analysis(uid)).overall_rating for uid in requested}
    assert ratings == {requested[0]: 5, failing_id: 4, requested[2]: 5}
