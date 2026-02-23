"""Tests for the versioned migration runner."""

from __future__ import annotations

import aiosqlite
import pytest

from home_finder.db.migrations import MIGRATIONS, run_migrations


@pytest.fixture
async def fresh_conn():
    """Provide an in-memory SQLite connection with row_factory."""
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def _get_user_version(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def _table_names(conn: aiosqlite.Connection) -> set[str]:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    rows = await cursor.fetchall()
    return {r[0] for r in rows}


class TestRunMigrations:
    async def test_fresh_db_applies_all_migrations(self, fresh_conn: aiosqlite.Connection):
        """Fresh DB should run all migrations and set user_version = len(MIGRATIONS)."""
        version = await run_migrations(fresh_conn)

        assert version == len(MIGRATIONS)
        assert await _get_user_version(fresh_conn) == len(MIGRATIONS)

    async def test_fresh_db_creates_all_tables(self, fresh_conn: aiosqlite.Connection):
        """All expected tables should exist after running migrations."""
        await run_migrations(fresh_conn)

        tables = await _table_names(fresh_conn)
        expected = {
            "properties",
            "property_images",
            "quality_analyses",
            "pipeline_runs",
            "status_events",
            "viewing_messages",
            "price_history",
            "rent_benchmarks",
            "enquiry_log",
            "scraper_runs",
            "property_events",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    async def test_idempotent_double_run(self, fresh_conn: aiosqlite.Connection):
        """Running migrations twice should not error and should keep same version."""
        v1 = await run_migrations(fresh_conn)
        v2 = await run_migrations(fresh_conn)

        assert v1 == v2
        assert v1 == len(MIGRATIONS)

    async def test_existing_db_transitions_from_version_zero(
        self, fresh_conn: aiosqlite.Connection
    ):
        """A DB with all tables but user_version=0 should transition cleanly.

        This simulates the production upgrade path: tables exist from the old
        initialize() code, but PRAGMA user_version was never set.
        """
        # First run creates everything
        await run_migrations(fresh_conn)
        # Reset user_version to 0 to simulate pre-migration DB
        await fresh_conn.execute("PRAGMA user_version = 0")
        await fresh_conn.commit()

        assert await _get_user_version(fresh_conn) == 0

        # Second run should succeed (IF NOT EXISTS / duplicate-column guards)
        version = await run_migrations(fresh_conn)

        assert version == len(MIGRATIONS)
        assert await _get_user_version(fresh_conn) == len(MIGRATIONS)

    async def test_version_matches_migration_count(self, fresh_conn: aiosqlite.Connection):
        """Final user_version should always equal len(MIGRATIONS)."""
        await run_migrations(fresh_conn)

        assert await _get_user_version(fresh_conn) == len(MIGRATIONS)

    async def test_partially_migrated_db_resumes(self, fresh_conn: aiosqlite.Connection):
        """If user_version is already at len(MIGRATIONS), no migration runs."""
        await run_migrations(fresh_conn)

        # Set version to current — run_migrations should be a no-op
        version = await run_migrations(fresh_conn)
        assert version == len(MIGRATIONS)

    async def test_migrations_list_not_empty(self):
        """MIGRATIONS list should contain at least the bootstrap migration."""
        assert len(MIGRATIONS) >= 1
        assert MIGRATIONS[0].__name__ == "migrate_001_initial_schema"
