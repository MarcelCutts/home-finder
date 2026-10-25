"""Bound enrichment without losing waiting homes or spending retries on the queue."""

import json
from unittest.mock import AsyncMock

import pytest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.pipeline.candidates import (
    _load_pending_enrichment,
    _prepare_enrichment_batch,
    prepare_candidates,
)
from home_finder.pipeline.contracts import CollectedListings
from home_finder.pipeline.event_recorder import EventRecorder
from home_finder.utils.image_cache import get_cached_image_path, save_image_bytes


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "queue.db"),
        data_dir=str(tmp_path),
        search_areas="e8",
        min_price=1000,
        max_price=2500,
        min_bedrooms=0,
        max_bedrooms=2,
        max_enrichment_per_run=3,
        max_analysis_per_run=1,
        enable_quality_filter=False,
    )


async def pending_rows(storage):
    async with storage._read_connection() as conn:
        rows = await (await conn.execute("SELECT * FROM properties")).fetchall()
        return {row["unique_id"]: dict(row) for row in rows}


async def test_fair_cap_preserves_backlog_attempts_cache_and_restart(
    settings, make_merged_property, monkeypatch
):
    retries = [make_merged_property() for _ in range(3)]
    fresh = [make_merged_property() for _ in range(3)]
    async with PropertyStorage(settings.database_path) as storage:
        for merged in retries:
            await storage.processing.save_unenriched_property(merged)
        cache = get_cached_image_path(
            settings.data_dir, retries[-1].unique_id, "https://example.com/a.jpg", "gallery", 0
        )
        save_image_bytes(cache, b"retained while waiting")
        pending, _ = await _load_pending_enrichment(storage, settings)
        assert len(pending) == 3  # independent of the paid-analysis limit of 1
        assert cache.read_bytes() == b"retained while waiting"
        run_id = await storage.runs.create_pipeline_run()
        async with EventRecorder(storage, run_id) as recorder:
            selected = await _prepare_enrichment_batch(
                fresh, pending, storage, settings, recorder=recorder
            )
        assert [m.unique_id for m in selected] == [
            retries[0].unique_id,
            fresh[0].unique_id,
            retries[1].unique_id,
        ]
        assert cache.read_bytes() == b"retained while waiting"
        stored = await pending_rows(storage)
        assert len(stored) == 6
        for merged in retries + fresh:
            assert stored[merged.unique_id]["enrichment_attempts"] == int(merged in retries)
            assert stored[merged.unique_id]["notification_status"] == "pending_enrichment"
        deferred_ids = set(stored) - {m.unique_id for m in selected}
        async with storage._read_connection() as conn:
            events = await (
                await conn.execute(
                    "SELECT * FROM property_events WHERE event_type = 'enrichment_deferred'"
                )
            ).fetchall()
        assert {row["property_id"] for row in events} == deferred_ids
        assert all(
            json.loads(row["metadata_json"]) == {"reason": "run_limit", "limit": 3}
            for row in events
        )
        for merged in selected:
            await storage.processing.mark_enriched(merged.unique_id)

    # A new process with no discovery still drains the remaining persisted homes.
    async with PropertyStorage(settings.database_path) as storage:
        enrichment = AsyncMock(return_value=[])
        monkeypatch.setattr(
            "home_finder.pipeline.candidates.collect_listings",
            AsyncMock(return_value=CollectedListings()),
        )
        monkeypatch.setattr("home_finder.pipeline.candidates._enrich_candidates", enrichment)
        await prepare_candidates(settings, storage)
        admitted = enrichment.await_args.args[0]
        assert {m.unique_id for m in admitted} == deferred_ids
        stored = await pending_rows(storage)
        for uid in deferred_ids:
            assert stored[uid]["enrichment_attempts"] == int(uid == retries[-1].unique_id)


async def test_discovery_is_queued_before_bounded_enrichment_can_crash(
    settings, make_property, monkeypatch
):
    properties = [make_property() for _ in range(5)]
    settings.max_enrichment_per_run = 2
    enrichment = AsyncMock(side_effect=RuntimeError("interrupted enrichment"))
    monkeypatch.setattr(
        "home_finder.pipeline.candidates.collect_listings",
        AsyncMock(return_value=CollectedListings(listings=properties)),
    )
    monkeypatch.setattr("home_finder.pipeline.candidates._enrich_candidates", enrichment)
    async with PropertyStorage(settings.database_path) as storage:
        with pytest.raises(RuntimeError, match="interrupted enrichment"):
            await prepare_candidates(settings, storage)
        assert len(enrichment.await_args.args[0]) == 2
    async with PropertyStorage(settings.database_path) as storage:
        pending = await storage.processing.get_unenriched_properties()
        assert {m.unique_id for m in pending} == {p.unique_id for p in properties}
        assert all(
            row["enrichment_attempts"] == 0 for row in (await pending_rows(storage)).values()
        )
        assert await storage.filter_new_merged(pending) == []
        for merged in pending:
            await storage.processing.save_unenriched_property(merged, attempted=False)
        assert all(
            row["enrichment_attempts"] == 0 for row in (await pending_rows(storage)).values()
        )
