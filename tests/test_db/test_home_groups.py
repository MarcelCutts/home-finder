"""Groups affect display/work; original home identities own personal history."""

import json
from unittest.mock import patch

import aiosqlite
import pytest

from home_finder.db.home_groups import HomeActionConflict, resolve_action
from home_finder.db.membership_ops import MembershipConflict, write_membership_decision
from home_finder.db.migrations import MIGRATIONS, run_migrations
from home_finder.db.spend import SpendDeferred
from home_finder.models import UserStatus
from home_finder.models.membership import MembershipDecision
from tests.test_db import test_membership as membership_fixtures
from tests.test_db.test_membership import REVIEW, merged, rows, snapshot
from tests.test_db.test_spend import policy, spec

homes = membership_fixtures.homes


async def test_chained_merges_and_representative_change_flatten_every_issued_link(homes):
    db, props, targets = homes
    a, b, c = [t.home_id for t in targets]
    first = await db.memberships.prepare_merge(a, (b,), **REVIEW)
    await db.memberships.apply(first, allow_incomplete_projections=True)
    second = await db.memberships.prepare_merge(c, (b,), **REVIEW)
    assert second.before.scope_home_ids == tuple(sorted((a, b, c)))
    await db.memberships.apply(second, allow_incomplete_projections=True)
    for p, t in zip(props, targets, strict=True):
        assert await db.homes.resolve(p.unique_id) == targets[2]
        assert await db.homes.resolve(t.home_id) == targets[2]
    # An existing group may deliberately choose another retained representative.
    reanchor = await db.memberships.prepare_merge(b, (c,), **REVIEW)
    await db.memberships.apply(reanchor, allow_incomplete_projections=True)
    assert len(await db.get_all_properties()) == 1
    assert await db.homes.resolve(a) == targets[1]
    with pytest.raises(MembershipConflict, match="later_decision"):
        await db.memberships.prepare_reversal(second.decision_id, **REVIEW)
    await db.memberships.apply(
        await db.memberships.prepare_reversal(reanchor.decision_id, **REVIEW),
        allow_incomplete_projections=True,
    )
    assert await db.homes.resolve(a) == targets[2]
    assert (await db.memberships.audit()).valid


async def test_personal_actions_never_follow_group_representative(homes):
    db, props, targets = homes
    await db.save_viewing_message(targets[1].home_id, "Original B message")
    await db.memberships.apply(
        await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW),
        allow_incomplete_projections=True,
    )
    await db.update_user_status(targets[1].home_id, UserStatus.ARCHIVED)
    assert len(await db.get_status_history(props[1].unique_id)) == 1
    assert not await db.get_status_history(props[0].unique_id)
    assert await db.get_viewing_message(targets[1].home_id) == "Original B message"
    assert await db.get_viewing_message(targets[0].home_id) is None
    await db.delete_viewing_message(props[1].unique_id)
    assert await db.get_viewing_message(targets[1].home_id) is None
    async with db._read_connection() as conn:
        assert await resolve_action(conn, targets[1].home_id) == targets[1]


async def test_alias_with_one_display_group_and_two_origins_refuses_mutation(homes):
    db, _, targets = homes
    async with db._transaction() as conn:
        await conn.executemany(
            "INSERT INTO home_aliases VALUES ('fixture:ambiguous', ?, 'legacy', 'old')",
            [(t.home_id,) for t in targets[:2]],
        )
    await db.memberships.apply(
        await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW),
        allow_incomplete_projections=True,
    )
    assert await db.homes.resolve("fixture:ambiguous") == targets[0]
    before = await snapshot(db)
    with pytest.raises(HomeActionConflict, match="multiple"):
        await db.update_user_status("fixture:ambiguous", UserStatus.ARCHIVED)
    assert await snapshot(db) == before


async def test_split_requires_review_when_one_original_home_spans_destinations(
    homes, sample_property
):
    db, props, targets = homes
    extra = sample_property.model_copy(update={"source_id": "extra"})
    await db.upsert_source_listings([extra])
    await db.link_source_listings([(extra.unique_id, props[1].unique_id)])
    a, b, c = [t.home_id for t in targets]
    await db.memberships.apply(
        await db.memberships.prepare_merge(a, (b,), **REVIEW), allow_incomplete_projections=True
    )
    with pytest.raises(MembershipConflict, match="ambiguous_alias_split"):
        await db.memberships.prepare_split(a, {props[1].unique_id: c}, **REVIEW)
    decision = await db.memberships.prepare_split(
        a, {props[1].unique_id: c}, alias_routes={b: c}, **REVIEW
    )
    await db.memberships.apply(decision, allow_incomplete_projections=True)
    assert await db.homes.resolve(b) == targets[2]
    # Provenance remains visible in each group containing the original home's members.
    async with db._read_connection() as conn:
        represented = await conn.execute_fetchall(
            "SELECT group_home_id FROM group_personal_states WHERE home_id = ?", (b,)
        )
    assert {r[0] for r in represented} == {a, c}
    assert (await db.memberships.audit()).valid


@pytest.mark.parametrize("operation", ["absorb", "cleanup"])
async def test_retirement_keeps_every_original_row_and_reversal_restores_pending_work(
    homes, sample_property, operation
):
    db, props, targets = homes
    pending = sample_property.model_copy(update={"source_id": "pending"})
    await db.pipeline.save_unenriched_property(merged(pending))
    old = await db.homes.resolve(pending.unique_id)
    original_properties = await rows(db, "properties")
    original_sources = await rows(db, "source_listings")
    if operation == "absorb":
        await db.absorb_staging_properties(
            props[0].unique_id, merged(props[0]), absorbed_ids=[pending.unique_id]
        )
        assert await db.homes.resolve(old.home_id) == targets[0]
    else:
        await db.delete_property(pending.unique_id)
        assert await db.homes.resolve(old.home_id) is None
    decision_id = (await rows(db, "membership_decisions"))[-1][1]
    assert await db.get_property(pending.unique_id) is None
    assert pending.unique_id not in {
        m.canonical.unique_id for m in await db.pipeline.get_unenriched_properties()
    }
    # Automatic cache refresh changes only the active anchor, never the donor.
    retained = await rows(db, "properties")
    assert retained[-1] == original_properties[-1]
    reversal = await db.memberships.prepare_reversal(decision_id, **REVIEW)
    await db.memberships.apply(reversal, allow_incomplete_projections=True)
    assert await db.homes.resolve(old.home_id) == old
    assert await rows(db, "source_listings") == original_sources
    assert pending.unique_id in {
        m.canonical.unique_id for m in await db.pipeline.get_unenriched_properties()
    }
    await db.memberships.apply(
        await db.memberships.prepare_reversal(reversal.decision_id, **REVIEW),
        allow_incomplete_projections=True,
    )
    assert await db.get_property(pending.unique_id) is None
    assert (await db.memberships.audit()).valid


async def test_unpromoted_staging_alias_tracks_reversal_to_unowned(homes, sample_property):
    db, props, targets = homes
    pending = sample_property.model_copy(update={"source_id": "unpromoted"})
    await db.upsert_source_listings([pending])
    await db.absorb_staging_properties(
        props[0].unique_id, merged(props[0]), absorbed_ids=[pending.unique_id]
    )
    decision_id = (await rows(db, "membership_decisions"))[-1][1]
    assert await db.homes.resolve(pending.unique_id) == targets[0]
    undo = await db.memberships.prepare_reversal(decision_id, **REVIEW)
    await db.memberships.apply(undo, allow_incomplete_projections=True)
    assert await db.homes.resolve(pending.unique_id) is None
    await db.memberships.apply(
        await db.memberships.prepare_reversal(undo.decision_id, **REVIEW),
        allow_incomplete_projections=True,
    )
    assert await db.homes.resolve(pending.unique_id) == targets[0]
    assert (await db.memberships.audit()).valid


async def test_inactive_home_cannot_reserve_model_spend_or_queue_reanalysis(homes):
    db, props, targets = homes
    await db.memberships.apply(
        await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW),
        allow_incomplete_projections=True,
    )
    with pytest.raises(SpendDeferred, match="inactive_home"):
        await db.spend.admit(
            spec().model_copy(update={"property_id": props[1].unique_id}), policy()
        )
    assert not await rows(db, "paid_attempts")
    assert {p.property.unique_id for p in await db.get_unsent_notifications()} == {
        props[0].unique_id,
        props[2].unique_id,
    }


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM home_routes",
        "UPDATE home_routes SET state = 'retired', target_home_id = NULL",
        "DELETE FROM home_route_baselines",
        "UPDATE home_route_baselines SET state = 'retired', target_home_id = NULL",
        "INSERT OR REPLACE INTO home_route_baselines SELECT * FROM home_route_baselines LIMIT 1",
        "INSERT OR REPLACE INTO home_routes SELECT * FROM home_routes LIMIT 1",
        "DELETE FROM home_route_changes",
        "INSERT OR REPLACE INTO home_route_changes SELECT * FROM home_route_changes LIMIT 1",
    ],
)
async def test_route_journal_and_projection_cannot_be_silently_rewritten(homes, statement):
    db, _, targets = homes
    await db.memberships.apply(
        await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW),
        allow_incomplete_projections=True,
    )
    before = await snapshot(db)
    with pytest.raises(aiosqlite.IntegrityError):
        async with db._transaction() as conn:
            await conn.execute(statement)
    assert await snapshot(db) == before


async def test_route_failure_rolls_back_membership_and_invalidations(homes):
    db, _, targets = homes
    decision = await db.memberships.prepare_merge(
        targets[0].home_id, (targets[1].home_id,), **REVIEW
    )
    before = await snapshot(db)
    async with db._transaction() as conn:
        await conn.execute(
            "CREATE TRIGGER fail_route BEFORE UPDATE ON home_routes "
            "BEGIN SELECT RAISE(ABORT, 'route fault'); END"
        )
    with pytest.raises(aiosqlite.IntegrityError, match="route fault"):
        await db.memberships.apply(decision, allow_incomplete_projections=True)
    assert await snapshot(db) == before


async def test_committed_v1_command_retry_and_reversal_remain_supported(homes):
    db, props, targets = homes
    review = await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)
    payload = review.model_dump(mode="json")
    payload["version"] = 1
    del payload["before"]["routes"]
    del payload["after_routes"]
    legacy = MembershipDecision.model_validate(payload)
    async with db._transaction() as conn:
        await write_membership_decision(
            conn,
            decision_id=legacy.decision_id,
            operation="merge",
            actor=legacy.actor,
            reason=legacy.reason,
            evidence=legacy.evidence,
            expected_revision=legacy.before.revision,
            command_json=json.dumps(payload),
            changes={props[1].unique_id: (targets[1].home_id, targets[0].home_id)},
        )
    before = await snapshot(db)
    assert (await db.memberships.apply(legacy, allow_incomplete_projections=True)).replayed
    assert await snapshot(db) == before
    undo = await db.memberships.prepare_reversal(legacy.decision_id, **REVIEW)
    await db.memberships.apply(undo, allow_incomplete_projections=True)
    assert await db.homes.resolve(targets[1].home_id) == targets[1]
    assert (await db.memberships.audit()).valid


async def test_unapplied_v1_review_requires_refresh(homes):
    db, _, targets = homes
    payload = (
        await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)
    ).model_dump(mode="json")
    payload["version"] = 1
    del payload["before"]["routes"]
    del payload["after_routes"]
    with pytest.raises(MembershipConflict, match="requires_refresh"):
        await db.memberships.apply(
            MembershipDecision.model_validate(payload), allow_incomplete_projections=True
        )


async def test_route_corruption_audit_is_read_only(homes):
    db, _, targets = homes
    async with db._transaction() as conn:
        await conn.execute("DROP TRIGGER home_route_guard")
        await conn.execute("DROP TRIGGER home_route_owner_guard")
        await conn.execute(
            "UPDATE home_routes SET target_home_id = ? WHERE home_id = ?",
            (targets[0].home_id, targets[1].home_id),
        )
    before = await snapshot(db)
    audit = await db.memberships.audit()
    assert audit.errors == {"route_projection": 1, "route_invariants": 1}
    assert await snapshot(db) == before


async def test_migration14_rolls_back_partial_schema(tmp_path):
    from home_finder.db.home_group_schema import create_home_group_schema

    async with aiosqlite.connect(tmp_path / "migration.db") as conn:
        conn.row_factory = aiosqlite.Row
        with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:13]):
            await run_migrations(conn)

        async def fail(connection):
            await create_home_group_schema(connection)
            raise RuntimeError("migration interrupted")

        with (
            patch("home_finder.db.home_group_schema.create_home_group_schema", side_effect=fail),
            pytest.raises(RuntimeError),
        ):
            await run_migrations(conn)
        assert (await conn.execute_fetchall("PRAGMA user_version"))[0][0] == 13
        assert not await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE name = 'home_routes'"
        )


async def test_merge_after_reservation_releases_it_before_dispatch(homes):
    db, props, targets = homes
    attempt = await db.spend.admit(
        spec().model_copy(update={"property_id": props[1].unique_id}), policy()
    )
    await db.memberships.apply(
        await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW),
        allow_incomplete_projections=True,
    )
    with pytest.raises(SpendDeferred, match="inactive_home"):
        await db.spend.mark_dispatched(attempt)
    async with db._read_connection() as conn:
        row = (
            await conn.execute_fetchall(
                "SELECT state, accounted_nanousd FROM paid_attempts WHERE attempt_id = ?",
                (attempt,),
            )
        )[0]
    assert tuple(row) == ("released", 0)
    assert (await db.spend.report())["held_nanousd"] == 0


async def test_legacy_deleted_origin_is_displayable_but_cannot_edit_the_anchor(tmp_path):
    from home_finder.db import PropertyStorage
    from tests.test_db.test_provider_identities import seed_legacy

    path = str(tmp_path / "deleted-origin.db")
    ghost = "h_" + "a" * 32
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        await seed_legacy(conn)
        with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:13]):
            await run_migrations(conn)
        anchor = (
            await conn.execute_fetchall("SELECT home_id FROM homes ORDER BY home_id LIMIT 1")
        )[0][0]
        await conn.execute("INSERT INTO homes VALUES (?, 'fixture:deleted', NULL, 'old')", (ghost,))
        await conn.execute(
            "INSERT INTO home_aliases VALUES ('fixture:deleted', ?, 'canonical', 'old')", (ghost,)
        )
        await conn.execute(
            "INSERT INTO home_redirects VALUES (?, ?, 'staging_absorption', 'old')", (ghost, anchor)
        )
        original = {
            table: [
                tuple(r)
                for r in await conn.execute_fetchall(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in (
                "homes",
                "home_aliases",
                "home_redirects",
                "properties",
                "source_listings",
                "membership_decisions",
            )
        }
        await conn.commit()
        await run_migrations(conn)
        for table, expected in original.items():
            assert [
                tuple(r)
                for r in await conn.execute_fetchall(f"SELECT * FROM {table} ORDER BY rowid")
            ] == expected
        await run_migrations(conn)
        assert not await conn.execute_fetchall("PRAGMA foreign_key_check")
    async with PropertyStorage(path) as db:
        assert await db.homes.resolve(ghost) == await db.homes.resolve(anchor)
        assert await db.homes.resolve("fixture:deleted") == await db.homes.resolve(anchor)
        with pytest.raises(HomeActionConflict, match="unavailable"):
            await db.update_user_status(ghost, UserStatus.INTERESTED)
        assert (await db.memberships.audit()).valid


async def test_all_work_queues_exclude_grouped_donor_and_restore_exact_state(
    homes, sample_quality_analysis
):
    db, props, targets = homes
    for prop in props:
        await db.save_quality_analysis(prop.unique_id, sample_quality_analysis)
        await db.detect_and_record_price_change(prop.unique_id, prop.price_pcm - 100)
    async with db._transaction() as conn:
        await conn.execute("UPDATE quality_analyses SET reanalysis_requested_at = 'fixture'")
    original = {t: await rows(db, t) for t in ("properties", "quality_analyses", "price_history")}
    merge = await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)
    await db.memberships.apply(merge, allow_incomplete_projections=True)
    assert len(await db.get_pending_notifications()) == 2
    assert len(await db.get_unsent_notifications()) == 2
    assert len(await db.get_recent_properties_for_dedup(days=None)) == 2
    assert len(await db.get_properties_without_ward()) == 2
    assert len(await db.get_properties_needing_commute()) == 2
    assert len(await db.get_properties_for_off_market_check()) == 2
    assert len(await db.pipeline.get_reanalysis_queue()) == 2
    await db.memberships.apply(
        await db.memberships.prepare_reversal(merge.decision_id, **REVIEW),
        allow_incomplete_projections=True,
    )
    assert len(await db.pipeline.get_reanalysis_queue()) == 3
    for table, expected in original.items():
        assert await rows(db, table) == expected


async def test_price_alerts_exclude_donors_without_consuming_their_pending_alert(homes):
    db, props, targets = homes
    for prop in props:
        await db.mark_notified(prop.unique_id)
        await db.detect_and_record_price_change(prop.unique_id, prop.price_pcm - 100)
    before = await rows(db, "price_history")
    merge = await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)
    await db.memberships.apply(merge, allow_incomplete_projections=True)
    assert len(await db.get_unsent_price_drops()) == 2
    await db.memberships.apply(
        await db.memberships.prepare_reversal(merge.decision_id, **REVIEW),
        allow_incomplete_projections=True,
    )
    assert len(await db.get_unsent_price_drops()) == 3
    assert await rows(db, "price_history") == before


async def test_staging_chain_checks_personal_history_on_retained_aliases(homes, sample_property):
    from home_finder.db.merge_safety import MergeBlockedError

    db, props, _ = homes
    middle = sample_property.model_copy(update={"source_id": "middle"})
    earliest = sample_property.model_copy(update={"source_id": "earliest"})
    for prop in (middle, earliest):
        await db.pipeline.save_unenriched_property(merged(prop))
    await db.absorb_staging_properties(
        middle.unique_id, merged(middle), absorbed_ids=[earliest.unique_id]
    )
    await db.update_user_status(earliest.unique_id, UserStatus.INTERESTED)
    before = await snapshot(db)
    with pytest.raises(MergeBlockedError) as error:
        await db.absorb_staging_properties(
            props[0].unique_id, merged(props[0]), absorbed_ids=[middle.unique_id]
        )
    assert "user_state" in error.value.blockers[earliest.unique_id]
    with pytest.raises(MergeBlockedError):
        await db.delete_property(middle.unique_id)
    assert await snapshot(db) == before


async def test_cleanup_of_an_already_absorbed_donor_keeps_issued_links(homes, sample_property):
    db, props, targets = homes
    donor = sample_property.model_copy(update={"source_id": "donor"})
    await db.pipeline.save_unenriched_property(merged(donor))
    await db.absorb_staging_properties(
        props[0].unique_id, merged(props[0]), absorbed_ids=[donor.unique_id]
    )
    before = await snapshot(db)
    await db.delete_property(donor.unique_id)
    assert await snapshot(db) == before
    assert await db.homes.resolve(donor.unique_id) == targets[0]
