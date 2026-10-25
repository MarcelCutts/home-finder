"""Retained anchors can recover without recreating identity or resetting money."""

from unittest.mock import AsyncMock

import pytest
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.merge_safety import MergeBlockedError, staging_blockers
from home_finder.models import PropertyImage, PropertySource
from home_finder.pipeline.assessment import process_initial_assessments
from home_finder.pipeline.candidates import prepare_candidates
from home_finder.pipeline.contracts import CollectedListings, CommuteAssessments
from home_finder.pipeline.recovery_admin import request_recovery
from home_finder.pipeline.run_lock import runner_lock
from tests.support.spend import policy, spec


async def property_row(storage, uid):
    async with storage._read_connection() as conn:
        return dict(
            await (
                await conn.execute("SELECT * FROM properties WHERE unique_id=?", (uid,))
            ).fetchone()
        )


async def ledger_rows(storage):
    async with storage._read_connection() as conn:
        return [
            tuple(row)
            for row in await (
                await conn.execute("SELECT * FROM paid_attempts ORDER BY attempt_id")
            ).fetchall()
        ]


@pytest.mark.parametrize("state", ["dropped", "failed"])
async def test_new_membership_recovers_original_anchor_after_restart(
    tmp_path, make_merged_property, monkeypatch, state
):
    path = tmp_path / "recovery.db"
    anchor = make_merged_property(source_id="retained-anchor")
    fresh = make_merged_property(source_id="new-source", sources=(PropertySource.ZOOPLA,))
    plan = PropertyImage(
        url=HttpUrl("https://example.com/floorplan.jpg"),
        source=PropertySource.ZOOPLA,
        image_type="floorplan",
        floorplan_origin="source_declared",
    )
    combined = anchor.model_copy(
        update={
            "sources": (*anchor.sources, *fresh.sources),
            "source_urls": {**anchor.source_urls, **fresh.source_urls},
            "floorplan": plan,
        }
    )
    async with PropertyStorage(str(path)) as storage:
        if state == "dropped":
            await storage.processing.save_dropped_properties([anchor], {})
        else:
            for _ in range(3):
                await storage.processing.save_unenriched_property(anchor)
            await storage.processing.expire_unenriched()
        async with storage._transaction() as conn:
            await conn.execute(
                "UPDATE properties SET user_status='interested', analysis_attempts=1 "
                "WHERE unique_id=?",
                (anchor.unique_id,),
            )
        attempt = spec().model_copy(update={"property_id": anchor.unique_id})
        await storage.spend.admit(attempt, policy())
        before = await property_row(storage, anchor.unique_id)
        money = await ledger_rows(storage)
        await storage.processing.save_unenriched_property(fresh, attempted=False)
        await storage.absorb_staging_properties(
            anchor.unique_id, combined, absorbed_ids=[fresh.unique_id]
        )
        assert await storage.get_property(fresh.unique_id) is None
        queued = await property_row(storage, anchor.unique_id)
        assert queued["enrichment_recovery_requested_at"]
        assert queued["enrichment_recovery_reason"] == "accepted_new_source"
        assert queued["enrichment_attempts"] == before["enrichment_attempts"]
        assert queued["analysis_attempts"] == 1
        assert queued["user_status"] == "interested"
        assert await storage.get_property_images(anchor.unique_id) == [plan]
        assert await ledger_rows(storage) == money
        # Replaying the same accepted membership cannot renew the grant.
        await storage.update_merged_sources(
            anchor.unique_id, combined, absorbed_ids=[fresh.unique_id]
        )
        assert (await property_row(storage, anchor.unique_id))[
            "enrichment_recovery_requested_at"
        ] == queued["enrichment_recovery_requested_at"]

    settings = Settings(
        _env_file=None,
        database_path=str(path),
        data_dir=str(tmp_path),
        search_areas="e8",
        require_floorplan=True,
        enable_quality_filter=False,
    )
    monkeypatch.setattr(
        "home_finder.pipeline.candidates.collect_listings",
        AsyncMock(return_value=CollectedListings()),
    )
    enrichment = AsyncMock(
        side_effect=lambda merged, *args, **kwargs: [
            m.model_copy(update={"floorplan": plan}) for m in merged
        ]
    )
    monkeypatch.setattr("home_finder.pipeline.candidates._enrich_candidates", enrichment)
    monkeypatch.setattr(
        "home_finder.pipeline.candidates._assess_commutes",
        AsyncMock(side_effect=lambda merged, *args, **kwargs: CommuteAssessments(merged)),
    )
    monkeypatch.setattr("home_finder.pipeline.assessment._lookup_wards", AsyncMock())
    async with PropertyStorage(str(path)) as storage:
        assert [m.unique_id for m in await storage.get_recent_properties_for_dedup()] == [
            anchor.unique_id
        ]
        batch = await prepare_candidates(settings, storage)
        assert batch is not None
        assert [m.unique_id for m in batch.candidates] == [anchor.unique_id]
        assert batch.candidates[0].floorplan == plan
        callback = AsyncMock()
        assessment = await process_initial_assessments(batch, settings, storage, callback)
        count, _ = assessment.processed_count, assessment.token_usage
        assert count == 1
        after = await property_row(storage, anchor.unique_id)
        assert after["enrichment_recovery_requested_at"] is None
        assert after["enrichment_attempts"] == before["enrichment_attempts"]
        assert after["analysis_attempts"] == 2  # normal completion, never a reset
        assert after["user_status"] == "interested"
        assert await ledger_rows(storage) == money


@pytest.mark.parametrize("previous_attempts", [0, 3])
async def test_failed_recovery_consumes_grant_and_does_not_requeue_same_membership(
    tmp_path, make_merged_property, previous_attempts
):
    async with PropertyStorage(str(tmp_path / "retry.db")) as storage:
        anchor = make_merged_property()
        if previous_attempts == 0:
            await storage.processing.save_dropped_properties([anchor], {})
        else:
            for _ in range(previous_attempts):
                await storage.processing.save_unenriched_property(anchor)
            await storage.processing.expire_unenriched()
        assert await storage.processing.request_enrichment_recovery(
            [anchor.unique_id], reason="fixed portal parser"
        ) == {anchor.unique_id: "requested"}
        assert (
            await storage.processing.expire_unenriched() == 0
        )  # waiting grant survives unrelated expiry
        assert len(await storage.processing.get_unenriched_properties()) == 1
        async with storage._read_connection() as conn:
            assert (
                "processing_recovery"
                in (await staging_blockers(conn, [anchor.unique_id]))[anchor.unique_id]
            )
        await storage.processing.save_unenriched_property(anchor)
        assert await storage.processing.expire_unenriched() == 0
        row = await property_row(storage, anchor.unique_id)
        assert row["enrichment_attempts"] == previous_attempts + 1
        assert row["enrichment_status"] == "failed"
        assert row["enrichment_recovery_requested_at"] is None
        assert await storage.processing.get_unenriched_properties() == []
        await storage.update_merged_sources(anchor.unique_id, anchor)
        assert await storage.processing.get_unenriched_properties() == []


async def test_recovery_action_is_bounded_idempotent_and_fenced(tmp_path, make_merged_property):
    path = tmp_path / "admin.db"
    dropped = make_merged_property()
    exhausted = make_merged_property()
    async with PropertyStorage(str(path)) as storage:
        await storage.processing.save_dropped_properties([dropped, exhausted], {})
        await storage.processing.mark_analysis_exhausted(
            exhausted.unique_id, "paid_operation_attempt_limit"
        )
    with runner_lock(str(path)) as owned:
        assert owned
        with pytest.raises(ValueError, match="Stop the active runner"):
            await request_recovery(path, [dropped.unique_id], reason="source now has a floorplan")
    result = await request_recovery(
        path,
        [dropped.unique_id, exhausted.unique_id, "missing"],
        reason="source now has a floorplan",
    )
    assert result == {
        dropped.unique_id: "requested",
        exhausted.unique_id: "analysis_exhausted",
        "missing": "not_found",
    }
    assert await request_recovery(path, [dropped.unique_id], reason="repeat") == {
        dropped.unique_id: "already_requested"
    }
    with pytest.raises(ValueError, match="between 1 and 20"):
        await request_recovery(path, [str(i) for i in range(21)], reason="too broad")
    async with PropertyStorage(str(path)) as storage:
        assert (await property_row(storage, dropped.unique_id))["enrichment_attempts"] == 0
        assert (await property_row(storage, exhausted.unique_id))[
            "analysis_exhausted_reason"
        ] == "paid_operation_attempt_limit"


async def test_recovery_anchor_cannot_be_retired_as_new_staging(tmp_path, make_merged_property):
    async with PropertyStorage(str(tmp_path / "identity.db")) as storage:
        anchor = make_merged_property()
        other = make_merged_property(sources=(PropertySource.ZOOPLA,))
        await storage.processing.save_dropped_properties([anchor], {})
        await storage.save_merged_property(other)
        await storage.processing.request_enrichment_recovery(
            [anchor.unique_id], reason="new details"
        )
        combined = other.model_copy(
            update={
                "sources": (*other.sources, *anchor.sources),
                "source_urls": {**other.source_urls, **anchor.source_urls},
            }
        )
        with pytest.raises(MergeBlockedError) as error:
            await storage.absorb_staging_properties(
                other.unique_id, combined, absorbed_ids=[anchor.unique_id]
            )
        assert "processing_recovery" in error.value.blockers[anchor.unique_id]
        assert await storage.get_property(anchor.unique_id) is not None
