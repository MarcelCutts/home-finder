"""Versioned schema migrations using SQLite PRAGMA user_version."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import aiosqlite

from home_finder.logging import get_logger

logger = get_logger(__name__)


async def migrate_001_initial_schema(conn: aiosqlite.Connection) -> None:
    """Bootstrap migration: full schema as of the initial extraction.

    Uses IF NOT EXISTS / duplicate-column guards so it works on both fresh DBs
    and existing production DBs that have user_version=0 but already have all tables.
    """
    # --- Core tables ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            unique_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            price_pcm INTEGER NOT NULL,
            bedrooms INTEGER NOT NULL,
            address TEXT NOT NULL,
            postcode TEXT,
            latitude REAL,
            longitude REAL,
            description TEXT,
            image_url TEXT,
            available_from TEXT,
            first_seen TEXT NOT NULL,
            commute_minutes INTEGER,
            transport_mode TEXT,
            notification_status TEXT NOT NULL DEFAULT 'pending',
            notified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sources TEXT,
            source_urls TEXT,
            min_price INTEGER,
            max_price INTEGER
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_notification_status
        ON properties(notification_status)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source
        ON properties(source)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_first_seen
        ON properties(first_seen)
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS property_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_unique_id TEXT NOT NULL,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            image_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id) ON DELETE CASCADE,
            UNIQUE(property_unique_id, url)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_property_images_property
        ON property_images(property_unique_id)
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS quality_analyses (
            property_unique_id TEXT PRIMARY KEY,
            analysis_json TEXT NOT NULL,
            overall_rating INTEGER,
            condition_concerns BOOLEAN DEFAULT 0,
            concern_severity TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_quality_rating
        ON quality_analyses(overall_rating)
    """)

    # --- Migrate: add columns to properties ---
    for column, col_type, default in [
        ("sources", "TEXT", None),
        ("source_urls", "TEXT", None),
        ("min_price", "INTEGER", None),
        ("max_price", "INTEGER", None),
        ("descriptions_json", "TEXT", None),
        ("enrichment_status", "TEXT", "'enriched'"),
        ("enrichment_attempts", "INTEGER", "0"),
        ("ward", "TEXT", None),
        ("analysis_attempts", "INTEGER", "0"),
        ("user_status", "TEXT", "'new'"),
        ("floor_area_sqft", "INTEGER", None),
        ("floor_area_source", "TEXT", None),
    ]:
        try:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            await conn.execute(
                f"ALTER TABLE properties ADD COLUMN {column} {col_type}{default_clause}"
            )
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_enrichment_status
        ON properties(enrichment_status)
    """)

    # --- Pipeline runs table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            scraped_count INTEGER DEFAULT 0,
            new_count INTEGER DEFAULT 0,
            enriched_count INTEGER DEFAULT 0,
            analyzed_count INTEGER DEFAULT 0,
            notified_count INTEGER DEFAULT 0,
            anchors_updated INTEGER DEFAULT 0,
            error_message TEXT,
            duration_seconds REAL
        )
    """)

    # --- Migrate: add sources_updated_at to properties ---
    for column, col_type, default in [
        ("sources_updated_at", "TEXT", None),
    ]:
        try:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            await conn.execute(
                f"ALTER TABLE properties ADD COLUMN {column} {col_type}{default_clause}"
            )
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    # --- Migrate: add denormalized quality columns ---
    for column, col_type in [
        ("epc_rating", "TEXT"),
        ("has_outdoor_space", "BOOLEAN"),
        ("red_flag_count", "INTEGER"),
        ("reanalysis_requested_at", "TEXT"),
    ]:
        try:
            await conn.execute(
                f"ALTER TABLE quality_analyses ADD COLUMN {column} {col_type}"
            )
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    # --- Migrate: fix one_line fields stored as JSON objects ---
    try:
        await conn.execute("""
            UPDATE quality_analyses
            SET analysis_json = json_set(
                analysis_json,
                '$.one_line',
                json_extract(json_extract(analysis_json, '$.one_line'), '$.one_line')
            )
            WHERE json_valid(analysis_json)
              AND json_type(json_extract(analysis_json, '$.one_line')) = 'object'
        """)
    except aiosqlite.OperationalError as e:
        err_msg = str(e).lower()
        if "no such column" in err_msg or "no such function" in err_msg or "json" in err_msg:
            pass
        else:
            raise

    # --- Migrate: add fit_score columns to quality_analyses ---
    try:
        await conn.execute(
            "ALTER TABLE quality_analyses ADD COLUMN fit_score INTEGER"
        )
    except aiosqlite.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quality_fit_score ON quality_analyses(fit_score)"
    )

    try:
        await conn.execute(
            "ALTER TABLE quality_analyses ADD COLUMN fit_score_version INTEGER"
        )
    except aiosqlite.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    # --- User status index ---
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_status ON properties(user_status)
    """)

    # --- Status events table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS status_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_unique_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            note TEXT,
            source TEXT NOT NULL DEFAULT 'web',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_status_events_property
            ON status_events(property_unique_id)
    """)

    # --- Viewing messages table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS viewing_messages (
            property_unique_id TEXT PRIMARY KEY,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id) ON DELETE CASCADE
        )
    """)

    # --- Price history table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_unique_id TEXT NOT NULL,
            old_price INTEGER NOT NULL,
            new_price INTEGER NOT NULL,
            change_amount INTEGER NOT NULL,
            source TEXT,
            detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_history_property
            ON price_history(property_unique_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_history_detected
            ON price_history(detected_at)
    """)

    # --- Rent benchmarks table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rent_benchmarks (
            outcode TEXT NOT NULL,
            bedrooms INTEGER NOT NULL,
            median_rent INTEGER NOT NULL,
            mean_rent INTEGER NOT NULL,
            sample_count INTEGER NOT NULL,
            computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (outcode, bedrooms)
        )
    """)

    # --- Enquiry log table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS enquiry_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_unique_id TEXT NOT NULL,
            portal TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            submitted_at TEXT,
            error TEXT,
            screenshot_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id) ON DELETE CASCADE,
            UNIQUE(property_unique_id, portal)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_enquiry_log_property
            ON enquiry_log(property_unique_id)
    """)

    # --- Migrate: add price_drop_notified and off-market columns ---
    for column, col_type, default in [
        ("price_drop_notified", "INTEGER", "0"),
        ("is_off_market", "INTEGER", "0"),
        ("off_market_since", "TEXT", None),
    ]:
        try:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            await conn.execute(
                f"ALTER TABLE properties ADD COLUMN {column} {col_type}{default_clause}"
            )
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_off_market ON properties(is_off_market)"
    )

    # --- Migrate: add per-stage timing columns to pipeline_runs ---
    for column in [
        "scraping_seconds",
        "filtering_seconds",
        "enrichment_seconds",
        "analysis_seconds",
        "notification_seconds",
    ]:
        try:
            await conn.execute(f"ALTER TABLE pipeline_runs ADD COLUMN {column} REAL")
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    # --- Migrate: add funnel counts + API cost columns to pipeline_runs ---
    for column in [
        "criteria_filtered_count",
        "location_filtered_count",
        "new_property_count",
        "commute_within_limit_count",
        "post_dedup_count",
        "post_floorplan_count",
        "total_input_tokens",
        "total_output_tokens",
        "total_cache_read_tokens",
        "total_cache_creation_tokens",
    ]:
        try:
            await conn.execute(
                f"ALTER TABLE pipeline_runs ADD COLUMN {column} INTEGER"
            )
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    try:
        await conn.execute(
            "ALTER TABLE pipeline_runs ADD COLUMN estimated_cost_usd REAL"
        )
    except aiosqlite.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    # --- Scraper runs table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS scraper_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_run_id INTEGER,
            scraper_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            duration_seconds REAL,
            areas_attempted INTEGER DEFAULT 0,
            areas_completed INTEGER DEFAULT 0,
            properties_found INTEGER DEFAULT 0,
            pages_fetched INTEGER DEFAULT 0,
            pages_failed INTEGER DEFAULT 0,
            parse_errors INTEGER DEFAULT 0,
            is_healthy BOOLEAN DEFAULT 1,
            error_message TEXT,
            FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_scraper_runs_pipeline
        ON scraper_runs(pipeline_run_id)
    """)

    # --- Property events table ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS property_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            property_id TEXT NOT NULL,
            source TEXT NOT NULL,
            event_type TEXT NOT NULL,
            stage TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_property_events_run
        ON property_events(run_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_property_events_property
        ON property_events(property_id)
    """)


# Ordered registry of migration functions.
# To add a new migration: write an async function above and append it here.
_MigrationFn = Callable[[aiosqlite.Connection], Coroutine[Any, Any, None]]

MIGRATIONS: list[_MigrationFn] = [
    migrate_001_initial_schema,
]


async def run_migrations(conn: aiosqlite.Connection) -> int:
    """Run unapplied migrations and return the final schema version.

    Uses SQLite's built-in ``PRAGMA user_version`` to track which migrations
    have been applied.  Each migration runs inside an explicit
    ``BEGIN IMMEDIATE … COMMIT`` transaction so the DDL statements and the
    version bump are atomic — a crash mid-migration rolls back cleanly
    instead of leaving the schema half-applied with a stale version number.
    """
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    current_version: int = row[0] if row else 0

    if current_version > len(MIGRATIONS):
        raise RuntimeError(
            f"Database schema version {current_version} is newer than "
            f"this application supports ({len(MIGRATIONS)})"
        )

    for i, migration_fn in enumerate(MIGRATIONS):
        target_version = i + 1
        if target_version <= current_version:
            continue
        logger.info(
            "running_migration",
            migration=migration_fn.__name__,
            from_version=current_version,
            to_version=target_version,
        )
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await migration_fn(conn)
            await conn.execute(f"PRAGMA user_version = {target_version}")
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        current_version = target_version

    return current_version
