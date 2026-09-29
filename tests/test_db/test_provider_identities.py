"""Additive identity migration and atomic writes preserve existing home records."""

import json
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from home_finder.db import PropertyStorage
from home_finder.db.identities import record_identity_claims
from home_finder.db.migrations import MIGRATIONS, run_migrations
from home_finder.models import MergedProperty, Property, PropertySource
from home_finder.models.identity import SourceIdentityInput


def property_for(source_id="123"):
    return Property.model_validate(
        {
            "source": "rightmove",
            "source_id": source_id,
            "url": f"https://www.rightmove.co.uk/properties/{source_id}",
            "title": "Identity fixture",
            "price_pcm": 2000,
            "bedrooms": 2,
            "address": "Fixture address",
        }
    )


def merged_for(prop):
    return MergedProperty(
        canonical=prop,
        sources=(PropertySource.RIGHTMOVE,),
        source_urls={PropertySource.RIGHTMOVE: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


@pytest.fixture
async def storage():
    async with PropertyStorage(":memory:") as db:
        yield db


async def table_rows(conn, table):
    async with conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid') as cursor:
        return [tuple(row) for row in await cursor.fetchall()]


async def save_via(storage, path, prop):
    merged = merged_for(prop)
    if path == "search":
        await storage.upsert_source_listings([prop])
    elif path == "property":
        await storage.save_property(prop)
    elif path == "merged":
        await storage.save_merged_property(merged)
    elif path == "dropped":
        await storage.pipeline.save_dropped_properties([merged], {})
    elif path == "unenriched":
        await storage.pipeline.save_unenriched_property(merged)
    elif path == "pre_analysis":
        await storage.pipeline.save_pre_analysis_properties([merged], {})
    else:
        raise AssertionError(path)


WRITE_PATHS = ["search", "property", "merged", "dropped", "unenriched", "pre_analysis"]


@pytest.mark.parametrize("path", WRITE_PATHS)
async def test_all_source_write_paths_register_identity_atomically_and_idempotently(storage, path):
    prop = property_for()
    await save_via(storage, path, prop)
    conn = await storage._get_connection()
    before = await table_rows(conn, "source_identity_claims")
    assert len(before) == 1
    assert len(await table_rows(conn, "provider_listings")) == 1
    await save_via(storage, path, prop.model_copy(update={"price_pcm": 2100}))
    assert await table_rows(conn, "source_identity_claims") == before
    assert len(await table_rows(conn, "provider_listings")) == 1
    audit = await storage.identities.audit()
    assert audit.resolved_rows == audit.provider_entities == 1
    assert audit.rows[0].reason == "provider_id_and_url_agree"


@pytest.mark.parametrize("path", WRITE_PATHS)
async def test_identity_write_failure_rolls_back_source_and_home_writes(storage, path):
    conn = await storage._get_connection()
    await conn.execute("""
        CREATE TRIGGER inject_identity_failure BEFORE INSERT ON source_identity_claims
        BEGIN SELECT RAISE(ABORT, 'injected identity failure'); END
    """)
    await conn.commit()
    with pytest.raises(aiosqlite.IntegrityError, match="injected identity failure"):
        await save_via(storage, path, property_for())
    for table in ("source_listings", "properties", "provider_listings", "source_identity_claims"):
        assert await table_rows(conn, table) == []


async def test_multiple_same_portal_members_survive_as_distinct_provider_listings(storage):
    first, second = property_for(), property_for("456")
    await storage.save_property(first)
    await storage.upsert_source_listings([second])
    await storage.link_source_listings([(second.unique_id, first.unique_id)])
    members = await storage.identities.members(first.unique_id)
    assert {member.provider_id for member in members} == {"123", "456"}
    assert len({member.listing_key for member in members}) == 2
    assert (await storage.identities.audit()).owner_conflict_groups == 0
    assert await storage.identities.members("absent") == ()


async def test_upsert_claim_describes_stored_url_and_keeps_backfill_provenance(storage):
    prop = property_for()
    await storage.upsert_source_listings([prop])
    # Historical rows retain their original URL and backfill flag on refresh.
    async with storage._transaction() as conn:
        await conn.execute("UPDATE source_listings SET is_backfilled = 1")
    refreshed = prop.model_copy(update={"url": str(prop.url) + "?fresh=1"})
    await storage.upsert_source_listings([refreshed])
    conn = await storage._get_connection()
    claims = await conn.execute_fetchall(
        "SELECT url, is_backfilled FROM source_identity_claims ORDER BY is_backfilled"
    )
    assert [tuple(row) for row in claims] == [(str(prop.url), 0), (str(prop.url), 1)]
    await storage.upsert_source_listings([refreshed])
    assert len(await table_rows(conn, "source_identity_claims")) == 2


@pytest.mark.parametrize("table", ["provider_listings", "source_identity_claims"])
@pytest.mark.parametrize("operation", ["DELETE FROM", "UPDATE"])
async def test_identity_evidence_cannot_be_overwritten_or_deleted(storage, table, operation):
    await storage.upsert_source_listings([property_for()])
    conn = await storage._get_connection()
    query = f"{operation} {table}" + (" SET source = 'zoopla'" if operation == "UPDATE" else "")
    with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
        await conn.execute(query)
    await conn.rollback()
    assert len(await table_rows(conn, table)) == 1


async def test_new_representation_retains_old_claim_and_source_row_deletion_retains_evidence(
    storage,
):
    prop = property_for()
    await storage.upsert_source_listings([prop])
    original = SourceIdentityInput(
        unique_id=prop.unique_id,
        source="rightmove",
        source_id="123",
        url=str(prop.url),
    )
    updated = original.model_copy(update={"url": str(prop.url) + "?updated=1"})
    async with storage._transaction() as conn:
        await record_identity_claims(conn, [updated])
        await conn.execute("DELETE FROM source_listings WHERE unique_id = ?", (prop.unique_id,))
    conn = await storage._get_connection()
    assert len(await table_rows(conn, "provider_listings")) == 1
    assert len(await table_rows(conn, "source_identity_claims")) == 2
    assert (await storage.identities.audit()).source_rows == 0


async def seed_legacy(conn):
    with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:10]):
        await run_migrations(conn)
    for home in ("rightmove:123", "onthemarket:789"):
        source, source_id = home.split(":")
        await conn.execute(
            "INSERT INTO properties (unique_id, source, source_id, url, title, price_pcm, "
            "bedrooms, address, first_seen, user_status) VALUES (?, ?, ?, ?, ?, 2000, 2, ?, ?, ?)",
            (
                home,
                source,
                source_id,
                "https://example.test/saved",
                "Saved",
                "Address",
                "old",
                "liked",
            ),
        )
    for source_id, home, backfilled in (
        ("123", "rightmove:123", 0),
        ("https://rightmove.co.uk/properties/123?old=1", "onthemarket:789", 1),
        ("opaque", None, 1),
    ):
        await conn.execute(
            "INSERT INTO source_listings (unique_id, source, source_id, url, title, price_pcm, "
            "bedrooms, address, first_seen, last_seen, merged_id, is_backfilled) "
            "VALUES (?, 'rightmove', ?, ?, 'Saved', 2000, 2, 'Address', 'old', 'old', ?, ?)",
            (
                f"rightmove:{source_id}",
                source_id,
                "https://www.rightmove.co.uk/properties/123",
                home,
                backfilled,
            ),
        )
    await conn.execute(
        "INSERT INTO price_history (property_unique_id, old_price, new_price, change_amount, "
        "detected_at) VALUES (?, ?, ?, ?, ?)",
        ("rightmove:123", 1900, 2000, 100, "old"),
    )
    await conn.commit()


async def test_migration_preserves_every_old_row_alias_history_and_conflicting_owner(tmp_path):
    database = tmp_path / "legacy.db"
    async with aiosqlite.connect(database) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await seed_legacy(conn)
        tables = [
            row[0]
            for row in await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        before = {table: await table_rows(conn, table) for table in tables}
        await run_migrations(conn)
        assert {table: await table_rows(conn, table) for table in tables} == before
        claims = await table_rows(conn, "source_identity_claims")
        assert len(claims) == 3
        assert len(await table_rows(conn, "provider_listings")) == 1
        await run_migrations(conn)
        assert await table_rows(conn, "source_identity_claims") == claims
        assert await conn.execute_fetchall("PRAGMA foreign_key_check") == []
    async with PropertyStorage(str(database)) as storage:
        audit = await storage.identities.audit()
        assert audit.owner_conflict_groups == 1
        assert audit.unresolved_rows == 1
        assert audit.groups[0].current_home_ids == ("onthemarket:789", "rightmove:123")
        conn = await storage._get_connection()
        assert {table: await table_rows(conn, table) for table in tables} == before
        assert json.loads(audit.model_dump_json())["reason_counts"] == {
            "legacy_url_and_url_agree": 1,
            "provider_id_and_url_agree": 1,
            "unresolved_declared_id": 1,
        }


async def test_failed_backfill_rolls_back_schema_and_version():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await seed_legacy(conn)
        fail = AsyncMock(side_effect=RuntimeError("injected backfill failure"))
        with (
            patch("home_finder.db.identities.record_identity_claims", fail),
            pytest.raises(RuntimeError, match="injected backfill failure"),
        ):
            await run_migrations(conn)
        assert (await conn.execute_fetchall("PRAGMA user_version"))[0][0] == 10
        assert (
            await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('provider_listings', 'source_identity_claims')"
            )
            == []
        )
        assert len(await table_rows(conn, "source_listings")) == 3
        await run_migrations(conn)
        assert len(await table_rows(conn, "source_identity_claims")) == 3
