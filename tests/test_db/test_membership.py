"""Membership review is atomic, reversible and cannot silently transfer user history."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import aiosqlite
import pytest
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.db.membership_ops import MembershipConflict
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.db.migrations import MIGRATIONS, run_migrations
from home_finder.models import MergedProperty, UserStatus
from home_finder.models.membership import MembershipDecision

REVIEW = {
    "actor": "fixture-reviewer",
    "reason": "Compared retained unit evidence",
    "evidence": ("fixture:pair",),
}


def merged(prop):
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


@pytest.fixture
async def homes(tmp_path, sample_property):
    async with PropertyStorage(str(tmp_path / "membership.db")) as db:
        props = [
            sample_property.model_copy(
                update={"source_id": str(i), "url": f"https://www.rightmove.co.uk/properties/{i}"}
            )
            for i in (101, 202, 303)
        ]
        for prop in props:
            await db.save_property(prop)
        targets = [await db.homes.resolve(p.unique_id) for p in props]
        assert all(targets)
        yield db, props, targets


async def rows(db, table):
    async with (
        db._read_connection() as conn,
        conn.execute(f"SELECT * FROM {table} ORDER BY rowid") as cur,
    ):
        return [tuple(row) for row in await cur.fetchall()]


async def snapshot(db):
    async with (
        db._read_connection() as conn,
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        ) as cur,
    ):
        tables = [row[0] for row in await cur.fetchall()]
    return {table: await rows(db, table) for table in tables}


async def review_pair(db, targets):
    return await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)


async def test_merge_split_replay_preserves_all_rows_and_original_links(
    homes, sample_quality_analysis
):
    db, props, targets = homes
    for index, prop in enumerate(props[:2]):
        await db.update_user_status(
            prop.unique_id, UserStatus.INTERESTED if index == 0 else UserStatus.ARCHIVED
        )
        await db.save_viewing_message(prop.unique_id, f"Saved message for original home {index}")
        await db.save_quality_analysis(prop.unique_id, sample_quality_analysis)
        async with db._transaction() as conn:
            await conn.execute(
                "INSERT INTO price_history "
                "(property_unique_id, old_price, new_price, change_amount) "
                "VALUES (?, 2000, 1900, -100)",
                (prop.unique_id,),
            )
    original = await snapshot(db)
    review = await review_pair(db, targets)
    assert review.before.has_status_conflict and review.before.has_message_conflict
    assert MembershipDecision.model_validate_json(review.model_dump_json()) == review
    receipt = await db.memberships.apply(review)
    assert receipt.changed == 1 and not receipt.replayed
    combined = await db.memberships.inspect((targets[0].home_id,))
    assert {m.source_unique_id for m in combined.members} == {p.unique_id for p in props[:2]}
    assert combined.has_status_conflict and combined.has_message_conflict
    assert {s.home_id for s in combined.personal_states} == {t.home_id for t in targets[:2]}
    for prop, target in zip(props, targets, strict=True):
        assert await db.homes.resolve(prop.unique_id) == target
        assert await db.homes.resolve(target.home_id) == target
    for table in original.keys() - {
        "source_listings",
        "source_memberships",
        "membership_decisions",
        "membership_changes",
        "membership_projection_invalidations",
    }:
        assert await rows(db, table) == original[table], table
    # A status action through an issued link retains its original provenance.
    await db.update_user_status(targets[1].home_id, UserStatus.VIEWING_BOOKED)
    assert (await db.get_status_history(props[1].unique_id))[-1]["to_status"] == "viewing_booked"
    assert (await db.get_status_history(props[0].unique_id))[-1]["to_status"] == "interested"
    split = await db.memberships.prepare_split(
        targets[0].home_id, {props[1].unique_id: targets[1].home_id}, **REVIEW
    )
    await db.memberships.apply(split)
    assert await rows(db, "source_listings") == original["source_listings"]
    assert (await db.memberships.inspect((targets[1].home_id,))).personal_states[
        0
    ].user_status == "viewing_booked"
    after_split = await snapshot(db)
    # Retrying the old committed merge acknowledges it; it must not merge again.
    replayed = await db.memberships.apply(review)
    assert replayed.replayed and replayed.revision == receipt.revision
    assert await snapshot(db) == after_split
    audit = await db.memberships.audit()
    assert audit.valid and audit.errors == {}
    assert audit.decisions == 5  # three original links, merge, split


async def test_reversal_uses_recorded_map_and_can_itself_be_reversed(homes):
    db, _, targets = homes
    original = await rows(db, "source_listings")
    merge = await review_pair(db, targets)
    await db.memberships.apply(merge)
    reversal = await db.memberships.prepare_reversal(merge.decision_id, **REVIEW)
    await db.memberships.apply(reversal)
    assert await rows(db, "source_listings") == original
    with pytest.raises(MembershipConflict, match="already_reversed"):
        await db.memberships.prepare_reversal(merge.decision_id, **REVIEW)
    redo = await db.memberships.prepare_reversal(reversal.decision_id, **REVIEW)
    await db.memberships.apply(redo)
    assert len((await db.memberships.inspect((targets[0].home_id,))).members) == 2


async def test_intervening_membership_change_and_change_back_invalidate_old_review(homes):
    db, _, targets = homes
    old_review = await review_pair(db, targets)
    applied = await review_pair(db, targets)
    await db.memberships.apply(applied)
    undo = await db.memberships.prepare_reversal(applied.decision_id, **REVIEW)
    await db.memberships.apply(undo)
    before = await snapshot(db)
    with pytest.raises(MembershipConflict, match="stale"):
        await db.memberships.apply(old_review)
    assert await snapshot(db) == before


@pytest.mark.parametrize(
    "mutation", ["status", "status_aba", "message", "new_member", "source", "receipt"]
)
async def test_review_rejects_changed_state_or_added_members(homes, mutation, sample_property):
    db, props, targets = homes
    review = await review_pair(db, targets)
    if mutation.startswith("status"):
        await db.update_user_status(props[1].unique_id, UserStatus.INTERESTED)
        if mutation == "status_aba":
            await db.update_user_status(props[1].unique_id, UserStatus.NEW)
    elif mutation == "message":
        await db.save_viewing_message(props[1].unique_id, "new saved message")
    elif mutation == "source":
        await db.upsert_source_listings([props[1].model_copy(update={"price_pcm": 3333})])
    elif mutation == "receipt":
        from home_finder.models.observations import SourceObservation
        from tests.test_db.test_observations import observation

        payload = observation().model_dump(mode="json")
        payload.update(
            source=props[1].source.value,
            source_id=props[1].source_id,
            listing_url=str(props[1].url),
        )
        await db.observations.ingest([SourceObservation.model_validate(payload)])
    else:
        extra = sample_property.model_copy(update={"source_id": "extra"})
        await db.upsert_source_listings([extra])
        await db.link_source_listings([(extra.unique_id, props[1].unique_id)])
    before = await snapshot(db)
    with pytest.raises(MembershipConflict, match="stale"):
        await db.memberships.apply(review)
    assert await snapshot(db) == before


async def test_partial_or_forged_review_is_not_accepted(homes):
    db, _, targets = homes
    review = await review_pair(db, targets)
    tampered = review.model_dump(mode="json")
    tampered["before"]["members"].pop(0)
    tampered["after"].pop(0)
    with pytest.raises(MembershipConflict, match="stale"):
        await db.memberships.apply(MembershipDecision.model_validate(tampered))
    await db.memberships.apply(review)
    with pytest.raises(MembershipConflict, match="different_payload"):
        await db.memberships.apply(review.model_copy(update={"reason": "different evidence"}))


async def test_later_split_blocks_reversal_of_the_original_merge(homes):
    db, props, targets = homes
    merge = await review_pair(db, targets)
    await db.memberships.apply(merge)
    split = await db.memberships.prepare_split(
        targets[0].home_id, {props[1].unique_id: targets[1].home_id}, **REVIEW
    )
    await db.memberships.apply(split)
    with pytest.raises(MembershipConflict, match="later_decision"):
        await db.memberships.prepare_reversal(merge.decision_id, **REVIEW)


async def test_two_independent_connections_cannot_apply_conflicting_reviews(homes):
    db, _, targets = homes
    async with PropertyStorage(db.db_path) as second:
        first_review = await review_pair(db, targets)
        second_review = await second.memberships.prepare_merge(
            targets[2].home_id, (targets[1].home_id,), **REVIEW
        )
        result = await asyncio.gather(
            db.memberships.apply(first_review),
            second.memberships.apply(second_review),
            return_exceptions=True,
        )
        assert sum(isinstance(item, MembershipConflict) for item in result) == 1
        assert sum(getattr(item, "changed", 0) for item in result) == 1
        assert len(await rows(db, "membership_decisions")) == 4  # three initial links, one winner


@pytest.mark.parametrize("failure", ["journal", "source_projection", "cancellation"])
async def test_failed_or_cancelled_group_rolls_back_every_write(homes, failure):
    db, _, targets = homes
    review = await db.memberships.prepare_merge(
        targets[0].home_id, tuple(t.home_id for t in targets[1:]), **REVIEW
    )
    before = await snapshot(db)
    if failure == "cancellation":
        from home_finder.db.membership import write_membership_decision

        reached = asyncio.Event()

        async def interrupt(*args, **kwargs):
            await write_membership_decision(*args, **kwargs)
            reached.set()
            await asyncio.Future()

        with patch("home_finder.db.membership.write_membership_decision", side_effect=interrupt):
            task = asyncio.create_task(db.memberships.apply(review))
            await asyncio.wait_for(reached.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    else:
        table, action = (
            ("membership_changes", "INSERT")
            if failure == "journal"
            else ("source_listings", "UPDATE OF merged_id")
        )
        async with db._transaction() as conn:
            await conn.execute(
                f"CREATE TRIGGER injected_failure BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        with pytest.raises(aiosqlite.IntegrityError, match="injected failure"):
            await db.memberships.apply(review)
    assert await snapshot(db) == before


@pytest.mark.parametrize(
    "method", ["property", "merged", "unenriched", "dropped", "pre_analysis", "link"]
)
async def test_ordinary_save_paths_cannot_undo_a_reviewed_assignment(homes, method):
    db, props, targets = homes
    await db.memberships.apply(await review_pair(db, targets))
    before = await snapshot(db)
    prop = props[1].model_copy(update={"price_pcm": 9876})
    with pytest.raises(MergeBlockedError, match=prop.unique_id):
        if method == "property":
            await db.save_property(prop)
        elif method == "merged":
            await db.save_merged_property(merged(prop))
        elif method == "unenriched":
            await db.pipeline.save_unenriched_property(merged(prop))
        elif method == "dropped":
            await db.pipeline.save_dropped_properties([merged(prop)], {})
        elif method == "pre_analysis":
            await db.pipeline.save_pre_analysis_properties([merged(prop)], {})
        else:
            await db.link_source_listings([(prop.unique_id, prop.unique_id)])
    assert await snapshot(db) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE source_listings SET merged_id = NULL",
        "DELETE FROM source_listings",
        "DELETE FROM properties",
        "UPDATE source_memberships SET home_id = NULL",
        "DELETE FROM source_memberships",
        "UPDATE homes SET current_property_id = NULL",
        "DELETE FROM membership_decisions",
        "UPDATE membership_decisions SET reason = 'changed'",
        "DELETE FROM membership_changes",
        "UPDATE membership_baselines SET home_id = NULL",
        "DELETE FROM membership_origins",
        "UPDATE source_listings SET unique_id = 'replaced'",
        "INSERT OR REPLACE INTO membership_decisions SELECT * FROM membership_decisions LIMIT 1",
        "INSERT OR REPLACE INTO membership_changes SELECT * FROM membership_changes LIMIT 1",
        "INSERT OR REPLACE INTO membership_baselines SELECT * FROM membership_baselines LIMIT 1",
        "INSERT OR REPLACE INTO membership_origins SELECT * FROM membership_origins LIMIT 1",
        "INSERT OR REPLACE INTO source_memberships SELECT * FROM source_memberships LIMIT 1",
    ],
)
async def test_sql_bypasses_cannot_discard_ownership_evidence(homes, statement):
    db, _, _ = homes
    before = await snapshot(db)
    with pytest.raises(aiosqlite.IntegrityError):
        async with db._transaction() as conn:
            await conn.execute(statement)
    assert await snapshot(db) == before


async def test_initial_link_and_noop_retries_are_journaled_once(homes, sample_property):
    db, props, targets = homes
    prop = sample_property.model_copy(update={"source_id": "initial"})
    await db.upsert_source_listings([prop])
    count = len(await rows(db, "membership_decisions"))
    await db.link_source_listings([(prop.unique_id, props[0].unique_id)])
    assert len(await rows(db, "membership_decisions")) == count + 1
    await db.link_source_listings([(prop.unique_id, props[0].unique_id)])
    assert len(await rows(db, "membership_decisions")) == count + 1
    with pytest.raises(MergeBlockedError):
        await db.link_source_listings([(prop.unique_id, props[1].unique_id)])
    assert len((await db.memberships.inspect((targets[0].home_id,))).members) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", " "),
        ("reason", ""),
        ("evidence", ()),
        ("version", 2),
        ("decision_id", "arbitrary"),
    ],
)
async def test_malformed_review_metadata_is_rejected(homes, field, value):
    db, _, targets = homes
    review = await review_pair(db, targets)
    with pytest.raises(ValidationError):
        await db.memberships.apply(review.model_copy(update={field: value}))


async def test_migration_preserves_conflicting_owners_and_is_repeatable(tmp_path):
    from tests.test_db.test_provider_identities import seed_legacy

    path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await seed_legacy(conn)
        with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:12]):
            await run_migrations(conn)
        old = {
            table: [
                tuple(row)
                for row in await conn.execute_fetchall(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in ("properties", "source_listings", "homes", "home_aliases", "price_history")
        }
        await run_migrations(conn)
        for table, expected in old.items():
            assert [
                tuple(row)
                for row in await conn.execute_fetchall(f"SELECT * FROM {table} ORDER BY rowid")
            ] == expected
        assert (await conn.execute_fetchall("SELECT count(*) FROM membership_decisions"))[0][0] == 0
        assert (
            await conn.execute_fetchall("SELECT count(DISTINCT home_id) FROM source_memberships")
        )[0][0] == 2
        assert not await conn.execute_fetchall("PRAGMA foreign_key_check")
    async with PropertyStorage(path) as db:
        before = await snapshot(db)
        await db.initialize()
        assert await snapshot(db) == before
        assert (await db.identities.audit()).owner_conflict_groups == 1


async def test_partial_migration_rolls_back_schema_and_version(tmp_path):
    from home_finder.db.membership_schema import create_membership_schema

    async with aiosqlite.connect(tmp_path / "migration.db") as conn:
        conn.row_factory = aiosqlite.Row
        with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:12]):
            await run_migrations(conn)

        async def fail_after_schema(connection):
            await create_membership_schema(connection)
            raise RuntimeError("migration interrupted")

        with (
            patch(
                "home_finder.db.membership_schema.create_membership_schema",
                side_effect=fail_after_schema,
            ),
            pytest.raises(RuntimeError, match="interrupted"),
        ):
            await run_migrations(conn)
        assert (await conn.execute_fetchall("PRAGMA user_version"))[0][0] == 12
        assert not await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE name = 'membership_decisions'"
        )


async def test_reversal_cannot_invent_an_unrecorded_destination(homes):
    db, _, targets = homes
    review = await review_pair(db, targets)
    await db.memberships.apply(review)
    reversal = await db.memberships.prepare_reversal(review.decision_id, **REVIEW)
    forged = reversal.model_dump(mode="json")
    forged["operation"] = "merge"
    forged["reverses"] = None
    with pytest.raises(MembershipConflict, match="merge_requires"):
        await db.memberships.apply(MembershipDecision.model_validate(forged))
    before = await snapshot(db)
    invalid = reversal.model_copy(update={"reverses": "md_" + uuid4().hex})
    with pytest.raises(MembershipConflict, match="only_reviewed"):
        await db.memberships.apply(invalid)
    assert await snapshot(db) == before


async def test_reviewed_empty_donor_cannot_be_retired_as_staging(homes, sample_property):
    db, props, targets = homes
    pending = sample_property.model_copy(update={"source_id": "pending-donor"})
    await db.pipeline.save_unenriched_property(merged(pending))
    donor = await db.homes.resolve(pending.unique_id)
    decision = await db.memberships.prepare_merge(targets[0].home_id, (donor.home_id,), **REVIEW)
    await db.memberships.apply(decision)
    before = await snapshot(db)
    with pytest.raises(MergeBlockedError) as error:
        await db.delete_property(pending.unique_id)
    assert "reviewed_membership" in error.value.blockers[pending.unique_id]
    with pytest.raises(MergeBlockedError):
        await db.absorb_staging_properties(
            props[0].unique_id, merged(props[0]), absorbed_ids=[pending.unique_id]
        )
    with pytest.raises(aiosqlite.IntegrityError, match="Reviewed home"):
        async with db._transaction() as conn:
            await conn.execute("DELETE FROM properties WHERE unique_id = ?", (pending.unique_id,))
    assert await snapshot(db) == before
    await db.memberships.apply(
        await db.memberships.prepare_reversal(decision.decision_id, **REVIEW)
    )
    assert (await db.memberships.audit()).valid


async def test_audit_reports_projection_corruption_without_repairing_it(homes):
    db, props, targets = homes
    assert (await db.memberships.audit()).valid
    async with db._transaction() as conn:
        await conn.execute("DROP TRIGGER membership_projection_guard")
        await conn.execute(
            "UPDATE source_memberships SET home_id = ? WHERE source_unique_id = ?",
            (targets[1].home_id, props[0].unique_id),
        )
    corrupted = await snapshot(db)
    report = await db.memberships.audit()
    assert not report.valid
    assert report.errors == {"membership_projection": 1, "legacy_projection": 1}
    assert await snapshot(db) == corrupted


async def test_staging_absorption_and_cleanup_are_journaled_and_keep_legacy_links(
    homes, sample_property
):
    db, props, targets = homes
    pending = sample_property.model_copy(update={"source_id": "staging"})
    await db.pipeline.save_unenriched_property(merged(pending))
    old_home = await db.homes.resolve(pending.unique_id)
    await db.absorb_staging_properties(
        props[0].unique_id, merged(props[0]), absorbed_ids=[pending.unique_id]
    )
    assert await db.homes.resolve(old_home.home_id) == targets[0]
    before_retry = await snapshot(db)
    await db.absorb_staging_properties(
        props[0].unique_id, merged(props[0]), absorbed_ids=[pending.unique_id]
    )
    # The existing cache refresh updates its timestamp; membership evidence is a no-op.
    for table in ("membership_decisions", "membership_changes", "source_memberships"):
        assert await rows(db, table) == before_retry[table]
    discarded = sample_property.model_copy(update={"source_id": "discard"})
    await db.pipeline.save_unenriched_property(merged(discarded))
    await db.delete_property(discarded.unique_id)
    audit = await db.memberships.audit()
    assert audit.valid and audit.retained_source_memberships == 5
    async with db._read_connection() as conn:
        operations = [
            r[0] for r in await conn.execute_fetchall("SELECT operation FROM membership_decisions")
        ]
    assert operations.count("staging_link") == operations.count("cleanup") == 1


async def test_url_linkage_uses_the_journal_and_stable_member_queries(homes, make_property):
    from home_finder.db.source_listing_ops import link_source_listings_by_url
    from home_finder.models import PropertySource

    db, props, targets = homes
    extra = make_property(source=PropertySource.ONTHEMARKET, source_id="extra")
    await db.upsert_source_listings([extra])
    async with db._transaction() as conn:
        await link_source_listings_by_url(conn, props[0].unique_id, {extra.source: extra.url})
    members = await db.identities.members(targets[0].home_id)
    assert {m.representation.unique_id for m in members} == {extra.unique_id, props[0].unique_id}
    assert (await db.memberships.audit()).valid
    assert (await rows(db, "membership_decisions"))[-1][4] == "in_run_url_linkage"


@pytest.mark.parametrize("replace", ["owned_source", "reviewed_empty_home"])
async def test_replace_cannot_bypass_delete_guards_with_recursive_triggers_off(homes, replace):
    db, props, targets = homes
    await db.memberships.apply(await review_pair(db, targets))
    before = await snapshot(db)
    with pytest.raises(aiosqlite.IntegrityError, match="membership decision"):
        async with db._transaction() as conn:
            await conn.execute("PRAGMA recursive_triggers = OFF")
            if replace == "owned_source":
                columns = [
                    r[1] for r in await conn.execute_fetchall("PRAGMA table_info(source_listings)")
                ]
                selected = [column if column != "merged_id" else "NULL" for column in columns]
                await conn.execute(
                    f"INSERT OR REPLACE INTO source_listings ({','.join(columns)}) "
                    f"SELECT {','.join(selected)} FROM source_listings LIMIT 1"
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO properties "
                    "SELECT * FROM properties WHERE unique_id = ?",
                    (props[1].unique_id,),
                )
    assert await snapshot(db) == before
