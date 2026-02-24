"""Versioned schema migrations using SQLite PRAGMA user_version."""

from __future__ import annotations

import contextlib
import json
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
async def migrate_002_floor_area_sqm(conn: aiosqlite.Connection) -> None:
    """Add floor_area_sqm column and migrate existing sqft data to sqm.

    The old floor_area_sqft column becomes vestigial — no code reads it after
    this migration. We don't DROP it (avoids table rewrite for zero benefit).
    """
    try:
        await conn.execute("ALTER TABLE properties ADD COLUMN floor_area_sqm REAL")
    except aiosqlite.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    await conn.execute(
        """
        UPDATE properties SET floor_area_sqm = ROUND(floor_area_sqft * 0.0929, 1)
        WHERE floor_area_sqft IS NOT NULL AND floor_area_sqm IS NULL
        """
    )


async def migrate_003_source_aliases(conn: aiosqlite.Connection) -> None:
    """Vestigial: superseded by source_listings (migration 004).

    Table retained for schema compatibility. No code reads or writes it
    after Ticket 15. The source_listings table with ``merged_id`` FK
    provides the same functionality with richer data.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_aliases (
            unique_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            anchor_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (anchor_id) REFERENCES properties(unique_id) ON DELETE CASCADE
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_aliases_anchor
        ON source_aliases(anchor_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_aliases_source
        ON source_aliases(source, source_id)
    """)


async def migrate_004_source_listings(conn: aiosqlite.Connection) -> None:
    """Add source_listings table — Layer 1 of the golden record pattern.

    Every scraped property from every platform gets its own row, linked to the
    canonical merged entity in ``properties`` via ``merged_id``.  Backfills
    from existing data so the table is immediately usable.
    """
    # --- DDL ---
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_listings (
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
            last_seen TEXT NOT NULL,
            merged_id TEXT,
            is_backfilled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (merged_id) REFERENCES properties(unique_id) ON DELETE SET NULL
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_listings_source
        ON source_listings(source, source_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_listings_merged
        ON source_listings(merged_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_listings_unmatched
        ON source_listings(unique_id) WHERE merged_id IS NULL
    """)

    # --- Phase 1: Canonical source from properties ---
    # Each golden record's own source/source_id becomes a source_listing.
    await conn.execute("""
        INSERT OR IGNORE INTO source_listings (
            unique_id, source, source_id, url, title, price_pcm,
            bedrooms, address, postcode, latitude, longitude,
            description, image_url, available_from,
            first_seen, last_seen, merged_id, is_backfilled
        )
        SELECT
            unique_id, source, source_id, url, title, price_pcm,
            bedrooms, address, postcode, latitude, longitude,
            description, image_url, available_from,
            first_seen, COALESCE(sources_updated_at, first_seen),
            unique_id, 0
        FROM properties
    """)

    # --- Phase 2: Secondary sources from source_aliases ---
    cursor = await conn.execute("""
        SELECT sa.unique_id, sa.source, sa.source_id, sa.anchor_id,
               p.title, p.price_pcm, p.bedrooms, p.address, p.postcode,
               p.latitude, p.longitude, p.image_url, p.first_seen,
               p.source_urls, p.url
        FROM source_aliases sa
        JOIN properties p ON p.unique_id = sa.anchor_id
    """)
    for row in await cursor.fetchall():
        # Try to extract the source-specific URL from source_urls JSON
        url = row["url"]  # fallback to anchor's URL
        if row["source_urls"]:
            try:
                urls_dict = json.loads(row["source_urls"])
                if row["source"] in urls_dict:
                    url = urls_dict[row["source"]]
            except (json.JSONDecodeError, TypeError):
                pass
        await conn.execute(
            """
            INSERT OR IGNORE INTO source_listings (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, latitude, longitude,
                image_url, first_seen, last_seen, merged_id, is_backfilled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                row["unique_id"],
                row["source"],
                row["source_id"],
                url,
                row["title"],
                row["price_pcm"],
                row["bedrooms"],
                row["address"],
                row["postcode"],
                row["latitude"],
                row["longitude"],
                row["image_url"],
                row["first_seen"],
                row["first_seen"],  # last_seen = first_seen for backfill
                row["anchor_id"],
            ),
        )

    # --- Phase 3: Secondary sources from JSON columns (no alias record) ---
    # Properties with multi-source `sources` JSON but no corresponding
    # source_aliases row (absorbed before source_aliases existed).
    cursor = await conn.execute("""
        SELECT unique_id, source, sources, source_urls,
               title, price_pcm, bedrooms, address, postcode,
               latitude, longitude, image_url, first_seen
        FROM properties
        WHERE sources IS NOT NULL AND json_array_length(sources) > 1
    """)
    for row in await cursor.fetchall():
        sources = json.loads(row["sources"])
        source_urls: dict[str, str] = {}
        if row["source_urls"]:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                source_urls = json.loads(row["source_urls"])
        for src_name in sources:
            if src_name == row["source"]:
                continue  # Phase 1 handled the canonical source
            src_url = source_urls.get(src_name)
            if not src_url:
                continue  # Can't reconstruct without a URL
            # Derive source_id from the URL-based unique_id format
            secondary_uid = f"{src_name}:{src_url}"
            # Check if already inserted by Phase 1 or 2
            await conn.execute(
                """
                INSERT OR IGNORE INTO source_listings (
                    unique_id, source, source_id, url, title, price_pcm,
                    bedrooms, address, postcode, latitude, longitude,
                    image_url, first_seen, last_seen, merged_id, is_backfilled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    secondary_uid,
                    src_name,
                    src_url,  # best-effort source_id
                    src_url,
                    row["title"],
                    row["price_pcm"],
                    row["bedrooms"],
                    row["address"],
                    row["postcode"],
                    row["latitude"],
                    row["longitude"],
                    row["image_url"],
                    row["first_seen"],
                    row["first_seen"],
                    row["unique_id"],
                ),
            )


async def migrate_005_fix_source_listings_linkage(conn: aiosqlite.Connection) -> None:
    """Link orphaned source_listings and deduplicate sources JSON.

    Fixes two issues from the in-run dedup path:
    1. Non-canonical source_listings with ``merged_id=NULL`` despite their
       URL appearing in a golden record's ``source_urls`` JSON.
    2. Duplicate entries in the ``sources`` JSON column (e.g.
       ``['rightmove', 'zoopla', 'zoopla']``).
    """
    # --- Phase 1: Link orphaned source_listings by URL ---
    # For each golden record, find source_listings whose URL appears
    # in the property's source_urls JSON but have no merged_id.
    cursor = await conn.execute(
        "SELECT unique_id, source_urls FROM properties WHERE source_urls IS NOT NULL"
    )
    linked = 0
    for row in await cursor.fetchall():
        try:
            urls_dict = json.loads(row["source_urls"])
        except (json.JSONDecodeError, TypeError):
            continue
        urls = list(urls_dict.values())
        if not urls:
            continue
        placeholders = ",".join("?" * len(urls))
        result = await conn.execute(
            f"UPDATE source_listings SET merged_id = ? "
            f"WHERE url IN ({placeholders}) AND merged_id IS NULL",
            [row["unique_id"], *urls],
        )
        linked += result.rowcount

    if linked:
        logger.info("migration_005_linked_orphaned_source_listings", count=linked)

    # --- Phase 2: Deduplicate sources JSON ---
    cursor = await conn.execute(
        "SELECT unique_id, sources FROM properties WHERE sources IS NOT NULL"
    )
    deduped = 0
    for row in await cursor.fetchall():
        try:
            sources = json.loads(row["sources"])
        except (json.JSONDecodeError, TypeError):
            continue
        unique_sources = list(dict.fromkeys(sources))
        if len(unique_sources) < len(sources):
            await conn.execute(
                "UPDATE properties SET sources = ? WHERE unique_id = ?",
                (json.dumps(unique_sources), row["unique_id"]),
            )
            deduped += 1

    if deduped:
        logger.info("migration_005_deduplicated_sources_json", count=deduped)

    # --- Phase 3: Add partial index for URL-based linking ---
    # _link_source_listings_by_url queries WHERE url IN (...) AND merged_id IS NULL
    # on every property save. This partial index covers that exact pattern.
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_listings_url_unlinked
        ON source_listings(url) WHERE merged_id IS NULL
    """)


_MigrationFn = Callable[[aiosqlite.Connection], Coroutine[Any, Any, None]]

MIGRATIONS: list[_MigrationFn] = [
    migrate_001_initial_schema,
    migrate_002_floor_area_sqm,
    migrate_003_source_aliases,
    migrate_004_source_listings,
    migrate_005_fix_source_listings_linkage,
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
