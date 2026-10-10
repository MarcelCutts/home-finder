"""Input currency and rebuild acknowledgement survive refresh, regrouping and failure."""

import asyncio
from unittest.mock import patch

import aiosqlite
import pytest

from home_finder.db import PropertyStorage
from home_finder.db.home_projections import ProjectionConflict
from home_finder.models import PropertySource, UserStatus
from tests.test_db.test_membership import REVIEW, rows, snapshot
from tests.test_db.test_observations import observation, value


@pytest.fixture
async def homes(tmp_path, make_property):
    async with PropertyStorage(str(tmp_path / "projection.db")) as db:
        props = [
            make_property(
                source=PropertySource.RIGHTMOVE,
                source_id=str(identifier),
                url=f"https://www.rightmove.co.uk/properties/{identifier}",
                price_pcm=1800 + i * 100,
                description=f"Advert {identifier}: gas hob",
            )
            for i, identifier in enumerate((123, 456, 789))
        ]
        for prop in props:
            await db.save_property(prop)
        targets = [await db.homes.resolve(p.unique_id) for p in props]
        yield db, props, targets


async def test_inspection_is_read_only_and_rebuild_retry_restart_are_idempotent(homes):
    db, props, targets = homes
    home = targets[0].home_id
    original = await snapshot(db)
    preview = await db.projections.inspect(home)
    assert preview.listings[0].member.source_unique_id == props[0].unique_id
    assert await snapshot(db) == original
    assert await db.projections.current(home) is None
    built = await db.projections.rebuild(home, expected_generation=preview.input_generation)
    assert built.snapshot_id == preview.sha256
    assert built.membership_invalidations_acknowledged == 1
    completed = await snapshot(db)
    retry = await db.projections.rebuild(home)
    assert retry.replayed and retry.membership_invalidations_acknowledged == 0
    assert await snapshot(db) == completed
    await db.close()
    await db.initialize()
    assert await db.projections.current(home) == preview
    assert await db.projections.get(built.snapshot_id) == preview
    assert await snapshot(db) == completed


@pytest.mark.parametrize(
    ("column", "changed", "material_changed"),
    [
        ("price_pcm", 1700, True),
        ("description", "Confirmed induction hob", True),
        ("is_off_market", 1, True),
        ("last_seen", "2026-10-01T12:00:00+00:00", False),
        ("last_checked_at", "2026-10-01T12:00:00+00:00", False),
    ],
)
async def test_source_changes_invalidate_only_the_affected_home(
    homes, column, changed, material_changed
):
    db, props, targets = homes
    await db.projections.rebuild_pending()
    before = [await db.projections.current(t.home_id) for t in targets]
    async with db._transaction() as conn:
        await conn.execute(
            f"UPDATE source_listings SET {column} = ? WHERE unique_id = ?",
            (changed, props[0].unique_id),
        )
    assert await db.projections.current(targets[0].home_id) is None
    assert await db.projections.current(targets[1].home_id) == before[1]
    fresh = await db.projections.inspect(targets[0].home_id)
    assert fresh.input_generation > before[0].input_generation
    assert (fresh.listing_material_sha256 != before[0].listing_material_sha256) == material_changed
    async with db._transaction() as conn:
        await conn.execute(
            f"UPDATE source_listings SET {column} = ? WHERE unique_id = ?",
            (changed, props[0].unique_id),
        )
    assert await db.projections.inspect(targets[0].home_id) == fresh
    await db.update_user_status(targets[0].home_id, UserStatus.INTERESTED)
    assert await db.projections.inspect(targets[0].home_id) == fresh


async def test_new_receipts_change_evidence_generation_without_changing_identical_material(homes):
    db, _, targets = homes
    first = observation("one", description=value("Captured gas hob"), price_pcm=value(1800))
    await db.observations.ingest([first])
    initial = await db.projections.rebuild(targets[0].home_id)
    current = await db.projections.current(targets[0].home_id)
    assert current.listings[0].member.text.description == "Captured gas hob"
    assert current.listings[0].observed.fields["price_pcm"].observation_ids == (first.sha256,)
    before = await snapshot(db)
    await db.observations.ingest([first])
    assert await snapshot(db) == before
    second = observation(
        "two", minute=1, description=value("Captured gas hob"), price_pcm=value(1800)
    )
    await db.observations.ingest([second])
    assert await db.projections.current(targets[0].home_id) is None
    refreshed = await db.projections.rebuild(targets[0].home_id)
    assert refreshed.snapshot_id != initial.snapshot_id
    assert refreshed.listing_material_sha256 == initial.listing_material_sha256
    assert (await db.projections.get(initial.snapshot_id)) == current


async def test_conflicting_alternatives_and_explicit_clear_have_distinct_material(homes):
    db, _, targets = homes

    async def conflict(prefix, minute, prices):
        await db.observations.ingest(
            [
                observation(f"{prefix}-{i}", minute=minute, price_pcm=value(price))
                for i, price in enumerate(prices)
            ]
        )
        return await db.projections.inspect(targets[0].home_id)

    first = await conflict("first", 0, (1800, 1900))
    second = await conflict("second", 1, (2000, 2100))
    assert first.listings[0].observed.fields["price_pcm"].state == "conflicting"
    assert second.listings[0].observed.fields["price_pcm"].value is None
    assert first.listing_material_sha256 != second.listing_material_sha256
    await db.observations.ingest(
        [observation("clear", minute=2, origin="correction", description={"state": "cleared"})]
    )
    cleared = await db.projections.inspect(targets[0].home_id)
    assert cleared.listings[0].member.text.description == ""
    assert cleared.listing_material_sha256 != second.listing_material_sha256


@pytest.mark.parametrize("operation", ["split", "reversal"])
async def test_merge_and_split_or_reversal_rebuild_all_members_and_retain_old_inputs(
    homes, operation
):
    db, props, targets = homes
    await db.projections.rebuild_pending()
    original = await db.projections.current(targets[0].home_id)
    merge = await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)
    await db.memberships.apply(merge, allow_incomplete_projections=True)
    pending = await db.projections.status(pending_only=True)
    assert {p.home_id for p in pending} == {t.home_id for t in targets[:2]}
    await db.projections.rebuild_pending()
    grouped = await db.projections.current(targets[0].home_id)
    donor = await db.projections.current(targets[1].home_id)
    assert {m.member.source_unique_id for m in grouped.listings} == {p.unique_id for p in props[:2]}
    assert donor.listings == () and donor.route.target_home_id == targets[0].home_id
    decision = (
        await db.memberships.prepare_reversal(merge.decision_id, **REVIEW)
        if operation == "reversal"
        else await db.memberships.prepare_split(
            targets[0].home_id, {props[1].unique_id: targets[1].home_id}, **REVIEW
        )
    )
    await db.memberships.apply(decision, allow_incomplete_projections=True)
    await db.projections.rebuild_pending()
    restored = await db.projections.current(targets[0].home_id)
    assert restored.listing_material_sha256 == original.listing_material_sha256
    assert restored.input_generation > grouped.input_generation
    assert len(restored.listings) == 1
    assert await db.projections.get(grouped.sha256) == grouped
    before = await snapshot(db)
    assert (await db.memberships.apply(merge, allow_incomplete_projections=True)).replayed
    assert await snapshot(db) == before
    assert not await db.projections.status(pending_only=True)


@pytest.mark.parametrize(
    "table",
    [
        "home_listing_projection_snapshots",
        "home_listing_projection_heads",
        "membership_projection_consumptions",
    ],
)
async def test_rebuild_failure_rolls_back_snapshot_head_and_acknowledgement(homes, table):
    db, _, targets = homes
    async with db._transaction() as conn:
        await conn.execute(
            f"CREATE TEMP TRIGGER fail_rebuild BEFORE INSERT ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'injected rebuild failure'); END"
        )
    before = await snapshot(db)
    with pytest.raises(aiosqlite.IntegrityError, match="injected rebuild failure"):
        await db.projections.rebuild(targets[0].home_id)
    assert await snapshot(db) == before
    assert await db.projections.current(targets[0].home_id) is None


async def test_cancelled_rebuild_cannot_acknowledge_uncommitted_work(homes):
    db, _, targets = homes
    conn = await db._get_connection()
    execute = conn.execute

    async def cancel_at_head(sql, *args, **kwargs):
        if sql.startswith("UPDATE home_listing_projection_heads"):
            raise asyncio.CancelledError
        return await execute(sql, *args, **kwargs)

    before = await snapshot(db)

    # Wrap only the awaited write statements; read context-manager behavior remains intact.
    def intercept(sql, *args, **kwargs):
        return (
            cancel_at_head(sql, *args, **kwargs)
            if sql.startswith("UPDATE home_listing_projection_heads")
            else execute(sql, *args, **kwargs)
        )

    with patch.object(conn, "execute", intercept), pytest.raises(asyncio.CancelledError):
        await db.projections.rebuild(targets[0].home_id)
    assert await snapshot(db) == before


async def test_stale_review_refuses_rebuild_without_consuming_invalidations(homes):
    db, props, targets = homes
    previous = await db.projections.inspect(targets[0].home_id)
    await db.detect_and_record_price_change(props[0].unique_id, 1750)
    before = await snapshot(db)
    with pytest.raises(ProjectionConflict, match="stale_home_input_generation"):
        await db.projections.rebuild(
            targets[0].home_id, expected_generation=previous.input_generation
        )
    assert await snapshot(db) == before


async def test_external_refresh_after_rebuild_cannot_be_acknowledged_by_the_old_snapshot(homes):
    from home_finder.db import home_projections

    db, props, targets = homes
    async with PropertyStorage(db.db_path) as writer:
        ready, release = asyncio.Event(), asyncio.Event()
        original = home_projections.listing_projection

        async def hold_after_read(*args):
            result = await original(*args)
            ready.set()
            await release.wait()
            return result

        with patch.object(home_projections, "listing_projection", hold_after_read):
            rebuilding = asyncio.create_task(db.projections.rebuild(targets[0].home_id))
            await ready.wait()
            updating = asyncio.create_task(
                writer.detect_and_record_price_change(props[0].unique_id, 1700)
            )
            try:
                done, _ = await asyncio.wait({updating}, timeout=0.05)
                assert not done
            finally:
                release.set()
            built = await rebuilding
            await updating
    assert await db.projections.current(targets[0].home_id) is None
    assert (await db.projections.get(built.snapshot_id)).listings[0].member.price_pcm == 1800
    await db.projections.rebuild(targets[0].home_id)
    assert (await db.projections.current(targets[0].home_id)).listings[0].member.price_pcm == 1700


async def test_rebuild_batch_is_bounded_and_preserves_remaining_work(homes):
    db, _, _ = homes
    assert len(await db.projections.rebuild_pending(limit=2)) == 2
    assert len(await db.projections.status(pending_only=True)) == 1
    assert len(await db.projections.rebuild_pending(limit=2)) == 1
    assert await db.projections.rebuild_pending() == []
    assert len(await rows(db, "home_listing_projection_snapshots")) == 3


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE home_listing_projection_snapshots SET created_at = 'rewritten'",
        "DELETE FROM home_listing_projection_snapshots",
        "UPDATE membership_projection_consumptions SET consumer = 'other'",
        "DELETE FROM membership_projection_consumptions",
        "UPDATE home_projection_generations SET input_generation = input_generation - 1",
        "DELETE FROM home_projection_generations",
        "DELETE FROM home_listing_projection_heads",
        *[
            f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"
            for table in (
                "home_projection_generations",
                "home_listing_projection_snapshots",
                "home_listing_projection_heads",
                "membership_projection_consumptions",
            )
        ],
    ],
)
async def test_sql_cannot_erase_or_replace_projection_history(homes, sql):
    db, _, targets = homes
    await db.projections.rebuild(targets[0].home_id)
    before = await snapshot(db)
    with pytest.raises(aiosqlite.IntegrityError):
        async with db._transaction() as conn:
            await conn.execute(sql)
    assert await snapshot(db) == before


async def test_cross_home_head_and_stale_invalidation_ack_are_refused(homes):
    db, _, targets = homes
    first, second = targets[:2]
    one = await db.projections.rebuild(first.home_id)
    two = await db.projections.rebuild(second.home_id)
    with pytest.raises(aiosqlite.IntegrityError, match="current home inputs"):
        async with db._transaction() as conn:
            await conn.execute(
                "UPDATE home_listing_projection_heads SET snapshot_id = ? WHERE home_id = ?",
                (two.snapshot_id, first.home_id),
            )
    merge = await db.memberships.prepare_merge(first.home_id, (second.home_id,), **REVIEW)
    await db.memberships.apply(merge, allow_incomplete_projections=True)
    before = await snapshot(db)
    with pytest.raises(aiosqlite.IntegrityError, match="requires current projection"):
        async with db._transaction() as conn:
            await conn.execute(
                "INSERT INTO membership_projection_consumptions VALUES (?, ?, 'listing_inputs', ?)",
                (merge.decision_id, first.home_id, one.snapshot_id),
            )
    with pytest.raises(aiosqlite.IntegrityError, match="current home inputs"):
        async with db._transaction() as conn:
            await conn.execute(
                "UPDATE home_listing_projection_heads SET snapshot_id = ? WHERE home_id = ?",
                (one.snapshot_id, first.home_id),
            )
    assert await snapshot(db) == before
    assert await db.projections.current(first.home_id) is None


async def test_rebuild_detects_bypassed_invalidation_instead_of_reusing_wrong_snapshot(homes):
    db, props, targets = homes
    await db.projections.rebuild(targets[0].home_id)
    async with db._transaction() as conn:
        await conn.execute("DROP TRIGGER source_projection_changed")
        await conn.execute(
            "UPDATE source_listings SET price_pcm = 1700 WHERE unique_id = ?",
            (props[0].unique_id,),
        )
    before = await snapshot(db)
    with pytest.raises(ProjectionConflict, match="inputs_changed_without_invalidation"):
        await db.projections.rebuild(targets[0].home_id)
    assert await snapshot(db) == before


async def test_stored_checksum_corruption_is_not_returned_as_current_evidence(homes):
    db, _, targets = homes
    built = await db.projections.rebuild(targets[0].home_id)
    async with db._transaction() as conn:
        await conn.execute("DROP TRIGGER immutable_home_listing_projection_snapshots_update")
        await conn.execute(
            "UPDATE home_listing_projection_snapshots SET listing_material_sha256 = ?",
            ("0" * 64,),
        )
    with pytest.raises(ProjectionConflict, match="stored_projection_checksum_mismatch"):
        await db.projections.current(targets[0].home_id)
    with pytest.raises(ProjectionConflict, match="stored_projection_checksum_mismatch"):
        await db.projections.get(built.snapshot_id)


async def test_projection_bound_failure_leaves_work_pending(homes, monkeypatch):
    db, _, targets = homes
    monkeypatch.setattr("home_finder.db.home_projections.MAX_HOME_PROJECTION_BYTES", 1)
    before = await snapshot(db)
    with pytest.raises(ValueError, match="review bound"):
        await db.projections.rebuild(targets[0].home_id)
    assert await snapshot(db) == before


async def test_observations_received_before_home_linking_are_in_the_first_snapshot(
    tmp_path, make_property
):
    async with PropertyStorage(str(tmp_path / "unowned.db")) as db:
        receipt = observation("unowned", description=value("Captured before linking"))
        await db.observations.ingest([receipt])
        assert await db.projections.status() == []
        prop = make_property(
            source=PropertySource.RIGHTMOVE,
            source_id="123",
            url="https://www.rightmove.co.uk/properties/123",
        )
        await db.save_property(prop)
        home = await db.homes.resolve(prop.unique_id)
        await db.projections.rebuild(home.home_id)
        current = await db.projections.current(home.home_id)
        assert current.listings[0].member.text.description == "Captured before linking"
        assert current.listings[0].observed.fields["description"].observation_ids == (
            receipt.sha256,
        )
