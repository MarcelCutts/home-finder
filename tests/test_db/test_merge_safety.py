"""A canonical match must not erase history or partially move source ownership."""

from unittest.mock import AsyncMock

import aiosqlite
import pytest
import structlog.testing

from home_finder.db import PropertyStorage
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.models import PropertySource
from home_finder.pipeline.stages import _cross_run_deduplicate
from tests.test_db.test_observations import observation
from tests.test_db.test_spend import policy, spec

TABLES = (
    "properties",
    "source_listings",
    "source_observations",
    "source_observation_fields",
    "source_observation_baselines",
    "property_images",
    "quality_analyses",
    "price_history",
    "status_events",
    "viewing_messages",
    "enquiry_log",
    "source_aliases",
    "paid_attempts",
)


@pytest.fixture
async def in_memory_storage():
    async with PropertyStorage(":memory:") as storage:
        yield storage


async def snapshot(storage):
    result = {}
    async with storage._read_connection() as conn:
        for table in TABLES:
            async with conn.execute(f"SELECT * FROM {table} ORDER BY 1") as cur:
                result[table] = [tuple(row) for row in await cur.fetchall()]
    return result


@pytest.fixture
async def proposed_merge(in_memory_storage, make_merged_property):
    anchor = make_merged_property(source_id="anchor")
    pending = make_merged_property(sources=(PropertySource.RIGHTMOVE,), source_id="pending")
    await in_memory_storage.save_merged_property(anchor)
    await in_memory_storage.pipeline.save_unenriched_property(pending)
    evidence = observation(
        "merge-evidence", description={"value": "Source words survive membership changes"}
    ).model_copy(
        update={"source_id": pending.canonical.source_id, "listing_url": str(pending.canonical.url)}
    )
    receipt = await in_memory_storage.observations.ingest([evidence])
    assert receipt.inserted == 1
    # Start with the observation projected, as it is before production dedup.
    await in_memory_storage.upsert_source_listings([pending.canonical])
    combined = anchor.model_copy(
        update={
            "sources": (*anchor.sources, *pending.sources),
            "source_urls": {**anchor.source_urls, **pending.source_urls},
        }
    )
    return anchor, pending, combined


HISTORY = {
    "established_property": "UPDATE properties SET enrichment_status='enriched' WHERE unique_id=?",
    "delivery_state": "UPDATE properties SET notification_status='sent' WHERE unique_id=?",
    "notified": "UPDATE properties SET notified_at='2026-09-01' WHERE unique_id=?",
    "user_state": "UPDATE properties SET user_status='interested' WHERE unique_id=?",
    "analysis_attempts": "UPDATE properties SET analysis_attempts=1 WHERE unique_id=?",
    "property_images": "INSERT INTO property_images (property_unique_id,source,url,image_type) "
    "VALUES (?,'rightmove','https://example.com/img','gallery')",
    "quality_analyses": "INSERT INTO quality_analyses (property_unique_id,analysis_json) "
    "VALUES (?,'{}')",
    "price_history": "INSERT INTO price_history "
    "(property_unique_id,old_price,new_price,change_amount) VALUES (?,2000,1900,-100)",
    "status_events": "INSERT INTO status_events (property_unique_id,to_status,note) "
    "VALUES (?,'new','Preserve this note even after returning to new')",
    "viewing_messages": "INSERT INTO viewing_messages (property_unique_id,message) "
    "VALUES (?,'An independently saved message')",
    "enquiry_log": "INSERT INTO enquiry_log (property_unique_id,portal,message) "
    "VALUES (?,'rightmove','Already sent')",
    "source_aliases": "INSERT INTO source_aliases (anchor_id,unique_id,source,source_id) "
    "VALUES (?,'legacy:1','rightmove','1')",
}


@pytest.mark.parametrize("history", [*HISTORY, "paid_attempts"])
async def test_entire_group_remains_unchanged_when_any_member_has_history(
    in_memory_storage, proposed_merge, make_merged_property, history
):
    db = in_memory_storage
    anchor, pending, combined = proposed_merge
    # A safe group member must not be moved before the protected one is checked.
    safe = make_merged_property(sources=(PropertySource.ZOOPLA,), source_id="safe")
    await db.pipeline.save_unenriched_property(safe)
    if history == "paid_attempts":
        await db.spend.admit(spec().model_copy(update={"property_id": pending.unique_id}), policy())
    else:
        async with db._transaction() as conn:
            await conn.execute(HISTORY[history], (pending.unique_id,))
    before = await snapshot(db)
    with pytest.raises(MergeBlockedError) as error:
        await db.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[safe.unique_id, pending.unique_id]
        )
    assert pending.unique_id in error.value.blockers
    assert await snapshot(db) == before


async def test_pending_retirement_moves_every_owned_source_and_retains_evidence(
    in_memory_storage, proposed_merge, make_property
):
    db = in_memory_storage
    anchor, pending, combined = proposed_merge
    extra = make_property(source=PropertySource.ZOOPLA, source_id="second-source")
    await db.upsert_source_listings([extra])
    await db.link_source_listings([(extra.unique_id, pending.unique_id)])
    observations = (await snapshot(db))["source_observations"]
    assert len(observations) == 1

    assert await db.absorb_staging_properties(
        anchor.unique_id, combined, absorbed_ids=[pending.unique_id, anchor.unique_id]
    ) == [pending.unique_id]
    after = await snapshot(db)
    assert len(after["properties"]) == 2  # Retained donor supports reversal.
    assert len(await db.get_all_properties()) == 1
    assert after["source_observations"] == observations
    async with db._read_connection() as conn:
        async with conn.execute("SELECT unique_id,merged_id FROM source_listings") as cur:
            memberships = dict(await cur.fetchall())
        async with conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
    assert memberships == dict.fromkeys(
        [anchor.unique_id, pending.unique_id, extra.unique_id], anchor.unique_id
    )
    # Repetition must not retire the survivor, create aliases, or lose a source.
    assert (
        await db.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[pending.unique_id]
        )
        == []
    )
    assert len((await snapshot(db))["source_listings"]) == 3


async def test_delete_failure_rolls_back_membership_and_projection(
    in_memory_storage, proposed_merge
):
    db = in_memory_storage
    anchor, pending, combined = proposed_merge
    async with db._transaction() as conn:
        await conn.execute(
            "CREATE TRIGGER fail_retirement BEFORE UPDATE ON home_routes "
            "BEGIN SELECT RAISE(ABORT,'simulated retirement failure'); END"
        )
    before = await snapshot(db)
    with pytest.raises(aiosqlite.IntegrityError, match="simulated retirement failure"):
        await db.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[pending.unique_id]
        )
    assert await snapshot(db) == before


async def test_source_link_helper_cannot_bypass_established_history(
    in_memory_storage, proposed_merge
):
    db = in_memory_storage
    anchor, pending, combined = proposed_merge
    await db.save_merged_property(pending)
    async with db._transaction() as conn:
        await conn.execute(HISTORY["established_property"], (pending.unique_id,))
    before = await snapshot(db)
    with pytest.raises(MergeBlockedError):
        await db.update_merged_sources(anchor.unique_id, combined, absorbed_ids=[pending.unique_id])
    assert await snapshot(db) == before


async def test_source_owned_by_an_unmentioned_canonical_is_not_stolen(
    in_memory_storage, proposed_merge, make_property, make_merged_property
):
    db = in_memory_storage
    anchor, _, combined = proposed_merge
    other = make_merged_property(sources=(PropertySource.ONTHEMARKET,), source_id="other")
    await db.save_merged_property(other)
    source = make_property(source=PropertySource.ZOOPLA, source_id="owned")
    await db.upsert_source_listings([source])
    await db.link_source_listings([(source.unique_id, other.unique_id)])
    before = await snapshot(db)
    with pytest.raises(MergeBlockedError, match=source.unique_id):
        await db.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[source.unique_id]
        )
    assert await snapshot(db) == before


async def test_standalone_cleanup_only_removes_unused_staging(in_memory_storage, proposed_merge):
    db = in_memory_storage
    _, pending, _ = proposed_merge
    await db.delete_property(pending.unique_id)
    assert await db.get_property(pending.unique_id) is None
    async with (
        db._read_connection() as conn,
        conn.execute(
            "SELECT merged_id FROM source_listings WHERE unique_id=?", (pending.unique_id,)
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None and row[0] is None


async def test_unrepresented_retry_and_unrelated_retry_are_never_deleted(
    in_memory_storage, proposed_merge, make_merged_property
):
    db = in_memory_storage
    anchor, pending, _ = proposed_merge
    unrelated = make_merged_property(sources=(PropertySource.ZOOPLA,), source_id="unrelated")
    await db.pipeline.save_unenriched_property(unrelated)
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [anchor]
    result = await _cross_run_deduplicate(
        dedup, [pending], db, {pending.unique_id, unrelated.unique_id}
    )
    assert [item.unique_id for item in result.genuinely_new] == [pending.unique_id]
    assert await db.get_property(unrelated.unique_id) is not None
    assert await db.get_property(pending.unique_id) is not None
    assert result.anchors_updated == 0


async def test_ambiguous_anchor_group_preserves_each_identity_and_new_work(
    in_memory_storage, proposed_merge, make_merged_property
):
    db = in_memory_storage
    anchor, pending, combined = proposed_merge
    other = make_merged_property(sources=(PropertySource.ZOOPLA,), source_id="other")
    await db.save_merged_property(other)
    combined = combined.model_copy(
        update={
            "sources": (*combined.sources, *other.sources),
            "source_urls": {**combined.source_urls, **other.source_urls},
        }
    )
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [combined]
    before = (await snapshot(db))["properties"]
    with structlog.testing.capture_logs() as captured:
        result = await _cross_run_deduplicate(dedup, [pending], db, {pending.unique_id})
    assert [item.unique_id for item in result.genuinely_new] == [pending.unique_id]
    assert result.anchors_updated == 0
    assert (await snapshot(db))["properties"] == before
    refusal = next(event for event in captured if event["event"] == "canonical_merge_deferred")
    assert refusal["reason"] == "multiple_established_anchors"
    assert set(refusal["anchor_ids"]) == {anchor.unique_id, other.unique_id}


async def test_cross_run_refusal_retains_pending_work_and_its_message(
    in_memory_storage, proposed_merge
):
    db = in_memory_storage
    _, pending, combined = proposed_merge
    async with db._transaction() as conn:
        await conn.execute(HISTORY["viewing_messages"], (pending.unique_id,))
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [combined]
    before = await snapshot(db)
    result = await _cross_run_deduplicate(dedup, [pending], db, {pending.unique_id})
    after = await snapshot(db)
    assert result.anchors_updated == 0
    assert [item.unique_id for item in result.genuinely_new] == [pending.unique_id]
    assert after["properties"] == before["properties"]
    assert after["viewing_messages"] == before["viewing_messages"]


async def test_successful_cross_run_retains_original_cache_for_recovery(
    in_memory_storage, proposed_merge, tmp_path
):
    from home_finder.utils.image_cache import get_cache_dir, save_image_bytes

    db = in_memory_storage
    anchor, pending, combined = proposed_merge
    cache = get_cache_dir(str(tmp_path), pending.unique_id)
    path = cache / "gallery_000_abcdef12.jpg"
    save_image_bytes(path, b"retained fixture bytes")
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [combined]
    result = await _cross_run_deduplicate(
        dedup, [pending], db, {pending.unique_id}, data_dir=str(tmp_path)
    )
    assert result.anchors_updated == 1 and result.genuinely_new == []
    assert await db.get_property(pending.unique_id) is None
    assert path.read_bytes() == b"retained fixture bytes"
    assert any(get_cache_dir(str(tmp_path), anchor.unique_id).iterdir())
