"""Tests for the versioned migration runner."""

from __future__ import annotations

import json
from unittest.mock import patch

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
            "source_aliases",
            "source_listings",
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

    async def test_multi_migration_sequence(self, fresh_conn: aiosqlite.Connection):
        """A DB at current version should skip existing and run only new migrations."""
        # Run real migrations first to get to current version
        await run_migrations(fresh_conn)
        current_version = len(MIGRATIONS)
        assert await _get_user_version(fresh_conn) == current_version

        # Define a mock next migration that adds a new table
        async def migrate_next_add_test_table(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS _test_table (id INTEGER PRIMARY KEY)"
            )

        fake_migrations = [*MIGRATIONS, migrate_next_add_test_table]
        with patch("home_finder.db.migrations.MIGRATIONS", fake_migrations):
            version = await run_migrations(fresh_conn)

        assert version == current_version + 1
        assert await _get_user_version(fresh_conn) == current_version + 1
        # Verify the new migration actually ran
        tables = await _table_names(fresh_conn)
        assert "_test_table" in tables

    async def test_failed_migration_leaves_version_unchanged(
        self, fresh_conn: aiosqlite.Connection
    ):
        """A migration that raises should roll back, leaving user_version unchanged."""
        await run_migrations(fresh_conn)
        current_version = len(MIGRATIONS)
        assert await _get_user_version(fresh_conn) == current_version

        async def migrate_next_failing(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "CREATE TABLE _should_not_persist (id INTEGER PRIMARY KEY)"
            )
            raise RuntimeError("simulated failure")

        fake_migrations = [*MIGRATIONS, migrate_next_failing]
        with (
            patch("home_finder.db.migrations.MIGRATIONS", fake_migrations),
            pytest.raises(RuntimeError, match="simulated failure"),
        ):
            await run_migrations(fresh_conn)

        # Version must still be at current — the failed migration was rolled back
        assert await _get_user_version(fresh_conn) == current_version
        # The table created inside the failed migration should not exist
        tables = await _table_names(fresh_conn)
        assert "_should_not_persist" not in tables

    async def test_forward_version_guard(self, fresh_conn: aiosqlite.Connection):
        """A DB with a higher version than MIGRATIONS should raise RuntimeError."""
        await fresh_conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
        await fresh_conn.commit()

        with pytest.raises(RuntimeError, match="newer than"):
            await run_migrations(fresh_conn)

    async def test_critical_indexes_exist(self, fresh_conn: aiosqlite.Connection):
        """Critical indexes should exist after running migrations."""
        await run_migrations(fresh_conn)

        cursor = await fresh_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        rows = await cursor.fetchall()
        indexes = {r[0] for r in rows}

        expected_indexes = {
            "idx_notification_status",
            "idx_source",
            "idx_first_seen",
            "idx_property_images_property",
            "idx_quality_rating",
            "idx_enrichment_status",
            "idx_user_status",
            "idx_status_events_property",
            "idx_price_history_property",
            "idx_price_history_detected",
            "idx_off_market",
            "idx_quality_fit_score",
            "idx_scraper_runs_pipeline",
            "idx_property_events_run",
            "idx_property_events_property",
            "idx_enquiry_log_property",
            "idx_source_aliases_anchor",
            "idx_source_aliases_source",
            "idx_source_listings_source",
            "idx_source_listings_merged",
            "idx_source_listings_unmatched",
        }
        assert expected_indexes.issubset(indexes), f"Missing indexes: {expected_indexes - indexes}"


class TestBackfillSourceListings:
    """Tests for the source_listings backfill in migrate_004."""

    async def test_backfill_creates_canonical_source_listings(
        self, fresh_conn: aiosqlite.Connection
    ):
        """Pre-populated properties produce one source_listing each with merged_id set."""
        # Run all migrations except 004
        from home_finder.db.migrations import (
            migrate_001_initial_schema,
            migrate_002_floor_area_sqm,
            migrate_003_source_aliases,
        )

        await fresh_conn.execute("BEGIN IMMEDIATE")
        await migrate_001_initial_schema(fresh_conn)
        await fresh_conn.execute("PRAGMA user_version = 1")
        await fresh_conn.commit()
        await fresh_conn.execute("BEGIN IMMEDIATE")
        await migrate_002_floor_area_sqm(fresh_conn)
        await fresh_conn.execute("PRAGMA user_version = 2")
        await fresh_conn.commit()
        await fresh_conn.execute("BEGIN IMMEDIATE")
        await migrate_003_source_aliases(fresh_conn)
        await fresh_conn.execute("PRAGMA user_version = 3")
        await fresh_conn.commit()

        # Insert a property
        await fresh_conn.execute(
            """INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, first_seen, notification_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "openrent:123",
                "openrent",
                "123",
                "https://openrent.com/123",
                "Nice flat",
                2000,
                2,
                "123 Mare Street",
                "E8 3RH",
                "2025-01-15T10:00:00",
                "sent",
            ),
        )
        await fresh_conn.commit()

        # Now run migration 004
        await run_migrations(fresh_conn)

        cursor = await fresh_conn.execute(
            "SELECT * FROM source_listings WHERE unique_id = ?", ("openrent:123",)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["source"] == "openrent"
        assert row["source_id"] == "123"
        assert row["merged_id"] == "openrent:123"
        assert row["is_backfilled"] == 0
        assert row["price_pcm"] == 2000

    async def test_backfill_creates_secondary_from_aliases(
        self, fresh_conn: aiosqlite.Connection
    ):
        """Source aliases produce source_listings with is_backfilled=1 and correct merged_id."""
        from home_finder.db.migrations import (
            migrate_001_initial_schema,
            migrate_002_floor_area_sqm,
            migrate_003_source_aliases,
        )

        await fresh_conn.execute("BEGIN IMMEDIATE")
        await migrate_001_initial_schema(fresh_conn)
        await fresh_conn.execute("PRAGMA user_version = 1")
        await fresh_conn.commit()
        await fresh_conn.execute("BEGIN IMMEDIATE")
        await migrate_002_floor_area_sqm(fresh_conn)
        await fresh_conn.execute("PRAGMA user_version = 2")
        await fresh_conn.commit()
        await fresh_conn.execute("BEGIN IMMEDIATE")
        await migrate_003_source_aliases(fresh_conn)
        await fresh_conn.execute("PRAGMA user_version = 3")
        await fresh_conn.commit()

        # Insert an anchor property with source_urls JSON
        source_urls = json.dumps({
            "openrent": "https://openrent.com/123",
            "zoopla": "https://zoopla.co.uk/456",
        })
        await fresh_conn.execute(
            """INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, first_seen, notification_status,
                sources, source_urls
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "openrent:123",
                "openrent",
                "123",
                "https://openrent.com/123",
                "Nice flat",
                2000,
                2,
                "123 Mare Street",
                "E8 3RH",
                "2025-01-15T10:00:00",
                "sent",
                json.dumps(["openrent", "zoopla"]),
                source_urls,
            ),
        )
        # Insert alias
        await fresh_conn.execute(
            """INSERT INTO source_aliases (unique_id, source, source_id, anchor_id)
               VALUES (?, ?, ?, ?)""",
            ("zoopla:456", "zoopla", "456", "openrent:123"),
        )
        await fresh_conn.commit()

        # Run migration 004
        await run_migrations(fresh_conn)

        # Check canonical source listing
        cursor = await fresh_conn.execute(
            "SELECT * FROM source_listings WHERE unique_id = ?", ("openrent:123",)
        )
        canonical = await cursor.fetchone()
        assert canonical is not None
        assert canonical["is_backfilled"] == 0

        # Check secondary source listing from alias
        cursor = await fresh_conn.execute(
            "SELECT * FROM source_listings WHERE unique_id = ?", ("zoopla:456",)
        )
        secondary = await cursor.fetchone()
        assert secondary is not None
        assert secondary["source"] == "zoopla"
        assert secondary["source_id"] == "456"
        assert secondary["merged_id"] == "openrent:123"
        assert secondary["is_backfilled"] == 1
        # URL should come from source_urls JSON
        assert secondary["url"] == "https://zoopla.co.uk/456"
