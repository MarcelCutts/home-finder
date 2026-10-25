"""One protected merge member must not prevent unrelated homes from progressing."""

from unittest.mock import AsyncMock

import pytest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import PropertySource
from home_finder.pipeline.assessment import process_initial_assessments
from home_finder.pipeline.contracts import CandidateBatch
from tests.test_db.test_merge_safety import snapshot


@pytest.fixture
async def batch(make_merged_property):
    async with PropertyStorage(":memory:") as storage:
        first = make_merged_property(source_id="refused-first")
        protected = make_merged_property(
            source_id="protected-second", sources=(PropertySource.ZOOPLA,)
        )
        for merged in (first, protected):
            await storage.processing.save_unenriched_property(merged)
        async with storage._transaction() as conn:
            await conn.execute(
                "UPDATE properties SET user_status='interested' WHERE unique_id=?",
                (protected.unique_id,),
            )
        group = first.model_copy(
            update={
                "sources": (*first.sources, *protected.sources),
                "source_urls": {**first.source_urls, **protected.source_urls},
            }
        )
        independent = [make_merged_property(source_id=f"independent-{i}") for i in range(2)]
        yield storage, group, protected, independent


@pytest.mark.parametrize("writer", ["save_pre_analysis_properties", "save_dropped_properties"])
async def test_refused_middle_group_rolls_back_completely_and_neighbors_commit(batch, writer):
    storage, group, protected, independent = batch
    before = await snapshot(storage)
    result = await getattr(storage.processing, writer)([independent[0], group, independent[1]], {})
    assert result.accepted == independent
    assert result.refused == {group.unique_id: {protected.unique_id: ["user_state"]}}
    # Compare every original row, including source ownership and timestamps.
    after = await snapshot(storage)
    for table, original in before.items():
        for row in original:
            assert row in after[table], table
    for merged in independent:
        saved = await storage.get_property(merged.unique_id)
        assert saved.notification_status.value == (
            "pending_analysis" if writer == "save_pre_analysis_properties" else "dropped"
        )
    assert len(await storage.processing.get_unenriched_properties()) == 2


async def test_analysis_consumes_only_accepted_groups(batch, tmp_path, monkeypatch):
    storage, group, protected, independent = batch
    settings = Settings(_env_file=None, enable_quality_filter=False, data_dir=str(tmp_path))
    lookup = AsyncMock()
    monkeypatch.setattr("home_finder.pipeline.assessment._lookup_wards", lookup)
    callback = AsyncMock()
    assessment = await process_initial_assessments(
        CandidateBatch(candidates=[independent[0], group, independent[1]]),
        settings,
        storage,
        callback,
    )
    processed, _ = assessment.processed_count, assessment.token_usage
    assert processed == 2
    assert {call.args[0].unique_id for call in callback.await_args_list} == {
        m.unique_id for m in independent
    }
    assert (
        await storage.get_property(group.unique_id)
    ).notification_status.value == "pending_enrichment"
    async with storage._read_connection() as conn:
        row = await (
            await conn.execute(
                "SELECT user_status FROM properties WHERE unique_id=?", (protected.unique_id,)
            )
        ).fetchone()
        assert row[0] == "interested"
        rows = await (
            await conn.execute("SELECT property_id FROM notification_deliveries")
        ).fetchall()
    assert {row[0] for row in rows} == {m.unique_id for m in independent}


@pytest.mark.parametrize("writer", ["save_pre_analysis_properties", "save_dropped_properties"])
async def test_stale_canonical_cannot_steal_accepted_source(batch, writer):
    storage, group, _protected, independent = batch
    owner = independent[0]
    await storage.save_merged_property(owner)
    async with storage._transaction() as conn:
        await conn.execute(
            "UPDATE source_listings SET merged_id=? WHERE unique_id=?",
            (owner.unique_id, group.unique_id),
        )
    before = await snapshot(storage)
    result = await getattr(storage.processing, writer)([group], {})
    assert result.accepted == []
    assert result.refused[group.unique_id][group.unique_id] == [
        "canonical_source_owned_by_other_home"
    ]
    assert await snapshot(storage) == before
