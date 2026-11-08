"""The versioned migration runner and the data repairs individual migrations make.

These tests call ``run_migrations`` directly, so the suite's migrated template
database is never used here.
"""

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


async def _migrate_to(conn: aiosqlite.Connection, version: int) -> None:
    """Build the schema an older release left behind."""
    with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:version]):
        assert await run_migrations(conn) == version


def _recording(applied: list[str]):
    """Wrap each production migration so the test sees which ones ran."""

    def wrap(migration):
        async def run(conn: aiosqlite.Connection) -> None:
            applied.append(migration.__name__)
            await migration(conn)

        return run

    return [wrap(m) for m in MIGRATIONS]


async def test_analysis_exhaustion_upgrade_preserves_other_lifecycle_axes(fresh_conn):
    await _migrate_to(fresh_conn, 12)
    for uid, attempts, status in [
        ("exhausted", 3, "pending_analysis"),
        ("sent", 4, "sent"),
        ("retry", 2, "pending_analysis"),
    ]:
        await fresh_conn.execute(
            """INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm, bedrooms, address, first_seen,
                notification_status, analysis_attempts, user_status, enrichment_status
            ) VALUES (?, 'openrent', ?, 'https://example.com/test', 'Fixture', 1800, 1,
                      'Fixture address', '2026-10-01T00:00:00Z', ?, ?, 'interested', 'enriched')""",
            (uid, uid, status, attempts),
        )
    await fresh_conn.commit()
    before = [
        dict(row)
        for row in await (
            await fresh_conn.execute("SELECT * FROM properties ORDER BY 1")
        ).fetchall()
    ]
    await run_migrations(fresh_conn)
    after = [
        dict(row)
        for row in await (
            await fresh_conn.execute("SELECT * FROM properties ORDER BY 1")
        ).fetchall()
    ]
    for old, new in zip(before, after, strict=True):
        assert {key: new[key] for key in old} == old
        assert new["analysis_exhausted_reason"] == (
            "initial_failure_limit" if old["unique_id"] == "exhausted" else None
        )
    await run_migrations(fresh_conn)
    repeated = [
        dict(row)
        for row in await (
            await fresh_conn.execute("SELECT * FROM properties ORDER BY 1")
        ).fetchall()
    ]
    assert repeated == after


class TestRunMigrations:
    async def test_fresh_db_applies_all_migrations(self, fresh_conn: aiosqlite.Connection):
        """Fresh DB should run all migrations and set user_version = len(MIGRATIONS)."""
        version = await run_migrations(fresh_conn)

        assert version == len(MIGRATIONS)
        assert await _get_user_version(fresh_conn) == len(MIGRATIONS)

    async def test_fresh_db_creates_every_table(self, fresh_conn: aiosqlite.Connection):
        """The complete schema, including tables added by later migrations."""
        await run_migrations(fresh_conn)

        assert await _table_names(fresh_conn) == {
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
            "paid_attempts",
            "visual_checkpoints",
            "notification_deliveries",
        }

    async def test_current_db_runs_no_migration(self, fresh_conn: aiosqlite.Connection):
        await run_migrations(fresh_conn)
        applied: list[str] = []

        with patch("home_finder.db.migrations.MIGRATIONS", _recording(applied)):
            version = await run_migrations(fresh_conn)

        assert version == len(MIGRATIONS)
        assert applied == []

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

    async def test_partially_migrated_db_runs_only_the_remaining_migrations(
        self, fresh_conn: aiosqlite.Connection
    ):
        await _migrate_to(fresh_conn, 8)
        assert "paid_attempts" not in await _table_names(fresh_conn)
        await fresh_conn.execute(
            "INSERT INTO pipeline_runs (started_at, status) VALUES ('2026-01-01', 'completed')"
        )
        await fresh_conn.commit()
        applied: list[str] = []

        with patch("home_finder.db.migrations.MIGRATIONS", _recording(applied)):
            version = await run_migrations(fresh_conn)

        assert version == len(MIGRATIONS)
        assert applied == [m.__name__ for m in MIGRATIONS[8:]]
        assert {"paid_attempts", "notification_deliveries"} <= await _table_names(fresh_conn)
        cursor = await fresh_conn.execute("SELECT status FROM pipeline_runs")
        assert [row[0] for row in await cursor.fetchall()] == ["completed"]

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
            await conn.execute("CREATE TABLE IF NOT EXISTS _test_table (id INTEGER PRIMARY KEY)")

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
            await conn.execute("CREATE TABLE _should_not_persist (id INTEGER PRIMARY KEY)")
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
            "idx_source_listings_off_market",
            "idx_source_listings_url_unlinked",
            "idx_status_events_telegram_update",
            "idx_paid_attempts_operation",
            "idx_paid_attempts_admitted_at",
            "idx_paid_attempts_unresolved_operation",
            "idx_notification_delivery_state",
        }
        assert expected_indexes.issubset(indexes), f"Missing indexes: {expected_indexes - indexes}"


class TestMigration004SourceListingsBackfill:
    async def test_backfill_creates_canonical_source_listings(
        self, fresh_conn: aiosqlite.Connection
    ):
        """Pre-populated properties produce one source_listing each with merged_id set."""
        await _migrate_to(fresh_conn, 3)

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

    async def test_backfill_creates_secondary_from_aliases(self, fresh_conn: aiosqlite.Connection):
        """Source aliases produce source_listings with is_backfilled=1 and correct merged_id."""
        await _migrate_to(fresh_conn, 3)

        # Insert an anchor property with source_urls JSON
        source_urls = json.dumps(
            {
                "openrent": "https://openrent.com/123",
                "zoopla": "https://zoopla.co.uk/456",
            }
        )
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


_PROPERTY_COLUMNS = (
    "unique_id, source, source_id, url, title, price_pcm, bedrooms, address, first_seen,"
    " notification_status"
)
_LISTING_COLUMNS = (
    "unique_id, source, source_id, url, title, price_pcm, bedrooms, address, first_seen,"
    " last_seen, merged_id"
)


class TestMigration005SourceListingsLinkage:
    async def test_unlinked_listing_is_linked_to_the_home_naming_its_url(
        self, fresh_conn: aiosqlite.Connection
    ):
        await _migrate_to(fresh_conn, 4)
        source_urls = {"openrent": "https://openrent.com/123", "zoopla": "https://zoopla.co.uk/456"}
        await fresh_conn.execute(
            f"INSERT INTO properties ({_PROPERTY_COLUMNS}, sources, source_urls)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "openrent:123",
                "openrent",
                "123",
                source_urls["openrent"],
                "Flat",
                2000,
                2,
                "123 Mare Street",
                "2025-01-15T10:00:00",
                "sent",
                json.dumps(["openrent", "zoopla"]),
                json.dumps(source_urls),
            ),
        )
        for unique_id, url, price, merged_id in (
            ("openrent:123", source_urls["openrent"], 2000, "openrent:123"),
            ("zoopla:456", source_urls["zoopla"], 1950, None),
        ):
            source, source_id = unique_id.split(":")
            await fresh_conn.execute(
                f"INSERT INTO source_listings ({_LISTING_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    unique_id,
                    source,
                    source_id,
                    url,
                    "Flat",
                    price,
                    2,
                    "123 Mare Street",
                    "2025-01-15T10:00:00",
                    "2025-01-15T10:00:00",
                    merged_id,
                ),
            )
        await fresh_conn.commit()

        await run_migrations(fresh_conn)

        cursor = await fresh_conn.execute(
            "SELECT merged_id FROM source_listings WHERE unique_id = 'zoopla:456'"
        )
        assert (await cursor.fetchone())["merged_id"] == "openrent:123"

    async def test_duplicate_entries_in_sources_json_are_removed(
        self, fresh_conn: aiosqlite.Connection
    ):
        await _migrate_to(fresh_conn, 4)
        await fresh_conn.execute(
            f"INSERT INTO properties ({_PROPERTY_COLUMNS}, sources)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "rightmove:789",
                "rightmove",
                "789",
                "https://rightmove.co.uk/789",
                "Flat",
                2100,
                2,
                "45 Dalston Lane",
                "2025-01-16T14:00:00",
                "sent",
                json.dumps(["rightmove", "zoopla", "zoopla"]),
            ),
        )
        await fresh_conn.commit()

        await run_migrations(fresh_conn)

        cursor = await fresh_conn.execute(
            "SELECT sources FROM properties WHERE unique_id = 'rightmove:789'"
        )
        assert json.loads((await cursor.fetchone())["sources"]) == ["rightmove", "zoopla"]


async def _columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in await cursor.fetchall()}


async def test_migration_006_adds_off_market_tracking_columns(fresh_conn: aiosqlite.Connection):
    await _migrate_to(fresh_conn, 5)
    added_to_listings = {
        "is_off_market",
        "off_market_since",
        "off_market_reason",
        "last_checked_at",
    }
    added_to_properties = {"last_checked_at", "off_market_reason", "off_market_history"}
    assert not added_to_listings & await _columns(fresh_conn, "source_listings")
    assert not added_to_properties & await _columns(fresh_conn, "properties")

    await _migrate_to(fresh_conn, 6)

    assert added_to_listings <= await _columns(fresh_conn, "source_listings")
    assert added_to_properties <= await _columns(fresh_conn, "properties")


async def test_pre_versioned_database_has_wrapped_one_line_unwrapped(
    fresh_conn: aiosqlite.Connection,
):
    """Migration 001 repairs one_line stored as a JSON object by an older writer."""
    # A database from before versioning (user_version 0) already has these tables.
    await fresh_conn.execute(
        """CREATE TABLE properties (
            unique_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
            url TEXT NOT NULL, title TEXT NOT NULL, price_pcm INTEGER NOT NULL,
            bedrooms INTEGER NOT NULL, address TEXT NOT NULL, postcode TEXT, latitude REAL,
            longitude REAL, description TEXT, image_url TEXT, available_from TEXT,
            first_seen TEXT NOT NULL, commute_minutes INTEGER, transport_mode TEXT,
            notification_status TEXT NOT NULL DEFAULT 'pending', notified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sources TEXT, source_urls TEXT, min_price INTEGER, max_price INTEGER
        )"""
    )
    await fresh_conn.execute(
        """CREATE TABLE quality_analyses (
            property_unique_id TEXT PRIMARY KEY, analysis_json TEXT NOT NULL,
            overall_rating INTEGER, condition_concerns BOOLEAN DEFAULT 0,
            concern_severity TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await fresh_conn.execute(
        "INSERT INTO quality_analyses (property_unique_id, analysis_json, created_at)"
        " VALUES (?, ?, '2026-01-01')",
        ("openrent:100", json.dumps({"one_line": {"one_line": "Bright flat"}, "summary": "Good"})),
    )
    await fresh_conn.commit()

    await run_migrations(fresh_conn)

    cursor = await fresh_conn.execute(
        "SELECT json_extract(analysis_json, '$.one_line') FROM quality_analyses"
    )
    assert (await cursor.fetchone())[0] == "Bright flat"
