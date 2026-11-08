"""Consolidation copies a newcomer's cached images to the anchor it joins.

A copy failure after the merge commits must leave the reassessment request in
place; ``tests/test_db/test_merge_safety.py`` covers repairing that cache later.
"""

from pathlib import Path

import pytest

from home_finder.matching.deduplication import Deduplicator
from home_finder.models import PropertySource
from home_finder.pipeline.matching import consolidate_candidates
from home_finder.utils.image_cache import get_cache_dir, save_image_bytes
from tests.support.matching import as_aggregate
from tests.support.pipeline_run import (
    reanalysis_queue_ids,
    save_notified_anchor,
    stored_sources,
)


def _cache_images(data_dir: Path, unique_id: str, count: int) -> list[str]:
    cache_dir = get_cache_dir(str(data_dir), unique_id)
    names = [f"gallery_{index:03d}_{index:08x}.jpg" for index in range(count)]
    for name in names:
        save_image_bytes(cache_dir / name, b"cached image bytes")
    return names


async def test_newcomer_cached_images_are_copied_to_the_anchor(
    storage, make_property, make_quality_analysis, tmp_path
):
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.RIGHTMOVE), make_quality_analysis()
    )
    newcomer = make_property(source=PropertySource.ZOOPLA)
    names = _cache_images(tmp_path, newcomer.unique_id, count=3)

    result = await consolidate_candidates(
        Deduplicator(enable_cross_platform=True, data_dir=str(tmp_path)),
        [as_aggregate(newcomer)],
        storage,
        set(),
        data_dir=str(tmp_path),
    )

    assert result.anchors_updated == 1
    anchor_cache = get_cache_dir(str(tmp_path), anchor.unique_id)
    assert sorted(path.name for path in anchor_cache.iterdir()) == names


async def test_merge_without_an_image_cache_still_updates_sources(
    storage, make_property, make_quality_analysis
):
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.RIGHTMOVE), make_quality_analysis()
    )

    result = await consolidate_candidates(
        Deduplicator(enable_cross_platform=True, data_dir=""),
        [as_aggregate(make_property(source=PropertySource.ZOOPLA))],
        storage,
        set(),
        data_dir="",
    )

    assert result.anchors_updated == 1
    assert await stored_sources(storage, anchor.unique_id) == {"rightmove", "zoopla"}


async def test_committed_merge_keeps_reanalysis_queued_when_cache_copy_fails(
    storage, make_property, make_quality_analysis, tmp_path, monkeypatch
):
    anchor = await save_notified_anchor(
        storage, make_property(source=PropertySource.RIGHTMOVE), make_quality_analysis()
    )
    newcomer = make_property(source=PropertySource.ZOOPLA)
    pending = as_aggregate(newcomer)
    await storage.processing.save_unenriched_property(pending)

    def interrupted_copy(*args):
        raise OSError("simulated cache copy interruption")

    monkeypatch.setattr("home_finder.pipeline.matching.copy_cached_images", interrupted_copy)
    with pytest.raises(OSError, match="simulated cache"):
        await consolidate_candidates(
            Deduplicator(enable_cross_platform=True),
            [pending],
            storage,
            {newcomer.unique_id},
            data_dir=str(tmp_path),
        )

    assert await storage.get_property(newcomer.unique_id) is None
    assert await stored_sources(storage, anchor.unique_id) == {"rightmove", "zoopla"}
    assert await reanalysis_queue_ids(storage) == [anchor.unique_id]
    (queued,) = await storage.processing.get_reanalysis_queue()
    assert queued.source_urls[newcomer.source] == newcomer.url
