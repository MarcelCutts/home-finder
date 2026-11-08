"""Start each fresh test database from a migrated template instead of migrating it.

About 1,600 tests open a fresh ``PropertyStorage``, about 1,100 of them in memory.
Running every schema migration takes 10-35 ms per database, while copying a
migrated database with SQLite's backup API takes well under 1 ms.

Each worker reuses a template built by the real migrations, so a broken
migration still fails every test that opens storage. It is used only for an empty
in-memory database while ``MIGRATIONS`` is the production list. File databases,
upgrade tests that start from an older schema, tests that patch ``MIGRATIONS`` and
direct ``run_migrations`` calls therefore always run the real migrations.

Disable it with ``-p no:tests.plugins.schema_template`` to rule it out.
"""

from __future__ import annotations

import sqlite3
import threading

import aiosqlite
import pytest

from home_finder.db import migrations
from home_finder.db import storage as storage_module

_real_run_migrations = migrations.run_migrations
_production_migrations = tuple(migrations.MIGRATIONS)
_template: sqlite3.Connection | None = None
# One connection serves every copy; aiosqlite runs each storage on its own thread.
_template_lock = threading.Lock()


async def _migrated_template() -> sqlite3.Connection:
    global _template
    if _template is None:
        candidate = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            async with aiosqlite.connect(":memory:") as source:
                await _real_run_migrations(source)
                await source._execute(source._conn.backup, candidate)
        except BaseException:
            candidate.close()
            raise
        # Concurrent first storages can both reach the awaits above. Keep one
        # completed template and close the other rather than leaking its handle.
        with _template_lock:
            if _template is None:
                _template = candidate
            else:
                candidate.close()
    return _template


async def _is_empty_in_memory(conn: aiosqlite.Connection) -> bool:
    # File databases run in WAL mode, where the connection can keep a snapshot taken
    # before the copy and so not see the copied schema; they migrate for real.
    async with conn.execute(
        "SELECT (SELECT file FROM pragma_database_list WHERE name = 'main'),"
        " (SELECT user_version FROM pragma_user_version),"
        " (SELECT count(*) FROM sqlite_master)"
    ) as cursor:
        row = await cursor.fetchone()
    # PropertyStorage installs aiosqlite.Row, which never compares equal to a tuple.
    return row is not None and tuple(row) == ("", 0, 0)


async def run_migrations_from_template(conn: aiosqlite.Connection) -> int:
    # Compare contents, so a test that edits the list in place also migrates for real.
    if tuple(migrations.MIGRATIONS) != _production_migrations or not await _is_empty_in_memory(
        conn
    ):
        return await _real_run_migrations(conn)
    template = await _migrated_template()

    def copy() -> None:
        with _template_lock:
            template.backup(conn._conn)

    await conn._execute(copy)
    return len(_production_migrations)


def pytest_configure(config: pytest.Config) -> None:
    storage_module.run_migrations = run_migrations_from_template  # type: ignore[assignment]


def pytest_unconfigure(config: pytest.Config) -> None:
    global _template
    storage_module.run_migrations = _real_run_migrations
    if _template is not None:
        _template.close()
        _template = None
