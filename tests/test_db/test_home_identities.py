"""Stable home links survive source changes without reassigning protected history."""

import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from home_finder.db import PropertyStorage
from home_finder.db.home_groups import HomeActionConflict
from home_finder.db.homes import HomeIdentityConflict
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.db.migrations import MIGRATIONS, run_migrations
from home_finder.models import MergedProperty, UserStatus
from home_finder.models.home_identity import is_home_id


@pytest.fixture
async def storage(tmp_path):
    async with PropertyStorage(str(tmp_path / "homes.db")) as db:
        yield db


def merged(prop):
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


async def save(storage, method, prop):
    if method == "property":
        await storage.save_property(prop)
    elif method == "merged":
        await storage.save_merged_property(merged(prop))
    elif method == "unenriched":
        await storage.pipeline.save_unenriched_property(merged(prop))
    elif method == "dropped":
        await storage.pipeline.save_dropped_properties([merged(prop)], {})
    else:
        assert method == "pre_analysis"
        await storage.pipeline.save_pre_analysis_properties([merged(prop)], {})


METHODS = ["property", "merged", "unenriched", "dropped", "pre_analysis"]


@pytest.mark.parametrize("method", METHODS)
async def test_all_canonical_save_paths_have_one_durable_home(storage, sample_property, method):
    await save(storage, method, sample_property)
    target = await storage.homes.resolve(sample_property.unique_id)
    assert target is not None and is_home_id(target.home_id)
    assert target.property_id == sample_property.unique_id
    assert target.home_id != sample_property.source_id
    await save(storage, method, sample_property)
    assert await storage.homes.resolve(target.home_id) == target
    with closing(sqlite3.connect(storage.db_path)) as reader:
        assert reader.execute("SELECT count(*) FROM homes").fetchone()[0] == 1
        assert reader.execute("SELECT count(*) FROM home_aliases").fetchone()[0] == 1


@pytest.mark.parametrize("method", METHODS)
async def test_identity_failure_rolls_back_every_part_of_a_save(storage, sample_property, method):
    conn = await storage._get_connection()
    await conn.execute("""
        CREATE TRIGGER injected_home_failure BEFORE INSERT ON home_aliases
        BEGIN SELECT RAISE(ABORT, 'injected identity failure'); END
    """)
    await conn.commit()
    with pytest.raises(aiosqlite.IntegrityError, match="injected identity failure"):
        await save(storage, method, sample_property)
    for table in ("homes", "home_aliases", "properties", "source_listings", "provider_listings"):
        assert not await conn.execute_fetchall(f"SELECT * FROM {table}")


async def test_scraped_unpromoted_source_does_not_invent_a_home(storage, sample_property):
    await storage.upsert_source_listings([sample_property])
    assert await storage.homes.resolve(sample_property.unique_id) is None


async def test_staging_absorption_retains_old_home_and_legacy_links(storage, sample_property):
    first = sample_property
    second = first.model_copy(update={"source_id": "another-listing"})
    await storage.save_property(first)
    await storage.pipeline.save_unenriched_property(merged(second))
    anchor = await storage.homes.resolve(first.unique_id)
    previous = await storage.homes.resolve(second.unique_id)
    assert anchor and previous and anchor.home_id != previous.home_id
    retired = await storage.absorb_staging_properties(
        first.unique_id, merged(first), absorbed_ids=[second.unique_id]
    )
    assert retired == [second.unique_id]
    assert await storage.homes.resolve(second.unique_id) == anchor
    assert await storage.homes.resolve(previous.home_id) == anchor
    assert (
        await storage.absorb_staging_properties(
            first.unique_id, merged(first), absorbed_ids=[second.unique_id]
        )
        == []
    )
    assert await storage.homes.resolve(previous.home_id) == anchor
    with pytest.raises(HomeIdentityConflict, match="cannot revive"):
        await storage.save_property(second)
    assert await storage.get_property(second.unique_id) is None
    assert (
        await storage.update_user_status(previous.home_id, UserStatus.INTERESTED) == UserStatus.NEW
    )
    assert len(await storage.get_status_history(second.unique_id)) == 1
    assert not await storage.get_status_history(first.unique_id)


async def test_unpromoted_source_alias_resolves_after_guarded_absorption(storage, sample_property):
    second = sample_property.model_copy(update={"source_id": "unpromoted"})
    await storage.save_property(sample_property)
    await storage.upsert_source_listings([second])
    await storage.absorb_staging_properties(
        sample_property.unique_id, merged(sample_property), absorbed_ids=[second.unique_id]
    )
    assert await storage.homes.resolve(second.unique_id) == await storage.homes.resolve(
        sample_property.unique_id
    )
    with pytest.raises(HomeIdentityConflict, match="cannot reuse"):
        await storage.save_property(second)


async def test_redirect_chain_and_replay_keep_all_previously_issued_links(storage, sample_property):
    middle = sample_property.model_copy(update={"source_id": "middle"})
    earliest = sample_property.model_copy(update={"source_id": "earliest"})
    await storage.save_property(sample_property)
    await storage.pipeline.save_unenriched_property(merged(middle))
    await storage.pipeline.save_unenriched_property(merged(earliest))
    original = await storage.homes.resolve(earliest.unique_id)
    assert original
    await storage.absorb_staging_properties(
        middle.unique_id, merged(middle), absorbed_ids=[earliest.unique_id]
    )
    await storage.absorb_staging_properties(
        sample_property.unique_id, merged(sample_property), absorbed_ids=[middle.unique_id]
    )
    anchor = await storage.homes.resolve(sample_property.unique_id)
    assert await storage.homes.resolve(original.home_id) == anchor
    assert (
        await storage.absorb_staging_properties(
            sample_property.unique_id, merged(sample_property), absorbed_ids=[earliest.unique_id]
        )
        == []
    )
    assert await storage.homes.resolve(earliest.unique_id) == anchor


async def test_failed_absorption_rolls_back_redirects_and_membership(storage, sample_property):
    second = sample_property.model_copy(update={"source_id": "staging"})
    await storage.save_property(sample_property)
    await storage.pipeline.save_unenriched_property(merged(second))
    original = await storage.homes.resolve(second.unique_id)
    conn = await storage._get_connection()
    await conn.execute("""
        CREATE TRIGGER injected_retirement_failure BEFORE UPDATE ON home_routes
        BEGIN SELECT RAISE(ABORT, 'injected retirement failure'); END
    """)
    await conn.commit()
    with pytest.raises(aiosqlite.IntegrityError, match="injected retirement failure"):
        await storage.absorb_staging_properties(
            sample_property.unique_id, merged(sample_property), absorbed_ids=[second.unique_id]
        )
    assert await storage.homes.resolve(second.unique_id) == original
    assert not await conn.execute_fetchall("SELECT * FROM home_redirects")
    assert (
        await conn.execute_fetchall(
            "SELECT merged_id FROM source_listings WHERE unique_id = ?", (second.unique_id,)
        )
    )[0][0] == second.unique_id


async def test_established_home_never_gets_a_redirect(storage, sample_property):
    second = sample_property.model_copy(update={"source_id": "established"})
    await storage.save_property(sample_property)
    await storage.save_property(second)
    original = await storage.homes.resolve(second.unique_id)
    with pytest.raises(MergeBlockedError):
        await storage.absorb_staging_properties(
            sample_property.unique_id, merged(sample_property), absorbed_ids=[second.unique_id]
        )
    assert await storage.homes.resolve(second.unique_id) == original


async def test_deleted_staging_home_keeps_identity_and_can_be_restored(storage, sample_property):
    await storage.pipeline.save_unenriched_property(merged(sample_property))
    original = await storage.homes.resolve(sample_property.unique_id)
    assert original
    await storage.delete_property(sample_property.unique_id)
    assert await storage.homes.resolve(original.home_id) is None
    assert await storage.homes.resolve(sample_property.unique_id) is None
    await storage.pipeline.save_unenriched_property(merged(sample_property))
    assert await storage.homes.resolve(sample_property.unique_id) == original


async def test_ambiguous_alias_and_redirect_cycle_fail_closed(storage, sample_property):
    await storage.save_property(sample_property)
    second = sample_property.model_copy(update={"source_id": "456"})
    await storage.save_property(second)
    first_target = await storage.homes.resolve(sample_property.unique_id)
    second_target = await storage.homes.resolve(second.unique_id)
    assert first_target and second_target
    async with storage._transaction() as conn:
        await conn.executemany(
            "INSERT INTO home_aliases VALUES ('rightmove:ambiguous', ?, 'legacy', 'old')",
            [(first_target.home_id,), (second_target.home_id,)],
        )
    assert await storage.homes.resolve("rightmove:ambiguous") is None
    with pytest.raises(HomeActionConflict):
        await storage.update_user_status("rightmove:ambiguous", UserStatus.ARCHIVED)
    async with storage._transaction() as conn:
        with pytest.raises(aiosqlite.IntegrityError, match="legacy evidence"):
            await conn.execute(
                "INSERT INTO home_redirects VALUES (?, ?, 'staging_absorption', 'old')",
                (first_target.home_id, second_target.home_id),
            )
        # Corruption rehearsal: bypass guards deliberately and refuse chained routes.
        await conn.execute("DROP TRIGGER home_route_guard")
        await conn.execute("DROP TRIGGER home_route_owner_guard")
        await conn.executemany(
            "UPDATE home_routes SET target_home_id = ? WHERE home_id = ?",
            [
                (second_target.home_id, first_target.home_id),
                (first_target.home_id, second_target.home_id),
            ],
        )
    assert await storage.homes.resolve(first_target.home_id) is None
    assert await storage.homes.resolve("h_" + "0" * 32) is None


@pytest.mark.parametrize(
    "table,sql",
    [
        ("homes", "UPDATE homes SET origin_property_id = 'other'"),
        ("homes", "DELETE FROM homes"),
        ("home_aliases", "UPDATE home_aliases SET alias = 'other'"),
        ("home_aliases", "DELETE FROM home_aliases"),
    ],
)
async def test_identity_and_alias_evidence_is_immutable(storage, sample_property, table, sql):
    await storage.save_property(sample_property)
    conn = await storage._get_connection()
    before = await conn.execute_fetchall(f"SELECT * FROM {table}")
    with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
        await conn.execute(sql)
    await conn.rollback()
    assert await conn.execute_fetchall(f"SELECT * FROM {table}") == before


async def seed_old_schema(conn):
    with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:11]):
        await run_migrations(conn)
    await conn.execute("""
        INSERT INTO properties
        (unique_id, source, source_id, url, title, price_pcm, bedrooms,
         address, first_seen, user_status)
        VALUES ('rightmove:123', 'rightmove', '123', 'https://rightmove.co.uk/properties/123',
                'Saved home', 2000, 2, 'Address', 'old', 'interested')
    """)
    await conn.execute("""
        INSERT INTO source_aliases (unique_id, source, source_id, anchor_id)
        VALUES ('zoopla:456', 'zoopla', '456', 'rightmove:123')
    """)
    await conn.execute("""
        INSERT INTO price_history (property_unique_id, old_price, new_price, change_amount)
        VALUES ('rightmove:123', 1900, 2000, 100)
    """)
    await conn.commit()


async def test_additive_migration_keeps_all_old_rows_and_aliases(tmp_path):
    path = tmp_path / "migration.db"
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await seed_old_schema(conn)
        tables = [
            row[0]
            for row in await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        before = {table: await conn.execute_fetchall(f"SELECT * FROM {table}") for table in tables}
        await run_migrations(conn)
        assert {
            table: await conn.execute_fetchall(f"SELECT * FROM {table}") for table in tables
        } == before
        homes = await conn.execute_fetchall("SELECT * FROM homes")
        aliases = await conn.execute_fetchall("SELECT * FROM home_aliases")
        await run_migrations(conn)
        assert await conn.execute_fetchall("SELECT * FROM homes") == homes
        assert await conn.execute_fetchall("SELECT * FROM home_aliases") == aliases
        assert not await conn.execute_fetchall("PRAGMA foreign_key_check")
    async with PropertyStorage(str(path)) as storage:
        target = await storage.homes.resolve("rightmove:123")
        assert target
        assert await storage.homes.resolve("zoopla:456") == target
        assert await storage.homes.resolve(target.home_id) == target


async def test_failed_backfill_rolls_back_new_tables_and_version():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await seed_old_schema(conn)
        with (
            patch(
                "home_finder.db.homes.ensure_home", AsyncMock(side_effect=RuntimeError("injected"))
            ),
            pytest.raises(RuntimeError, match="injected"),
        ):
            await run_migrations(conn)
        assert (await conn.execute_fetchall("PRAGMA user_version"))[0][0] == 11
        assert not await conn.execute_fetchall(
            "SELECT name FROM sqlite_master "
            "WHERE name IN ('homes', 'home_aliases', 'home_redirects')"
        )
        await run_migrations(conn)
