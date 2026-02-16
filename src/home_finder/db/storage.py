"""SQLite storage for tracked properties."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import aiosqlite

from home_finder.data.area_context import HOSTING_TOLERANCE
from home_finder.db.pipeline_repo import PipelineRepository
from home_finder.db.row_mappers import (
    PropertyDetailItem,
    PropertyListItem,
    build_base_insert,
    build_merged_insert_columns,
    row_to_merged_property,
    row_to_property,
)
from home_finder.db.web_queries import WebQueryService
from home_finder.filters.fit_score import compute_fit_score
from home_finder.logging import get_logger
from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    TrackedProperty,
    TransportMode,
)

if TYPE_CHECKING:
    from home_finder.web.filters import PropertyFilter

# Default lookback window for cross-platform dedup anchors
_DEDUP_LOOKBACK_DAYS: Final = 30

# Re-export TypedDicts for backward compatibility
__all__ = ["PropertyDetailItem", "PropertyListItem", "PropertyStorage"]

logger = get_logger(__name__)


class PropertyStorage:
    """SQLite-based storage for tracking properties."""

    def __init__(self, db_path: str) -> None:
        """Initialize storage with database path.

        Args:
            db_path: Path to SQLite database file, or ":memory:" for in-memory.
        """
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._ensure_directory()
        self._web = WebQueryService(self._get_connection, self.get_property_images)
        self._pipeline = PipelineRepository(
            self._get_connection, self.get_property_images, self.save_quality_analysis
        )

    def _ensure_directory(self) -> None:
        """Ensure the directory for the database exists."""
        if self.db_path != ":memory:":
            path = Path(self.db_path)
            path.parent.mkdir(parents=True, exist_ok=True)

    async def _get_connection(self) -> aiosqlite.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.execute("PRAGMA cache_size=-64000")
            await self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def initialize(self) -> None:
        """Initialize the database schema."""
        conn = await self._get_connection()
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

        # Create indexes for common queries
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

        # Property images table for storing gallery and floorplan images
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS property_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_unique_id TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                image_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id),
                UNIQUE(property_unique_id, url)
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_property_images_property
            ON property_images(property_unique_id)
        """)

        # Quality analyses table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS quality_analyses (
                property_unique_id TEXT PRIMARY KEY,
                analysis_json TEXT NOT NULL,
                overall_rating INTEGER,
                condition_concerns BOOLEAN DEFAULT 0,
                concern_severity TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_quality_rating
            ON quality_analyses(overall_rating)
        """)

        # Migrate: add columns that may not exist in older databases
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
        ]:
            try:
                default_clause = f" DEFAULT {default}" if default is not None else ""
                await conn.execute(
                    f"ALTER TABLE properties ADD COLUMN {column} {col_type}{default_clause}"
                )
            except aiosqlite.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

        # Index for efficiently loading unenriched properties
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_enrichment_status
            ON properties(enrichment_status)
        """)

        # Pipeline runs table for observability
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

        # Migrate: add sources_updated_at for cross-run source tracking
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

        # Migrate: add denormalized quality columns
        for column, col_type in [
            ("epc_rating", "TEXT"),
            ("has_outdoor_space", "BOOLEAN"),
            ("red_flag_count", "INTEGER"),
            ("reanalysis_requested_at", "TEXT"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE quality_analyses ADD COLUMN {column} {col_type}")
            except aiosqlite.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

        # Migrate: fix one_line fields that were stored as JSON objects
        # instead of plain strings (LLM wrapping bug).
        # Suppresses OperationalError because json_extract/json_type throw on
        # rows with malformed analysis_json; the Pydantic unwrap_one_line
        # validator already handles this at read time.
        with contextlib.suppress(aiosqlite.OperationalError):
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

        # Migrate: add fit_score column for SQL-based sorting
        try:
            await conn.execute("ALTER TABLE quality_analyses ADD COLUMN fit_score INTEGER")
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quality_fit_score ON quality_analyses(fit_score)"
        )

        # Backfill fit_score for existing rows
        await self._backfill_fit_scores(conn)

        await conn.commit()

        logger.info("database_initialized", db_path=self.db_path)

    async def _backfill_fit_scores(self, conn: aiosqlite.Connection) -> None:
        """Backfill fit_score for existing quality analyses that lack it."""
        cursor = await conn.execute("""
            SELECT q.property_unique_id, q.analysis_json, p.bedrooms, p.postcode
            FROM quality_analyses q
            JOIN properties p ON p.unique_id = q.property_unique_id
            WHERE q.fit_score IS NULL AND q.analysis_json IS NOT NULL
        """)
        rows = await cursor.fetchall()
        if not rows:
            return

        updates: list[tuple[int | None, str]] = []
        for row in rows:
            try:
                analysis = json.loads(row["analysis_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            postcode = row["postcode"] or ""
            outcode = postcode.split()[0] if postcode else None
            if outcode:
                ht = HOSTING_TOLERANCE.get(outcode)
                if ht:
                    analysis["_area_hosting_tolerance"] = ht.get("rating")
            bedrooms = row["bedrooms"] or 0
            score = compute_fit_score(analysis, bedrooms)
            if score is not None:
                updates.append((score, row["property_unique_id"]))

        if updates:
            await conn.executemany(
                "UPDATE quality_analyses SET fit_score = ? WHERE property_unique_id = ?",
                updates,
            )
            logger.info("fit_score_backfill_complete", updated=len(updates))

    async def save_property(
        self,
        prop: Property,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
    ) -> None:
        """Save or update a property.

        Args:
            prop: Property to save.
            commute_minutes: Commute time in minutes (if calculated).
            transport_mode: Transport mode used for commute calculation.
        """
        conn = await self._get_connection()
        columns, values = build_base_insert(
            prop,
            commute_minutes=commute_minutes,
            transport_mode=transport_mode,
            notification_status=NotificationStatus.PENDING,
        )
        col_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        await conn.execute(
            f"""
            INSERT INTO properties ({col_list})
            VALUES ({placeholders})
            ON CONFLICT(unique_id) DO UPDATE SET
                price_pcm = excluded.price_pcm,
                title = excluded.title,
                description = excluded.description,
                image_url = excluded.image_url,
                commute_minutes = COALESCE(excluded.commute_minutes, commute_minutes),
                transport_mode = COALESCE(excluded.transport_mode, transport_mode)
        """,
            values,
        )
        await conn.commit()

        logger.debug("property_saved", unique_id=prop.unique_id)

    async def is_seen(self, unique_id: str) -> bool:
        """Check if a property has been seen before.

        Args:
            unique_id: Unique property identifier.

        Returns:
            True if property exists in database.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT 1 FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_property(self, unique_id: str) -> TrackedProperty | None:
        """Get a tracked property by unique ID.

        Args:
            unique_id: Unique property identifier.

        Returns:
            TrackedProperty if found, None otherwise.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_tracked_property(row)

    async def get_all_properties(self) -> list[TrackedProperty]:
        """Get all tracked properties.

        Returns:
            List of all tracked properties.
        """
        conn = await self._get_connection()
        cursor = await conn.execute("SELECT * FROM properties ORDER BY first_seen DESC")
        rows = await cursor.fetchall()
        return [self._row_to_tracked_property(row) for row in rows]

    async def get_pending_notifications(self) -> list[TrackedProperty]:
        """Get properties pending notification.

        Returns:
            List of properties with pending notification status.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM properties WHERE notification_status = ? ORDER BY first_seen ASC",
            (NotificationStatus.PENDING.value,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_tracked_property(row) for row in rows]

    async def get_unsent_notifications(self) -> list[TrackedProperty]:
        """Get properties that need notification (pending or previously failed).

        Returns:
            List of properties with pending or failed notification status.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """SELECT * FROM properties
               WHERE notification_status IN (?, ?)
               ORDER BY first_seen ASC""",
            (NotificationStatus.PENDING.value, NotificationStatus.FAILED.value),
        )
        rows = await cursor.fetchall()
        return [self._row_to_tracked_property(row) for row in rows]

    async def mark_notified(self, unique_id: str) -> None:
        """Mark a property as notified.

        Args:
            unique_id: Unique property identifier.
        """
        conn = await self._get_connection()
        await conn.execute(
            """
            UPDATE properties
            SET notification_status = ?, notified_at = ?
            WHERE unique_id = ?
        """,
            (
                NotificationStatus.SENT.value,
                datetime.now(UTC).isoformat(),
                unique_id,
            ),
        )
        await conn.commit()

        logger.debug("property_marked_notified", unique_id=unique_id)

    async def mark_notification_failed(self, unique_id: str) -> None:
        """Mark a property notification as failed.

        Args:
            unique_id: Unique property identifier.
        """
        conn = await self._get_connection()
        await conn.execute(
            """
            UPDATE properties
            SET notification_status = ?
            WHERE unique_id = ?
        """,
            (NotificationStatus.FAILED.value, unique_id),
        )
        await conn.commit()

        logger.debug("property_notification_failed", unique_id=unique_id)

    async def _get_seen_ids(self, unique_ids: list[str]) -> set[str]:
        """Batch-check which unique IDs already exist in the database.

        Args:
            unique_ids: List of unique IDs to check.

        Returns:
            Set of IDs that exist in the database.
        """
        if not unique_ids:
            return set()
        conn = await self._get_connection()
        seen: set[str] = set()
        chunk_size = 500
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor = await conn.execute(
                f"SELECT unique_id FROM properties WHERE unique_id IN ({placeholders})",
                chunk,
            )
            rows = await cursor.fetchall()
            seen.update(row[0] for row in rows)
        return seen

    async def filter_new(self, properties: list[Property]) -> list[Property]:
        """Filter to only properties not yet seen.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties not in the database.
        """
        seen = await self._get_seen_ids([p.unique_id for p in properties])
        return [p for p in properties if p.unique_id not in seen]

    async def filter_new_merged(self, properties: list[MergedProperty]) -> list[MergedProperty]:
        """Filter to only merged properties not yet seen.

        A merged property is considered "seen" if its canonical unique_id exists
        in the database.

        Args:
            properties: List of merged properties to filter.

        Returns:
            List of merged properties not in the database.
        """
        seen = await self._get_seen_ids([m.canonical.unique_id for m in properties])
        return [m for m in properties if m.canonical.unique_id not in seen]

    async def save_unenriched_property(
        self,
        merged: MergedProperty,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
    ) -> None:
        """Save a property that failed enrichment for retry on next run.

        On INSERT: saves with enrichment_status='pending', enrichment_attempts=1,
        notification_status='pending_enrichment'.
        On CONFLICT: just increments enrichment_attempts (preserves other fields).
        """
        conn = await self._get_connection()
        columns, values = build_merged_insert_columns(
            merged,
            commute_minutes=commute_minutes,
            transport_mode=transport_mode,
            notification_status=NotificationStatus.PENDING_ENRICHMENT,
        )
        col_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)

        await conn.execute(
            f"""
            INSERT INTO properties ({col_list}, enrichment_status, enrichment_attempts)
            VALUES ({placeholders}, 'pending', 1)
            ON CONFLICT(unique_id) DO UPDATE SET
                enrichment_attempts = enrichment_attempts + 1
        """,
            values,
        )
        await conn.commit()
        logger.debug("unenriched_property_saved", unique_id=merged.canonical.unique_id)

    async def get_unenriched_properties(self, max_attempts: int = 3) -> list[MergedProperty]:
        """Load properties that failed enrichment for retry.

        Args:
            max_attempts: Only return properties with fewer attempts than this.

        Returns:
            List of MergedProperty objects needing enrichment.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            SELECT * FROM properties
            WHERE enrichment_status = 'pending'
              AND enrichment_attempts < ?
            ORDER BY first_seen ASC
            """,
            (max_attempts,),
        )
        rows = await cursor.fetchall()

        results = [await row_to_merged_property(row, load_images=False) for row in rows]

        logger.info("loaded_unenriched_properties", count=len(results))
        return results

    async def mark_enriched(self, unique_id: str) -> None:
        """Transition a re-enriched property into the normal notification flow.

        Sets enrichment_status='enriched' and notification_status='pending'
        only for rows that still have notification_status='pending_enrichment'.
        No-op for genuinely new properties (already 'pending').
        """
        conn = await self._get_connection()
        await conn.execute(
            """
            UPDATE properties
            SET enrichment_status = 'enriched',
                notification_status = ?
            WHERE unique_id = ?
              AND notification_status = ?
            """,
            (
                NotificationStatus.PENDING.value,
                unique_id,
                NotificationStatus.PENDING_ENRICHMENT.value,
            ),
        )
        await conn.commit()

    async def expire_unenriched(self, max_attempts: int = 3) -> int:
        """Give up on properties that exceeded max enrichment retries.

        Args:
            max_attempts: Threshold for giving up.

        Returns:
            Number of properties expired.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            UPDATE properties
            SET enrichment_status = 'failed'
            WHERE enrichment_status = 'pending'
              AND enrichment_attempts >= ?
            """,
            (max_attempts,),
        )
        await conn.commit()
        count = cursor.rowcount
        if count:
            logger.info("expired_unenriched_properties", count=count)
        return count

    async def delete_property(self, unique_id: str) -> None:
        """Delete a property and all related rows from the database.

        Used to clean up unenriched rows consumed by cross-platform anchor merges.
        """
        conn = await self._get_connection()
        await conn.execute(
            "DELETE FROM property_images WHERE property_unique_id = ?",
            (unique_id,),
        )
        await conn.execute(
            "DELETE FROM quality_analyses WHERE property_unique_id = ?",
            (unique_id,),
        )
        await conn.execute(
            "DELETE FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        await conn.commit()
        logger.debug("property_deleted", unique_id=unique_id)

    async def get_recent_properties_for_dedup(
        self, days: int | None = _DEDUP_LOOKBACK_DAYS
    ) -> list[MergedProperty]:
        """Load recent DB properties as MergedProperty objects for dedup anchoring.

        Used to detect cross-platform duplicates across pipeline runs: new
        properties from platform B can be matched against existing DB records
        from platform A.

        Args:
            days: Lookback window in days (default 30). Pass None to load all.

        Returns:
            List of MergedProperty objects reconstructed from DB rows.
        """
        conn = await self._get_connection()

        if days is not None:
            cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            cursor = await conn.execute(
                """
                SELECT * FROM properties
                WHERE first_seen >= ?
                  AND COALESCE(enrichment_status, 'enriched') != 'pending'
                ORDER BY first_seen DESC
                """,
                (cutoff,),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT * FROM properties
                WHERE COALESCE(enrichment_status, 'enriched') != 'pending'
                ORDER BY first_seen DESC
                """
            )

        rows = await cursor.fetchall()

        results = [
            await row_to_merged_property(
                row, get_property_images=self.get_property_images
            )
            for row in rows
        ]

        logger.debug(
            "loaded_dedup_anchors",
            count=len(results),
            days=days,
        )
        return results

    async def update_merged_sources(
        self,
        existing_unique_id: str,
        merged: MergedProperty,
    ) -> None:
        """Update an existing DB property with additional sources.

        Merges sources, source_urls, descriptions, and price range from a
        newly-matched MergedProperty into the existing record. Does NOT
        touch first_seen, notification_status, quality analysis, or commute data.

        Args:
            existing_unique_id: The unique_id of the existing DB record.
            merged: The MergedProperty containing combined source data.
        """
        conn = await self._get_connection()

        # Read current DB state
        cursor = await conn.execute(
            "SELECT sources, source_urls, descriptions_json, min_price, max_price, price_pcm "
            "FROM properties WHERE unique_id = ?",
            (existing_unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning("update_merged_sources_not_found", unique_id=existing_unique_id)
            return

        # Merge sources
        existing_sources: list[str] = json.loads(row["sources"]) if row["sources"] else []
        existing_source_urls: dict[str, str] = (
            json.loads(row["source_urls"]) if row["source_urls"] else {}
        )
        existing_descriptions: dict[str, str] = (
            json.loads(row["descriptions_json"]) if row["descriptions_json"] else {}
        )

        for src in merged.sources:
            if src.value not in existing_sources:
                existing_sources.append(src.value)
            if src in merged.source_urls:
                existing_source_urls[src.value] = str(merged.source_urls[src])
            if src in merged.descriptions:
                existing_descriptions[src.value] = merged.descriptions[src]

        # Expand price range
        db_min = row["min_price"] if row["min_price"] is not None else row["price_pcm"]
        db_max = row["max_price"] if row["max_price"] is not None else row["price_pcm"]
        new_min = min(db_min, merged.min_price)
        new_max = max(db_max, merged.max_price)

        sources_json = json.dumps(existing_sources)
        source_urls_json = json.dumps(existing_source_urls)
        descriptions_json = json.dumps(existing_descriptions) if existing_descriptions else None

        await conn.execute(
            """
            UPDATE properties SET
                sources = ?,
                source_urls = ?,
                descriptions_json = ?,
                min_price = ?,
                max_price = ?,
                sources_updated_at = ?
            WHERE unique_id = ?
            """,
            (
                sources_json,
                source_urls_json,
                descriptions_json,
                new_min,
                new_max,
                datetime.now(UTC).isoformat(),
                existing_unique_id,
            ),
        )
        await conn.commit()

        # Save any new images from the merged property
        new_images = list(merged.images)
        if merged.floorplan:
            new_images.append(merged.floorplan)
        if new_images:
            await self.save_property_images(existing_unique_id, new_images)

        logger.info(
            "merged_sources_updated",
            unique_id=existing_unique_id,
            sources=existing_sources,
            min_price=new_min,
            max_price=new_max,
        )

    async def save_merged_property(
        self,
        merged: MergedProperty,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
        ward: str | None = None,
    ) -> None:
        """Save a merged property with multi-source data.

        Args:
            merged: Merged property to save.
            commute_minutes: Commute time in minutes (if calculated).
            transport_mode: Transport mode used for commute calculation.
            ward: Official ward name from postcodes.io lookup.
        """
        conn = await self._get_connection()
        columns, values = build_merged_insert_columns(
            merged,
            commute_minutes=commute_minutes,
            transport_mode=transport_mode,
            notification_status=NotificationStatus.PENDING,
            extra={"ward": ward},
        )
        col_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)

        await conn.execute(
            f"""
            INSERT INTO properties ({col_list})
            VALUES ({placeholders})
            ON CONFLICT(unique_id) DO UPDATE SET
                price_pcm = excluded.price_pcm,
                title = excluded.title,
                description = excluded.description,
                image_url = excluded.image_url,
                commute_minutes = COALESCE(excluded.commute_minutes, commute_minutes),
                transport_mode = COALESCE(excluded.transport_mode, transport_mode),
                sources = excluded.sources,
                source_urls = excluded.source_urls,
                min_price = excluded.min_price,
                max_price = excluded.max_price,
                descriptions_json = COALESCE(excluded.descriptions_json, descriptions_json),
                ward = COALESCE(excluded.ward, ward)
        """,
            values,
        )
        await conn.commit()

        logger.debug(
            "merged_property_saved",
            unique_id=merged.canonical.unique_id,
            sources=[s.value for s in merged.sources],
        )

    async def update_wards(self, ward_map: dict[str, str]) -> int:
        """Batch update ward column for multiple properties.

        Args:
            ward_map: Mapping of unique_id -> ward name.

        Returns:
            Number of rows updated.
        """
        if not ward_map:
            return 0
        conn = await self._get_connection()
        cursor = await conn.executemany(
            "UPDATE properties SET ward = ? WHERE unique_id = ?",
            [(ward, uid) for uid, ward in ward_map.items()],
        )
        await conn.commit()
        return cursor.rowcount

    async def get_properties_without_ward(
        self,
    ) -> list[dict[str, Any]]:
        """Get properties that don't have a ward set yet.

        Returns dicts with unique_id, postcode, latitude, longitude.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            SELECT unique_id, postcode, latitude, longitude
            FROM properties
            WHERE ward IS NULL
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def save_property_images(self, unique_id: str, images: list[PropertyImage]) -> None:
        """Save property images to the database.

        Args:
            unique_id: Property unique ID.
            images: List of images to save.
        """
        if not images:
            return

        conn = await self._get_connection()

        rows = [(unique_id, img.source.value, str(img.url), img.image_type) for img in images]
        await conn.executemany(
            """
            INSERT OR IGNORE INTO property_images
            (property_unique_id, source, url, image_type)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()

        logger.debug(
            "property_images_saved",
            unique_id=unique_id,
            image_count=len(images),
        )

    async def get_property_images(self, unique_id: str) -> list[PropertyImage]:
        """Get all images for a property.

        Args:
            unique_id: Property unique ID.

        Returns:
            List of property images.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            SELECT source, url, image_type
            FROM property_images
            WHERE property_unique_id = ?
            ORDER BY image_type, id
        """,
            (unique_id,),
        )
        rows = await cursor.fetchall()

        images = []
        for row in rows:
            images.append(
                PropertyImage(
                    source=PropertySource(row["source"]),
                    url=row["url"],
                    image_type=row["image_type"],
                )
            )
        return images

    async def get_all_known_source_ids(self) -> dict[str, set[str]]:
        """Get all source_ids grouped by property source.

        Returns:
            Dict mapping source name to set of source_ids.
        """
        conn = await self._get_connection()
        cursor = await conn.execute("SELECT source, source_id FROM properties")
        rows = await cursor.fetchall()
        result: dict[str, set[str]] = {}
        for source, source_id in rows:
            result.setdefault(source, set()).add(source_id)
        return result

    async def save_quality_analysis(
        self, unique_id: str, analysis: PropertyQualityAnalysis
    ) -> None:
        """Save a quality analysis result for a property.

        Args:
            unique_id: Property unique ID.
            analysis: PropertyQualityAnalysis instance.
        """
        conn = await self._get_connection()

        # Use model_dump(mode="json") + json.dumps() instead of model_dump_json()
        # to guarantee valid JSON. Pydantic's Rust serializer can produce invalid
        # JSON for edge cases (e.g. NaN floats), while json.dumps() is strict.
        try:
            analysis_json = json.dumps(analysis.model_dump(mode="json"))
        except (ValueError, TypeError) as e:
            logger.error("analysis_json_serialization_failed", unique_id=unique_id, error=str(e))
            return

        # Denormalize key fields for SQL filtering
        overall_rating = analysis.overall_rating
        condition_concerns = analysis.condition_concerns
        concern_severity = analysis.concern_severity

        # Extract denormalized fields from new analysis dimensions
        epc_rating = analysis.listing_extraction.epc_rating if analysis.listing_extraction else None
        has_outdoor_space = None
        if analysis.outdoor_space:
            has_outdoor_space = any(
                [
                    analysis.outdoor_space.has_balcony,
                    analysis.outdoor_space.has_garden,
                    analysis.outdoor_space.has_terrace,
                    analysis.outdoor_space.has_shared_garden,
                ]
            )
        red_flag_count = (
            analysis.listing_red_flags.red_flag_count if analysis.listing_red_flags else None
        )

        # Compute fit_score for SQL-based sorting
        fit_score_val: int | None = None
        prop_cursor = await conn.execute(
            "SELECT bedrooms, postcode FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        prop_row = await prop_cursor.fetchone()
        if prop_row:
            analysis_dict = json.loads(analysis_json)
            postcode = prop_row["postcode"] or ""
            outcode = postcode.split()[0] if postcode else None
            if outcode:
                ht = HOSTING_TOLERANCE.get(outcode)
                if ht:
                    analysis_dict["_area_hosting_tolerance"] = ht.get("rating")
            fit_score_val = compute_fit_score(analysis_dict, prop_row["bedrooms"] or 0)

        await conn.execute(
            """
            INSERT INTO quality_analyses (
                property_unique_id, analysis_json, overall_rating,
                condition_concerns, concern_severity, epc_rating,
                has_outdoor_space, red_flag_count, fit_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(property_unique_id) DO UPDATE SET
                analysis_json = excluded.analysis_json,
                overall_rating = excluded.overall_rating,
                condition_concerns = excluded.condition_concerns,
                concern_severity = excluded.concern_severity,
                epc_rating = excluded.epc_rating,
                has_outdoor_space = excluded.has_outdoor_space,
                red_flag_count = excluded.red_flag_count,
                fit_score = excluded.fit_score
            """,
            (
                unique_id,
                analysis_json,
                overall_rating,
                condition_concerns,
                concern_severity,
                epc_rating,
                has_outdoor_space,
                red_flag_count,
                fit_score_val,
                datetime.now(UTC).isoformat(),
            ),
        )
        await conn.commit()
        logger.debug("quality_analysis_saved", unique_id=unique_id)

    async def get_quality_analysis(self, unique_id: str) -> PropertyQualityAnalysis | None:
        """Get quality analysis for a property.

        Args:
            unique_id: Property unique ID.

        Returns:
            PropertyQualityAnalysis if found, None otherwise.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT analysis_json FROM quality_analyses WHERE property_unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return PropertyQualityAnalysis.model_validate_json(row["analysis_json"])

    # ------------------------------------------------------------------
    # Facade: pipeline run tracking (delegates to PipelineRepository)
    # ------------------------------------------------------------------

    async def create_pipeline_run(self) -> int:
        """Create a new pipeline run record."""
        return await self._pipeline.create_pipeline_run()

    async def update_pipeline_run(self, run_id: int, **counts: int) -> None:
        """Update count columns on a pipeline run."""
        await self._pipeline.update_pipeline_run(run_id, **counts)

    async def complete_pipeline_run(
        self,
        run_id: int,
        status: str,
        *,
        error_message: str | None = None,
    ) -> None:
        """Mark a pipeline run as completed or failed."""
        await self._pipeline.complete_pipeline_run(run_id, status, error_message=error_message)

    async def get_last_pipeline_run(self) -> dict[str, Any] | None:
        """Get the most recent completed pipeline run."""
        return await self._pipeline.get_last_pipeline_run()

    # ------------------------------------------------------------------
    # Facade: quality analysis retry (delegates to PipelineRepository)
    # ------------------------------------------------------------------

    async def save_pre_analysis_properties(
        self,
        merged_list: list[MergedProperty],
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> None:
        """Batch save properties before quality analysis."""
        await self._pipeline.save_pre_analysis_properties(merged_list, commute_lookup)

    async def get_pending_analysis_properties(
        self,
        *,
        exclude_ids: set[str] | None = None,
    ) -> list[MergedProperty]:
        """Load properties needing quality analysis from previous crashed runs."""
        return await self._pipeline.get_pending_analysis_properties(exclude_ids=exclude_ids)

    async def complete_analysis(
        self,
        unique_id: str,
        quality_analysis: PropertyQualityAnalysis | None,
    ) -> None:
        """Complete quality analysis for a property and transition to pending notification."""
        await self._pipeline.complete_analysis(unique_id, quality_analysis)

    async def reset_failed_analyses(self) -> int:
        """Reset properties with fallback analysis for re-analysis."""
        return await self._pipeline.reset_failed_analyses()

    # ------------------------------------------------------------------
    # Facade: re-analysis support (delegates to PipelineRepository)
    # ------------------------------------------------------------------

    async def request_reanalysis(self, unique_ids: list[str]) -> int:
        """Mark specific properties for re-analysis."""
        return await self._pipeline.request_reanalysis(unique_ids)

    async def request_reanalysis_by_filter(
        self,
        *,
        outcodes: list[str] | None = None,
        all_properties: bool = False,
    ) -> int:
        """Bulk-mark properties for re-analysis by outcode or all."""
        return await self._pipeline.request_reanalysis_by_filter(
            outcodes=outcodes, all_properties=all_properties
        )

    async def get_reanalysis_queue(self, *, outcode: str | None = None) -> list[MergedProperty]:
        """Load properties flagged for re-analysis."""
        return await self._pipeline.get_reanalysis_queue(outcode=outcode)

    async def complete_reanalysis(
        self,
        unique_id: str,
        analysis: PropertyQualityAnalysis,
    ) -> None:
        """Save updated quality analysis and clear the re-analysis flag."""
        await self._pipeline.complete_reanalysis(unique_id, analysis)

    # ------------------------------------------------------------------
    # Facade: web dashboard queries (delegates to WebQueryService)
    # ------------------------------------------------------------------

    async def get_filter_count(self, filters: PropertyFilter) -> int:
        """Get count of properties matching filters (no data fetch)."""
        return await self._web.get_filter_count(filters)

    async def get_map_markers(self, filters: PropertyFilter) -> list[dict[str, Any]]:
        """Get lightweight map marker data for all matching properties with coordinates."""
        return await self._web.get_map_markers(filters)

    async def get_properties_paginated(
        self,
        filters: PropertyFilter,
        *,
        sort: str = "newest",
        page: int = 1,
        per_page: int = 24,
    ) -> tuple[list[PropertyListItem], int]:
        """Get paginated properties with optional filters."""
        return await self._web.get_properties_paginated(
            filters, sort=sort, page=page, per_page=per_page
        )

    async def get_property_detail(self, unique_id: str) -> PropertyDetailItem | None:
        """Get full property detail including quality analysis and images."""
        return await self._web.get_property_detail(unique_id)

    async def get_property_count(self) -> int:
        """Get total number of tracked properties."""
        return await self._web.get_property_count()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_tracked_property(self, row: aiosqlite.Row) -> TrackedProperty:
        """Convert a database row to a TrackedProperty.

        Args:
            row: Database row.

        Returns:
            TrackedProperty instance.
        """
        notified_at = datetime.fromisoformat(row["notified_at"]) if row["notified_at"] else None
        return TrackedProperty(
            property=row_to_property(row),
            commute_minutes=row["commute_minutes"],
            transport_mode=(
                TransportMode(row["transport_mode"]) if row["transport_mode"] else None
            ),
            notification_status=NotificationStatus(row["notification_status"]),
            notified_at=notified_at,
        )
