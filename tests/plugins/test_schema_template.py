"""Schema-template activation, schema parity, fallback and resource ownership."""

import asyncio
import sqlite3

import aiosqlite
import pytest

from home_finder.db import PropertyStorage, migrations
from home_finder.db import storage as storage_module
from tests.plugins import schema_template


@pytest.fixture(autouse=True)
def _requires_the_plugin():
    if storage_module.run_migrations is not schema_template.run_migrations_from_template:
        pytest.skip("schema template plugin disabled (-p no:tests.plugins.schema_template)")


async def _schema(database) -> tuple[int, list[tuple[str, str, str]]]:
    async with PropertyStorage(str(database)) as storage, storage._read_connection() as conn:
        version = await (await conn.execute("PRAGMA user_version")).fetchone()
        cursor = await conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return version[0], [tuple(row) for row in await cursor.fetchall()]


async def test_fresh_in_memory_storage_takes_the_template_path(monkeypatch):
    """Without this, a broken fast path would silently fall back to real migrations."""
    await schema_template._migrated_template()
    real_calls = await _records_real_migrations(monkeypatch)
    assert (await _schema(":memory:"))[0] == len(migrations.MIGRATIONS)
    assert real_calls == []


async def test_template_schema_and_version_match_real_migrations(monkeypatch):
    from_template = await _schema(":memory:")
    monkeypatch.setattr(storage_module, "run_migrations", schema_template._real_run_migrations)
    migrated = await _schema(":memory:")

    assert from_template == migrated
    assert migrated[0] == len(migrations.MIGRATIONS)


async def test_file_database_is_migrated_for_real(tmp_path, monkeypatch):
    real_calls = await _records_real_migrations(monkeypatch)
    async with PropertyStorage(str(tmp_path / "file.db")):
        pass
    assert len(real_calls) == 1


async def _records_real_migrations(
    monkeypatch, *, delegate: bool = True
) -> list[aiosqlite.Connection]:
    calls = []

    real = schema_template._real_run_migrations

    async def recording_run_migrations(conn):
        calls.append(conn)
        return await real(conn) if delegate else 0

    monkeypatch.setattr(schema_template, "_real_run_migrations", recording_run_migrations)
    return calls


async def test_existing_database_is_migrated_for_real(monkeypatch):
    calls = await _records_real_migrations(monkeypatch)
    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute("CREATE TABLE legacy (id INTEGER)")
        await schema_template.run_migrations_from_template(conn)
    assert len(calls) == 1


async def test_edited_migration_list_is_migrated_for_real(monkeypatch):
    # The edited list cannot migrate a database, so only record the attempt.
    calls = await _records_real_migrations(monkeypatch, delegate=False)

    async def replacement(conn):
        pass

    original = migrations.MIGRATIONS[0]
    migrations.MIGRATIONS[0] = replacement
    try:
        async with aiosqlite.connect(":memory:") as conn:
            await schema_template.run_migrations_from_template(conn)
    finally:
        migrations.MIGRATIONS[0] = original
    assert len(calls) == 1


@pytest.fixture
def template_connections(monkeypatch):
    """Observe only native template handles; aiosqlite's source handles are separate."""
    opened = []
    connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        if kwargs.get("check_same_thread") is False:
            opened.append(connection)
        return connection

    monkeypatch.setattr(schema_template, "_template", None)
    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    yield opened
    for connection in opened:
        connection.close()


async def test_concurrent_first_storages_share_one_template_and_close_the_unused_copy(
    monkeypatch, template_connections
):
    real = schema_template._real_run_migrations
    both_started = asyncio.Event()
    starts = 0

    async def migrate_together(connection):
        nonlocal starts
        starts += 1
        if starts == 2:
            both_started.set()
        await both_started.wait()
        return await real(connection)

    monkeypatch.setattr(schema_template, "_real_run_migrations", migrate_together)
    first, second = await asyncio.gather(
        schema_template._migrated_template(), schema_template._migrated_template()
    )
    assert first is second
    assert first.execute("PRAGMA user_version").fetchone()[0] == len(migrations.MIGRATIONS)
    assert len(template_connections) == 2
    unused = next(connection for connection in template_connections if connection is not first)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        unused.execute("SELECT 1")


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_failed_template_build_closes_the_native_handle(
    monkeypatch, template_connections, error
):
    async def fail(connection):
        raise error("fixture")

    monkeypatch.setattr(schema_template, "_real_run_migrations", fail)
    with pytest.raises(error, match="fixture"):
        await schema_template._migrated_template()
    assert schema_template._template is None
    assert len(template_connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        template_connections[0].execute("SELECT 1")
