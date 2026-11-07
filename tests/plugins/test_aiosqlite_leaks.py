"""The leak check closes a test's unclosed connections and leaves older ones alone."""

import sqlite3

import aiosqlite

from tests.plugins.aiosqlite_leaks import close_leaked_connections, connection_worker_threads


async def test_closed_connection_never_scans_the_heap(monkeypatch):
    from tests.plugins import aiosqlite_leaks

    before = connection_worker_threads()
    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("SELECT 1")
    connection._thread.join(timeout=1)

    def unexpected_scan():
        raise AssertionError("Healthy tests must not scan the heap")

    monkeypatch.setattr(aiosqlite_leaks.gc, "get_objects", unexpected_scan)
    assert close_leaked_connections(before) == []


async def test_leaked_connection_is_closed_and_releases_its_lock(tmp_path):
    database = tmp_path / "leaked.db"
    before = connection_worker_threads()
    leaked = await aiosqlite.connect(database)
    await leaked.execute("CREATE TABLE t (x)")
    await leaked.execute("BEGIN IMMEDIATE")  # holds the write lock until closed

    assert close_leaked_connections(before) == [str(database)]

    assert not leaked._thread.is_alive()
    other = sqlite3.connect(database, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")  # raises "database is locked" if still held
    finally:
        other.close()


async def test_connection_open_before_the_test_is_left_usable():
    owned = await aiosqlite.connect(":memory:")
    try:
        before = connection_worker_threads()

        assert close_leaked_connections(before) == []

        assert await (await owned.execute("SELECT 1")).fetchone() == (1,)
    finally:
        await owned.close()


async def test_closed_connection_is_not_reported():
    before = connection_worker_threads()
    connection = await aiosqlite.connect(":memory:")
    await connection.close()

    assert close_leaked_connections(before) == []
