"""A canonical match must not erase history or partially move source ownership.

The batch writers refuse only the group that contains a protected member, so
unrelated homes in the same batch still progress.
"""

from unittest.mock import AsyncMock

import aiosqlite
import pytest
import structlog.testing
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.models import PropertyImage, PropertySource
from home_finder.pipeline.assessment import process_initial_assessments
from home_finder.pipeline.contracts import CandidateBatch
from home_finder.pipeline.matching import consolidate_candidates
from tests.support.merge_state import snapshot
from tests.support.spend import policy, spec


@pytest.fixture
async def proposed_merge(storage, make_merged_property):
    anchor = make_merged_property(source_id="anchor")
    pending = make_merged_property(sources=(PropertySource.RIGHTMOVE,), source_id="pending")
    await storage.save_merged_property(anchor)
    await storage.processing.save_unenriched_property(pending)
    await storage.upsert_source_listings([pending.canonical])
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
    "notification_deliveries": "INSERT INTO notification_deliveries (event_key,property_id,kind) "
    "VALUES ('discovery:pending',?,'discovery')",
    "processing_recovery": "UPDATE properties SET enrichment_recovery_requested_at='2026-09-01' "
    "WHERE unique_id=?",
}
# Each entry names the blocker it creates, except notified_at, which counts as delivery state.
BLOCKER = {"notified": "delivery_state"}


@pytest.mark.parametrize("history", [*HISTORY, "paid_attempts"])
async def test_entire_group_remains_unchanged_when_any_member_has_history(
    storage, proposed_merge, make_merged_property, history
):
    anchor, pending, combined = proposed_merge
    # A safe group member must not be moved before the protected one is checked.
    safe = make_merged_property(sources=(PropertySource.ZOOPLA,), source_id="safe")
    await storage.processing.save_unenriched_property(safe)
    if history == "paid_attempts":
        await storage.spend.admit(
            spec().model_copy(update={"property_id": pending.unique_id}), policy()
        )
    else:
        async with storage._transaction() as conn:
            await conn.execute(HISTORY[history], (pending.unique_id,))
    before = await snapshot(storage)
    with pytest.raises(MergeBlockedError) as error:
        await storage.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[safe.unique_id, pending.unique_id]
        )
    assert BLOCKER.get(history, history) in error.value.blockers[pending.unique_id]
    assert safe.unique_id not in error.value.blockers
    assert await snapshot(storage) == before


async def test_pending_retirement_moves_every_owned_source_and_retains_evidence(
    storage, proposed_merge, make_property
):
    anchor, pending, combined = proposed_merge
    extra = make_property(source=PropertySource.ZOOPLA, source_id="second-source")
    await storage.upsert_source_listings([extra])
    await storage.link_source_listings([(extra.unique_id, pending.unique_id)])

    assert await storage.absorb_staging_properties(
        anchor.unique_id, combined, absorbed_ids=[pending.unique_id, anchor.unique_id]
    ) == [pending.unique_id]
    after = await snapshot(storage)
    assert len(after["properties"]) == 1
    async with storage._read_connection() as conn:
        async with conn.execute("SELECT unique_id,merged_id FROM source_listings") as cur:
            memberships = dict(await cur.fetchall())
        async with conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
    assert memberships == dict.fromkeys(
        [anchor.unique_id, pending.unique_id, extra.unique_id], anchor.unique_id
    )
    # Repetition must not retire the survivor, create aliases, or lose a source.
    assert (
        await storage.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[pending.unique_id]
        )
        == []
    )
    assert len((await snapshot(storage))["source_listings"]) == 3


async def test_delete_failure_rolls_back_membership_and_projection(
    storage, proposed_merge, make_quality_analysis
):
    anchor, pending, combined = proposed_merge
    await storage.save_quality_analysis(anchor.unique_id, make_quality_analysis())
    async with storage._transaction() as conn:
        await conn.execute(
            "CREATE TRIGGER fail_retirement BEFORE DELETE ON properties "
            "BEGIN SELECT RAISE(ABORT,'simulated retirement failure'); END"
        )
    before = await snapshot(storage)
    with pytest.raises(aiosqlite.IntegrityError, match="simulated retirement failure"):
        await storage.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[pending.unique_id]
        )
    assert await snapshot(storage) == before


async def test_source_link_helper_cannot_bypass_established_history(storage, proposed_merge):
    anchor, pending, combined = proposed_merge
    await storage.save_merged_property(pending)
    async with storage._transaction() as conn:
        await conn.execute(HISTORY["established_property"], (pending.unique_id,))
    before = await snapshot(storage)
    with pytest.raises(MergeBlockedError):
        await storage.update_merged_sources(
            anchor.unique_id, combined, absorbed_ids=[pending.unique_id]
        )
    assert await snapshot(storage) == before


async def test_source_owned_by_an_unmentioned_canonical_is_not_stolen(
    storage, proposed_merge, make_property, make_merged_property
):
    anchor, _, combined = proposed_merge
    other = make_merged_property(sources=(PropertySource.ONTHEMARKET,), source_id="other")
    await storage.save_merged_property(other)
    source = make_property(source=PropertySource.ZOOPLA, source_id="owned")
    await storage.upsert_source_listings([source])
    await storage.link_source_listings([(source.unique_id, other.unique_id)])
    before = await snapshot(storage)
    with pytest.raises(MergeBlockedError, match=source.unique_id):
        await storage.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[source.unique_id]
        )
    assert await snapshot(storage) == before


async def test_standalone_cleanup_only_removes_unused_staging(storage, proposed_merge):
    _, pending, _ = proposed_merge
    await storage.delete_property(pending.unique_id)
    assert await storage.get_property(pending.unique_id) is None
    async with (
        storage._read_connection() as conn,
        conn.execute(
            "SELECT merged_id FROM source_listings WHERE unique_id=?", (pending.unique_id,)
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None and row[0] is None


async def test_standalone_cleanup_refuses_an_established_home(storage, make_merged_property):
    established = make_merged_property(source_id="established")
    await storage.save_merged_property(established)
    image = PropertyImage(
        url=HttpUrl("https://example.com/img1.jpg"),
        source=PropertySource.OPENRENT,
        image_type="gallery",
    )
    await storage.save_property_images(established.unique_id, [image])
    before = await snapshot(storage)

    with pytest.raises(MergeBlockedError):
        await storage.delete_property(established.unique_id)

    assert await snapshot(storage) == before


async def test_standalone_cleanup_of_an_unknown_id_changes_nothing(storage):
    before = await snapshot(storage)
    await storage.delete_property("nonexistent:999")
    assert await snapshot(storage) == before


async def test_unrepresented_retry_and_unrelated_retry_are_never_deleted(
    storage, proposed_merge, make_merged_property
):
    anchor, pending, _ = proposed_merge
    unrelated = make_merged_property(sources=(PropertySource.ZOOPLA,), source_id="unrelated")
    await storage.processing.save_unenriched_property(unrelated)
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [anchor]
    result = await consolidate_candidates(
        dedup, [pending], storage, {pending.unique_id, unrelated.unique_id}
    )
    assert [item.unique_id for item in result.unmatched_candidates] == [pending.unique_id]
    assert await storage.get_property(unrelated.unique_id) is not None
    assert await storage.get_property(pending.unique_id) is not None
    assert result.anchors_updated == 0


async def test_ambiguous_anchor_group_preserves_each_identity_and_new_work(
    storage, proposed_merge, make_merged_property
):
    anchor, pending, combined = proposed_merge
    other = make_merged_property(sources=(PropertySource.ZOOPLA,), source_id="other")
    await storage.save_merged_property(other)
    combined = combined.model_copy(
        update={
            "sources": (*combined.sources, *other.sources),
            "source_urls": {**combined.source_urls, **other.source_urls},
        }
    )
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [combined]
    before = (await snapshot(storage))["properties"]
    with structlog.testing.capture_logs() as captured:
        result = await consolidate_candidates(dedup, [pending], storage, {pending.unique_id})
    assert [item.unique_id for item in result.unmatched_candidates] == [pending.unique_id]
    assert result.anchors_updated == 0
    assert (await snapshot(storage))["properties"] == before
    refusal = next(event for event in captured if event["event"] == "canonical_merge_deferred")
    assert refusal["reason"] == "multiple_established_anchors"
    assert set(refusal["anchor_ids"]) == {anchor.unique_id, other.unique_id}


async def test_cross_run_refusal_retains_pending_work_and_its_message(storage, proposed_merge):
    _, pending, combined = proposed_merge
    async with storage._transaction() as conn:
        await conn.execute(HISTORY["viewing_messages"], (pending.unique_id,))
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [combined]
    before = await snapshot(storage)
    result = await consolidate_candidates(dedup, [pending], storage, {pending.unique_id})
    after = await snapshot(storage)
    assert result.anchors_updated == 0
    assert [item.unique_id for item in result.unmatched_candidates] == [pending.unique_id]
    assert after["properties"] == before["properties"]
    assert after["viewing_messages"] == before["viewing_messages"]


async def test_successful_cross_run_retains_original_cache_for_recovery(
    storage, proposed_merge, tmp_path
):
    from home_finder.utils.image_cache import get_cache_dir, save_image_bytes

    anchor, pending, combined = proposed_merge
    cache = get_cache_dir(str(tmp_path), pending.unique_id)
    path = cache / "gallery_000_abcdef12.jpg"
    save_image_bytes(path, b"retained fixture bytes")
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [combined]
    result = await consolidate_candidates(
        dedup, [pending], storage, {pending.unique_id}, data_dir=str(tmp_path)
    )
    assert result.anchors_updated == 1 and result.unmatched_candidates == []
    assert await storage.get_property(pending.unique_id) is None
    assert path.read_bytes() == b"retained fixture bytes"
    assert any(get_cache_dir(str(tmp_path), anchor.unique_id).iterdir())


async def test_rejected_proposal_never_changes_cache_files(storage, proposed_merge, tmp_path):
    from home_finder.matching.deduplication import Deduplicator
    from home_finder.utils.image_cache import get_cache_dir, save_image_bytes

    anchor, pending, _ = proposed_merge
    pending_path = get_cache_dir(str(tmp_path), pending.unique_id) / "gallery_000_abcdef12.jpg"
    save_image_bytes(pending_path, b"unaccepted source")
    await storage.mark_notified(pending.unique_id)
    before = await snapshot(storage)
    proposal = Deduplicator(data_dir=str(tmp_path))._merge_merged_properties([anchor, pending])
    with pytest.raises(MergeBlockedError):
        await storage.absorb_staging_properties(
            anchor.unique_id, proposal, absorbed_ids=[pending.unique_id]
        )
    assert await snapshot(storage) == before
    assert pending_path.read_bytes() == b"unaccepted source"
    assert not get_cache_dir(str(tmp_path), anchor.unique_id).exists()


async def test_committed_merge_repairs_cache_without_repeating_the_merge(
    storage, proposed_merge, tmp_path, monkeypatch
):
    from home_finder.pipeline.cache_recovery import repair_merged_image_caches
    from home_finder.utils.image_cache import get_cache_dir, save_image_bytes

    anchor, pending, combined = proposed_merge
    path = get_cache_dir(str(tmp_path), pending.unique_id) / "gallery_000_abcdef12.jpg"
    save_image_bytes(path, b"accepted source bytes")
    dedup = AsyncMock()
    dedup.deduplicate_merged_async.return_value = [combined]

    def interrupted(*args):
        raise OSError("interrupted after commit")

    monkeypatch.setattr("home_finder.pipeline.matching.copy_cached_images", interrupted)
    with pytest.raises(OSError, match="interrupted after commit"):
        await consolidate_candidates(
            dedup, [pending], storage, {pending.unique_id}, data_dir=str(tmp_path)
        )
    assert await storage.get_property(pending.unique_id) is None
    before = await snapshot(storage)
    assert await repair_merged_image_caches(storage, str(tmp_path)) == 1
    assert await repair_merged_image_caches(storage, str(tmp_path)) == 0
    assert await snapshot(storage) == before
    target = get_cache_dir(str(tmp_path), anchor.unique_id) / path.name
    assert target.read_bytes() == path.read_bytes() == b"accepted source bytes"


# Batch writers isolate a refused group


@pytest.fixture
async def refused_group(storage, make_merged_property):
    """A two-listing group whose second member has owner history, and two
    independent homes to save alongside it."""
    first = make_merged_property(source_id="refused-first")
    protected = make_merged_property(source_id="protected-second", sources=(PropertySource.ZOOPLA,))
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
    return storage, group, protected, independent


@pytest.mark.parametrize("writer", ["save_pre_analysis_properties", "save_dropped_properties"])
async def test_refused_middle_group_rolls_back_completely_and_neighbors_commit(
    refused_group, writer
):
    storage, group, protected, independent = refused_group
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


async def test_analysis_consumes_only_accepted_groups(refused_group, tmp_path, monkeypatch):
    storage, group, protected, independent = refused_group
    settings = Settings(
        _env_file=None,
        enable_quality_filter=False,
        database_path=str(tmp_path / "home_finder.db"),
    )
    lookup = AsyncMock()
    monkeypatch.setattr("home_finder.pipeline.assessment._lookup_wards", lookup)
    callback = AsyncMock()
    assessment = await process_initial_assessments(
        CandidateBatch(candidates=[independent[0], group, independent[1]]),
        settings,
        storage,
        callback,
    )
    assert assessment.processed_count == 2
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
async def test_stale_canonical_cannot_steal_accepted_source(refused_group, writer):
    storage, group, _protected, independent = refused_group
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
