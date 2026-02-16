"""SQLite storage for tracked properties."""

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, TypedDict, cast

import aiosqlite
from pydantic import HttpUrl

from home_finder.data.area_context import HOSTING_TOLERANCE
from home_finder.filters.fit_score import (
    compute_fit_breakdown,
    compute_fit_score,
    compute_lifestyle_icons,
)
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

# Default lookback window for cross-platform dedup anchors
_DEDUP_LOOKBACK_DAYS: Final = 30


class PropertyListItem(TypedDict, total=False):
    """Shape of dicts returned by get_properties_paginated.

    Fields from the SQL join plus parsed JSON columns.
    All fields marked total=False because dict(row) includes the full row.
    """

    unique_id: str
    title: str
    price_pcm: int
    bedrooms: int
    address: str
    postcode: str | None
    image_url: str | None
    latitude: float | None
    longitude: float | None
    commute_minutes: int | None
    transport_mode: str | None
    min_price: int | None
    max_price: int | None
    # Quality analysis (from JOIN)
    quality_rating: int | None
    quality_concerns: bool | None
    quality_severity: str | None
    quality_summary: str
    # Parsed JSON fields
    sources_list: list[str]
    source_urls_dict: dict[str, str]
    descriptions_dict: dict[str, str]
    # Value rating from analysis
    value_rating: str | None
    # Extended quality fields (from analysis_json)
    highlights: list[str] | None
    lowlights: list[str] | None
    property_type: str | None
    one_line: str | None
    epc_rating: str | None


class PropertyDetailItem(PropertyListItem, total=False):
    """Shape of dicts returned by get_property_detail.

    Extends PropertyListItem with images and parsed quality analysis.
    """

    description: str | None
    ward: str | None
    quality_analysis: PropertyQualityAnalysis | None
    gallery_images: list[PropertyImage]
    floorplan_images: list[PropertyImage]


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

        await conn.commit()

        logger.info("database_initialized", db_path=self.db_path)

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
        await conn.execute(
            """
            INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, latitude, longitude,
                description, image_url, available_from, first_seen,
                commute_minutes, transport_mode, notification_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(unique_id) DO UPDATE SET
                price_pcm = excluded.price_pcm,
                title = excluded.title,
                description = excluded.description,
                image_url = excluded.image_url,
                commute_minutes = COALESCE(excluded.commute_minutes, commute_minutes),
                transport_mode = COALESCE(excluded.transport_mode, transport_mode)
        """,
            (
                prop.unique_id,
                prop.source.value,
                prop.source_id,
                str(prop.url),
                prop.title,
                prop.price_pcm,
                prop.bedrooms,
                prop.address,
                prop.postcode,
                prop.latitude,
                prop.longitude,
                prop.description,
                str(prop.image_url) if prop.image_url else None,
                prop.available_from.isoformat() if prop.available_from else None,
                prop.first_seen.isoformat(),
                commute_minutes,
                transport_mode.value if transport_mode else None,
                NotificationStatus.PENDING.value,
            ),
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
        prop = merged.canonical
        conn = await self._get_connection()

        sources_json = json.dumps([s.value for s in merged.sources])
        source_urls_json = json.dumps({s.value: str(url) for s, url in merged.source_urls.items()})
        descriptions_json = (
            json.dumps({s.value: d for s, d in merged.descriptions.items()})
            if merged.descriptions
            else None
        )

        await conn.execute(
            """
            INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, latitude, longitude,
                description, image_url, available_from, first_seen,
                commute_minutes, transport_mode, notification_status,
                sources, source_urls, min_price, max_price, descriptions_json,
                enrichment_status, enrichment_attempts
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1
            )
            ON CONFLICT(unique_id) DO UPDATE SET
                enrichment_attempts = enrichment_attempts + 1
        """,
            (
                prop.unique_id,
                prop.source.value,
                prop.source_id,
                str(prop.url),
                prop.title,
                prop.price_pcm,
                prop.bedrooms,
                prop.address,
                prop.postcode,
                prop.latitude,
                prop.longitude,
                prop.description,
                str(prop.image_url) if prop.image_url else None,
                prop.available_from.isoformat() if prop.available_from else None,
                prop.first_seen.isoformat(),
                commute_minutes,
                transport_mode.value if transport_mode else None,
                NotificationStatus.PENDING_ENRICHMENT.value,
                sources_json,
                source_urls_json,
                merged.min_price,
                merged.max_price,
                descriptions_json,
            ),
        )
        await conn.commit()
        logger.debug("unenriched_property_saved", unique_id=prop.unique_id)

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

        results: list[MergedProperty] = []
        for row in rows:
            prop = self._row_to_tracked_property(row).property

            sources_list: list[PropertySource] = []
            source_urls: dict[PropertySource, HttpUrl] = {}
            descriptions: dict[PropertySource, str] = {}

            if row["sources"]:
                for s in json.loads(row["sources"]):
                    sources_list.append(PropertySource(s))
            else:
                sources_list.append(prop.source)

            if row["source_urls"]:
                for s, url in json.loads(row["source_urls"]).items():
                    source_urls[PropertySource(s)] = HttpUrl(url)
            else:
                source_urls[prop.source] = prop.url

            if row["descriptions_json"]:
                for s, desc in json.loads(row["descriptions_json"]).items():
                    descriptions[PropertySource(s)] = desc

            min_price = row["min_price"] if row["min_price"] is not None else prop.price_pcm
            max_price = row["max_price"] if row["max_price"] is not None else prop.price_pcm

            results.append(
                MergedProperty(
                    canonical=prop,
                    sources=tuple(sources_list),
                    source_urls=source_urls,
                    images=(),
                    floorplan=None,
                    min_price=min_price,
                    max_price=max_price,
                    descriptions=descriptions,
                )
            )

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
        self, days: int = _DEDUP_LOOKBACK_DAYS
    ) -> list[MergedProperty]:
        """Load recent DB properties as MergedProperty objects for dedup anchoring.

        Used to detect cross-platform duplicates across pipeline runs: new
        properties from platform B can be matched against existing DB records
        from platform A.

        Args:
            days: Lookback window in days (default 30).

        Returns:
            List of MergedProperty objects reconstructed from DB rows.
        """
        conn = await self._get_connection()
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
        rows = await cursor.fetchall()

        results: list[MergedProperty] = []
        for row in rows:
            prop = self._row_to_tracked_property(row).property

            # Parse multi-source fields from DB
            sources_list: list[PropertySource] = []
            source_urls: dict[PropertySource, HttpUrl] = {}
            descriptions: dict[PropertySource, str] = {}

            if row["sources"]:
                for s in json.loads(row["sources"]):
                    sources_list.append(PropertySource(s))
            else:
                sources_list.append(prop.source)

            if row["source_urls"]:
                for s, url in json.loads(row["source_urls"]).items():
                    source_urls[PropertySource(s)] = HttpUrl(url)
            else:
                source_urls[prop.source] = prop.url

            if row["descriptions_json"]:
                for s, desc in json.loads(row["descriptions_json"]).items():
                    descriptions[PropertySource(s)] = desc

            # Load images for this property
            images = await self.get_property_images(prop.unique_id)
            gallery = tuple(img for img in images if img.image_type == "gallery")
            floorplan_img = next((img for img in images if img.image_type == "floorplan"), None)

            min_price = row["min_price"] if row["min_price"] is not None else prop.price_pcm
            max_price = row["max_price"] if row["max_price"] is not None else prop.price_pcm

            results.append(
                MergedProperty(
                    canonical=prop,
                    sources=tuple(sources_list),
                    source_urls=source_urls,
                    images=gallery,
                    floorplan=floorplan_img,
                    min_price=min_price,
                    max_price=max_price,
                    descriptions=descriptions,
                )
            )

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
        prop = merged.canonical
        conn = await self._get_connection()

        # Serialize sources, source_urls, and descriptions as JSON
        sources_json = json.dumps([s.value for s in merged.sources])
        source_urls_json = json.dumps({s.value: str(url) for s, url in merged.source_urls.items()})
        descriptions_json = (
            json.dumps({s.value: d for s, d in merged.descriptions.items()})
            if merged.descriptions
            else None
        )

        await conn.execute(
            """
            INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, latitude, longitude,
                description, image_url, available_from, first_seen,
                commute_minutes, transport_mode, notification_status,
                sources, source_urls, min_price, max_price, descriptions_json,
                ward
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            (
                prop.unique_id,
                prop.source.value,
                prop.source_id,
                str(prop.url),
                prop.title,
                prop.price_pcm,
                prop.bedrooms,
                prop.address,
                prop.postcode,
                prop.latitude,
                prop.longitude,
                prop.description,
                str(prop.image_url) if prop.image_url else None,
                prop.available_from.isoformat() if prop.available_from else None,
                prop.first_seen.isoformat(),
                commute_minutes,
                transport_mode.value if transport_mode else None,
                NotificationStatus.PENDING.value,
                sources_json,
                source_urls_json,
                merged.min_price,
                merged.max_price,
                descriptions_json,
                ward,
            ),
        )
        await conn.commit()

        logger.debug(
            "merged_property_saved",
            unique_id=prop.unique_id,
            sources=[s.value for s in merged.sources],
        )

    async def update_wards(self, ward_map: dict[str, str]) -> int:
        """Batch update ward column for multiple properties.

        Args:
            ward_map: Mapping of unique_id → ward name.

        Returns:
            Number of rows updated.
        """
        if not ward_map:
            return 0
        conn = await self._get_connection()
        updated = 0
        for unique_id, ward in ward_map.items():
            cursor = await conn.execute(
                "UPDATE properties SET ward = ? WHERE unique_id = ?",
                (ward, unique_id),
            )
            updated += cursor.rowcount
        await conn.commit()
        return updated

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

        await conn.execute(
            """
            INSERT INTO quality_analyses (
                property_unique_id, analysis_json, overall_rating,
                condition_concerns, concern_severity, epc_rating,
                has_outdoor_space, red_flag_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(property_unique_id) DO UPDATE SET
                analysis_json = excluded.analysis_json,
                overall_rating = excluded.overall_rating,
                condition_concerns = excluded.condition_concerns,
                concern_severity = excluded.concern_severity,
                epc_rating = excluded.epc_rating,
                has_outdoor_space = excluded.has_outdoor_space,
                red_flag_count = excluded.red_flag_count
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
    # Pipeline run tracking
    # ------------------------------------------------------------------

    async def create_pipeline_run(self) -> int:
        """Create a new pipeline run record.

        Returns:
            The ID of the new run.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            "INSERT INTO pipeline_runs (started_at, status) VALUES (?, 'running')",
            (datetime.now(UTC).isoformat(),),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def update_pipeline_run(self, run_id: int, **counts: int) -> None:
        """Update count columns on a pipeline run.

        Args:
            run_id: The pipeline run ID.
            **counts: Column name/value pairs to update (e.g. scraped_count=42).
        """
        if not counts:
            return
        conn = await self._get_connection()
        set_clauses = ", ".join(f"{k} = ?" for k in counts)
        values = list(counts.values())
        values.append(run_id)
        await conn.execute(
            f"UPDATE pipeline_runs SET {set_clauses} WHERE id = ?",
            values,
        )
        await conn.commit()

    async def complete_pipeline_run(
        self,
        run_id: int,
        status: str,
        *,
        error_message: str | None = None,
    ) -> None:
        """Mark a pipeline run as completed or failed.

        Args:
            run_id: The pipeline run ID.
            status: Final status ('completed' or 'failed').
            error_message: Error message if status is 'failed'.
        """
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()
        # Calculate duration from started_at
        cursor = await conn.execute("SELECT started_at FROM pipeline_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        duration = None
        if row:
            started = datetime.fromisoformat(row["started_at"])
            duration = (datetime.fromisoformat(now) - started).total_seconds()

        await conn.execute(
            """
            UPDATE pipeline_runs
            SET completed_at = ?, status = ?, error_message = ?, duration_seconds = ?
            WHERE id = ?
            """,
            (now, status, error_message, duration, run_id),
        )
        await conn.commit()

    async def get_last_pipeline_run(self) -> dict[str, Any] | None:
        """Get the most recent completed pipeline run.

        Returns:
            Dict with run data, or None if no runs exist.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            SELECT * FROM pipeline_runs
            WHERE status IN ('completed', 'failed')
            ORDER BY id DESC LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Quality analysis retry (save-before-analyze pattern)
    # ------------------------------------------------------------------

    async def save_pre_analysis_properties(
        self,
        merged_list: list[MergedProperty],
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> None:
        """Batch save properties before quality analysis.

        Saves with notification_status='pending_analysis' and
        enrichment_status='enriched'. If the process crashes during
        quality analysis, these can be recovered on next run.

        Args:
            merged_list: Properties to save.
            commute_lookup: Commute data keyed by unique_id.
        """
        conn = await self._get_connection()
        for merged in merged_list:
            prop = merged.canonical
            commute_info = commute_lookup.get(prop.unique_id)
            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None

            sources_json = json.dumps([s.value for s in merged.sources])
            source_urls_json = json.dumps(
                {s.value: str(url) for s, url in merged.source_urls.items()}
            )
            descriptions_json = (
                json.dumps({s.value: d for s, d in merged.descriptions.items()})
                if merged.descriptions
                else None
            )

            await conn.execute(
                """
                INSERT INTO properties (
                    unique_id, source, source_id, url, title, price_pcm,
                    bedrooms, address, postcode, latitude, longitude,
                    description, image_url, available_from, first_seen,
                    commute_minutes, transport_mode, notification_status,
                    sources, source_urls, min_price, max_price, descriptions_json,
                    enrichment_status
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'enriched'
                )
                ON CONFLICT(unique_id) DO UPDATE SET
                    notification_status = excluded.notification_status
                """,
                (
                    prop.unique_id,
                    prop.source.value,
                    prop.source_id,
                    str(prop.url),
                    prop.title,
                    prop.price_pcm,
                    prop.bedrooms,
                    prop.address,
                    prop.postcode,
                    prop.latitude,
                    prop.longitude,
                    prop.description,
                    str(prop.image_url) if prop.image_url else None,
                    prop.available_from.isoformat() if prop.available_from else None,
                    prop.first_seen.isoformat(),
                    commute_minutes,
                    transport_mode.value if transport_mode else None,
                    NotificationStatus.PENDING_ANALYSIS.value,
                    sources_json,
                    source_urls_json,
                    merged.min_price,
                    merged.max_price,
                    descriptions_json,
                ),
            )

            # Save images
            images = list(merged.images)
            if merged.floorplan:
                images.append(merged.floorplan)
            if images:
                rows = [
                    (prop.unique_id, img.source.value, str(img.url), img.image_type)
                    for img in images
                ]
                await conn.executemany(
                    """
                    INSERT OR IGNORE INTO property_images
                    (property_unique_id, source, url, image_type)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )

        await conn.commit()
        logger.info("pre_analysis_properties_saved", count=len(merged_list))

    async def get_pending_analysis_properties(
        self,
        *,
        exclude_ids: set[str] | None = None,
    ) -> list[MergedProperty]:
        """Load properties needing quality analysis from previous crashed runs.

        Args:
            exclude_ids: Property IDs to exclude (e.g. current batch just saved).

        Returns:
            List of MergedProperty objects with notification_status='pending_analysis'.
        """
        conn = await self._get_connection()
        query = """
            SELECT * FROM properties
            WHERE notification_status = ?
            ORDER BY first_seen ASC
        """
        params: list[Any] = [NotificationStatus.PENDING_ANALYSIS.value]

        if exclude_ids:
            placeholders = ",".join("?" * len(exclude_ids))
            query = f"""
                SELECT * FROM properties
                WHERE notification_status = ?
                  AND unique_id NOT IN ({placeholders})
                ORDER BY first_seen ASC
            """
            params.extend(exclude_ids)

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()

        results: list[MergedProperty] = []
        for row in rows:
            prop = self._row_to_tracked_property(row).property

            sources_list: list[PropertySource] = []
            source_urls: dict[PropertySource, HttpUrl] = {}
            descriptions: dict[PropertySource, str] = {}

            if row["sources"]:
                for s in json.loads(row["sources"]):
                    sources_list.append(PropertySource(s))
            else:
                sources_list.append(prop.source)

            if row["source_urls"]:
                for s, url in json.loads(row["source_urls"]).items():
                    source_urls[PropertySource(s)] = HttpUrl(url)
            else:
                source_urls[prop.source] = prop.url

            if row["descriptions_json"]:
                for s, desc in json.loads(row["descriptions_json"]).items():
                    descriptions[PropertySource(s)] = desc

            # Load images
            images = await self.get_property_images(prop.unique_id)
            gallery = tuple(img for img in images if img.image_type == "gallery")
            floorplan_img = next((img for img in images if img.image_type == "floorplan"), None)

            min_price = row["min_price"] if row["min_price"] is not None else prop.price_pcm
            max_price = row["max_price"] if row["max_price"] is not None else prop.price_pcm

            results.append(
                MergedProperty(
                    canonical=prop,
                    sources=tuple(sources_list),
                    source_urls=source_urls,
                    images=gallery,
                    floorplan=floorplan_img,
                    min_price=min_price,
                    max_price=max_price,
                    descriptions=descriptions,
                )
            )

        if results:
            logger.info(
                "loaded_pending_analysis_retries_from_db",
                count=len(results),
            )
        return results

    async def complete_analysis(
        self,
        unique_id: str,
        quality_analysis: PropertyQualityAnalysis | None,
    ) -> None:
        """Complete quality analysis for a property and transition to pending notification.

        Saves quality data (if any) and sets notification_status to 'pending'.
        Only transitions properties that are currently 'pending_analysis'.

        Args:
            unique_id: Property unique ID.
            quality_analysis: Analysis result, or None if analysis was skipped.
        """
        if quality_analysis:
            await self.save_quality_analysis(unique_id, quality_analysis)

        conn = await self._get_connection()
        await conn.execute(
            """
            UPDATE properties
            SET notification_status = ?
            WHERE unique_id = ?
              AND notification_status = ?
            """,
            (
                NotificationStatus.PENDING.value,
                unique_id,
                NotificationStatus.PENDING_ANALYSIS.value,
            ),
        )
        await conn.commit()
        logger.debug("analysis_completed", unique_id=unique_id)

    async def reset_failed_analyses(self) -> int:
        """Reset properties with fallback analysis for re-analysis.

        Finds properties where quality analysis ran but produced only the
        minimal fallback (overall_rating IS NULL), indicating the API failed.
        Deletes the fallback quality data and transitions them back to
        'pending_analysis' so the next pipeline run re-analyzes them.

        Returns:
            Number of properties reset.
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT p.unique_id FROM properties p
            JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE q.overall_rating IS NULL
              AND p.notification_status != ?
            """,
            (NotificationStatus.PENDING_ANALYSIS.value,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        ids = [row["unique_id"] for row in rows]
        placeholders = ",".join("?" * len(ids))

        await conn.execute(
            f"DELETE FROM quality_analyses WHERE property_unique_id IN ({placeholders})",
            ids,
        )
        await conn.execute(
            f"""
            UPDATE properties SET notification_status = ?
            WHERE unique_id IN ({placeholders})
            """,
            [NotificationStatus.PENDING_ANALYSIS.value, *ids],
        )
        await conn.commit()

        logger.info("reset_failed_analyses", count=len(ids), unique_ids=ids)
        return len(ids)

    # ------------------------------------------------------------------
    # Re-analysis support
    # ------------------------------------------------------------------

    async def request_reanalysis(self, unique_ids: list[str]) -> int:
        """Mark specific properties for re-analysis.

        Sets reanalysis_requested_at on matching quality_analyses rows.
        Idempotent — re-requesting just updates the timestamp.

        Args:
            unique_ids: Property unique IDs to flag for re-analysis.

        Returns:
            Number of rows updated.
        """
        if not unique_ids:
            return 0
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()
        total = 0
        chunk_size = 500
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor = await conn.execute(
                f"""
                UPDATE quality_analyses
                SET reanalysis_requested_at = ?
                WHERE property_unique_id IN ({placeholders})
                """,
                [now, *chunk],
            )
            total += cursor.rowcount
        await conn.commit()
        logger.info("reanalysis_requested", count=total, ids=unique_ids)
        return total

    async def request_reanalysis_by_filter(
        self,
        *,
        outcodes: list[str] | None = None,
        all_properties: bool = False,
    ) -> int:
        """Bulk-mark properties for re-analysis by outcode or all.

        Only targets properties that already have a quality analysis.

        Args:
            outcodes: Outcode prefixes to match (e.g. ["E2", "E8"]).
            all_properties: If True, mark all analyzed properties.

        Returns:
            Number of rows updated.
        """
        if not outcodes and not all_properties:
            return 0
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()

        if all_properties:
            cursor = await conn.execute(
                """
                UPDATE quality_analyses
                SET reanalysis_requested_at = ?
                WHERE property_unique_id IN (
                    SELECT unique_id FROM properties
                )
                """,
                (now,),
            )
        else:
            # Build OR conditions for outcode prefix matching
            conditions = []
            params: list[str] = [now]
            for outcode in outcodes or []:
                conditions.append("UPPER(p.postcode) LIKE ?")
                params.append(f"{outcode.upper()}%")
            or_clause = " OR ".join(conditions)
            cursor = await conn.execute(
                f"""
                UPDATE quality_analyses
                SET reanalysis_requested_at = ?
                WHERE property_unique_id IN (
                    SELECT p.unique_id FROM properties p
                    WHERE {or_clause}
                )
                """,
                params,
            )

        await conn.commit()
        count = cursor.rowcount
        logger.info(
            "reanalysis_requested_by_filter",
            count=count,
            outcodes=outcodes,
            all_properties=all_properties,
        )
        return count

    async def get_reanalysis_queue(self, *, outcode: str | None = None) -> list[MergedProperty]:
        """Load properties flagged for re-analysis.

        Reconstructs MergedProperty objects from DB rows, same pattern
        as get_pending_analysis_properties().

        Args:
            outcode: Optional outcode filter (prefix match).

        Returns:
            List of MergedProperty objects needing re-analysis.
        """
        conn = await self._get_connection()

        if outcode:
            query = """
                SELECT p.* FROM properties p
                JOIN quality_analyses q ON p.unique_id = q.property_unique_id
                WHERE q.reanalysis_requested_at IS NOT NULL
                  AND UPPER(p.postcode) LIKE ?
                ORDER BY p.first_seen ASC
            """
            params: list[str] = [f"{outcode.upper()}%"]
            cursor = await conn.execute(query, params)
        else:
            cursor = await conn.execute("""
                SELECT p.* FROM properties p
                JOIN quality_analyses q ON p.unique_id = q.property_unique_id
                WHERE q.reanalysis_requested_at IS NOT NULL
                ORDER BY p.first_seen ASC
            """)

        rows = await cursor.fetchall()

        results: list[MergedProperty] = []
        for row in rows:
            prop = self._row_to_tracked_property(row).property

            sources_list: list[PropertySource] = []
            source_urls: dict[PropertySource, HttpUrl] = {}
            descriptions: dict[PropertySource, str] = {}

            if row["sources"]:
                for s in json.loads(row["sources"]):
                    sources_list.append(PropertySource(s))
            else:
                sources_list.append(prop.source)

            if row["source_urls"]:
                for s, url in json.loads(row["source_urls"]).items():
                    source_urls[PropertySource(s)] = HttpUrl(url)
            else:
                source_urls[prop.source] = prop.url

            if row["descriptions_json"]:
                for s, desc in json.loads(row["descriptions_json"]).items():
                    descriptions[PropertySource(s)] = desc

            # Load images
            images = await self.get_property_images(prop.unique_id)
            gallery = tuple(img for img in images if img.image_type == "gallery")
            floorplan_img = next((img for img in images if img.image_type == "floorplan"), None)

            min_price = row["min_price"] if row["min_price"] is not None else prop.price_pcm
            max_price = row["max_price"] if row["max_price"] is not None else prop.price_pcm

            results.append(
                MergedProperty(
                    canonical=prop,
                    sources=tuple(sources_list),
                    source_urls=source_urls,
                    images=gallery,
                    floorplan=floorplan_img,
                    min_price=min_price,
                    max_price=max_price,
                    descriptions=descriptions,
                )
            )

        logger.info("loaded_reanalysis_queue", count=len(results))
        return results

    async def complete_reanalysis(
        self,
        unique_id: str,
        analysis: PropertyQualityAnalysis,
    ) -> None:
        """Save updated quality analysis and clear the re-analysis flag.

        Does NOT touch notification_status — property stays 'sent'.

        Args:
            unique_id: Property unique ID.
            analysis: New quality analysis result.
        """
        await self.save_quality_analysis(unique_id, analysis)

        conn = await self._get_connection()
        await conn.execute(
            """
            UPDATE quality_analyses
            SET reanalysis_requested_at = NULL
            WHERE property_unique_id = ?
            """,
            (unique_id,),
        )
        await conn.commit()
        logger.debug("reanalysis_completed", unique_id=unique_id)

    @staticmethod
    def _build_filter_clauses(
        *,
        min_price: int | None = None,
        max_price: int | None = None,
        bedrooms: int | None = None,
        min_rating: int | None = None,
        area: str | None = None,
        property_type: str | None = None,
        outdoor_space: str | None = None,
        natural_light: str | None = None,
        pets: str | None = None,
        value_rating: str | None = None,
        hob_type: str | None = None,
        floor_level: str | None = None,
        building_construction: str | None = None,
        office_separation: str | None = None,
        hosting_layout: str | None = None,
        hosting_noise_risk: str | None = None,
        broadband_type: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[str, list[Any]]:
        """Build WHERE clause and params for property filtering.

        Returns:
            Tuple of (where_sql, params).
        """
        where_clauses: list[str] = [
            "COALESCE(p.enrichment_status, 'enriched') != 'pending'",
            "p.notification_status != 'pending_analysis'",
            # Hide properties with fallback analysis (API failed, no real quality data).
            # A fallback has a quality_analyses row but NULL overall_rating.
            # Properties with no analysis row at all (q.property_unique_id IS NULL) are fine.
            "(q.overall_rating IS NOT NULL OR q.property_unique_id IS NULL)",
            # Hide properties with no images — not worth viewing without photos
            """(p.image_url IS NOT NULL OR EXISTS (
                SELECT 1 FROM property_images pi
                WHERE pi.property_unique_id = p.unique_id
                AND pi.image_type = 'gallery'))""",
        ]
        params: list[Any] = []

        if min_price is not None:
            where_clauses.append("p.price_pcm >= ?")
            params.append(min_price)
        if max_price is not None:
            where_clauses.append("p.price_pcm <= ?")
            params.append(max_price)
        if bedrooms is not None:
            where_clauses.append("p.bedrooms = ?")
            params.append(bedrooms)
        if min_rating is not None:
            where_clauses.append("q.overall_rating >= ?")
            params.append(min_rating)
        if area:
            where_clauses.append("UPPER(p.postcode) LIKE ?")
            params.append(f"{area.upper()}%")
        if property_type:
            where_clauses.append(
                "json_extract(q.analysis_json, '$.listing_extraction.property_type') = ?"
            )
            params.append(property_type)
        if outdoor_space == "yes":
            where_clauses.append("q.has_outdoor_space = 1")
        elif outdoor_space == "no":
            where_clauses.append("(q.has_outdoor_space = 0 OR q.has_outdoor_space IS NULL)")
        if natural_light:
            where_clauses.append("json_extract(q.analysis_json, '$.light_space.natural_light') = ?")
            params.append(natural_light)
        if pets == "yes":
            where_clauses.append(
                "json_extract(q.analysis_json, '$.listing_extraction.pets_allowed') = 'yes'"
            )
        if value_rating:
            where_clauses.append(
                "(json_extract(q.analysis_json, '$.value.quality_adjusted_rating') = ?"
                " OR json_extract(q.analysis_json, '$.value.rating') = ?)"
            )
            params.extend([value_rating, value_rating])
        if hob_type:
            where_clauses.append("json_extract(q.analysis_json, '$.kitchen.hob_type') = ?")
            params.append(hob_type)
        if floor_level:
            where_clauses.append("json_extract(q.analysis_json, '$.light_space.floor_level') = ?")
            params.append(floor_level)
        if building_construction:
            where_clauses.append(
                "json_extract(q.analysis_json, '$.flooring_noise.building_construction') = ?"
            )
            params.append(building_construction)
        if office_separation:
            where_clauses.append("json_extract(q.analysis_json, '$.bedroom.office_separation') = ?")
            params.append(office_separation)
        if hosting_layout:
            where_clauses.append("json_extract(q.analysis_json, '$.space.hosting_layout') = ?")
            params.append(hosting_layout)
        if hosting_noise_risk:
            where_clauses.append(
                "json_extract(q.analysis_json, '$.flooring_noise.hosting_noise_risk') = ?"
            )
            params.append(hosting_noise_risk)
        if broadband_type:
            where_clauses.append(
                "json_extract(q.analysis_json, '$.listing_extraction.broadband_type') = ?"
            )
            params.append(broadband_type)
        if tags:
            for t in tags:
                where_clauses.append(
                    "(json_extract(q.analysis_json, '$.highlights') LIKE ?"
                    " OR json_extract(q.analysis_json, '$.lowlights') LIKE ?)"
                )
                escaped = t.replace("%", "\\%").replace("_", "\\_")
                params.extend([f"%{escaped}%", f"%{escaped}%"])

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        return where_sql, params

    async def get_filter_count(
        self,
        *,
        min_price: int | None = None,
        max_price: int | None = None,
        bedrooms: int | None = None,
        min_rating: int | None = None,
        area: str | None = None,
        property_type: str | None = None,
        outdoor_space: str | None = None,
        natural_light: str | None = None,
        pets: str | None = None,
        value_rating: str | None = None,
        hob_type: str | None = None,
        floor_level: str | None = None,
        building_construction: str | None = None,
        office_separation: str | None = None,
        hosting_layout: str | None = None,
        hosting_noise_risk: str | None = None,
        broadband_type: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """Get count of properties matching filters (no data fetch).

        Returns:
            Total count of matching properties.
        """
        conn = await self._get_connection()
        where_sql, params = self._build_filter_clauses(
            min_price=min_price,
            max_price=max_price,
            bedrooms=bedrooms,
            min_rating=min_rating,
            area=area,
            property_type=property_type,
            outdoor_space=outdoor_space,
            natural_light=natural_light,
            pets=pets,
            value_rating=value_rating,
            hob_type=hob_type,
            floor_level=floor_level,
            building_construction=building_construction,
            office_separation=office_separation,
            hosting_layout=hosting_layout,
            hosting_noise_risk=hosting_noise_risk,
            broadband_type=broadband_type,
            tags=tags,
        )
        cursor = await conn.execute(
            f"""
            SELECT COUNT(*) FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
            """,
            params,
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_map_markers(
        self,
        *,
        min_price: int | None = None,
        max_price: int | None = None,
        bedrooms: int | None = None,
        min_rating: int | None = None,
        area: str | None = None,
        property_type: str | None = None,
        outdoor_space: str | None = None,
        natural_light: str | None = None,
        pets: str | None = None,
        value_rating: str | None = None,
        hob_type: str | None = None,
        floor_level: str | None = None,
        building_construction: str | None = None,
        office_separation: str | None = None,
        hosting_layout: str | None = None,
        hosting_noise_risk: str | None = None,
        broadband_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get lightweight map marker data for all matching properties with coordinates.

        Same filters as get_properties_paginated but no pagination and only
        map-relevant columns. Returns only properties that have lat/lon.

        Returns:
            List of dicts with map marker fields.
        """
        conn = await self._get_connection()
        where_sql, params = self._build_filter_clauses(
            min_price=min_price,
            max_price=max_price,
            bedrooms=bedrooms,
            min_rating=min_rating,
            area=area,
            property_type=property_type,
            outdoor_space=outdoor_space,
            natural_light=natural_light,
            pets=pets,
            value_rating=value_rating,
            hob_type=hob_type,
            floor_level=floor_level,
            building_construction=building_construction,
            office_separation=office_separation,
            hosting_layout=hosting_layout,
            hosting_noise_risk=hosting_noise_risk,
            broadband_type=broadband_type,
            tags=tags,
        )
        cursor = await conn.execute(
            f"""
            SELECT p.unique_id, p.latitude, p.longitude, p.price_pcm,
                   p.bedrooms, p.title, p.postcode,
                   p.commute_minutes, p.image_url,
                   q.overall_rating as quality_rating,
                   json_extract(q.analysis_json, '$.value.quality_adjusted_rating') as value_rating,
                   json_extract(q.analysis_json, '$.one_line') as one_line
            FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
              AND p.latitude IS NOT NULL AND p.longitude IS NOT NULL
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["unique_id"],
                "lat": row["latitude"],
                "lon": row["longitude"],
                "price": row["price_pcm"],
                "bedrooms": row["bedrooms"],
                "rating": row["quality_rating"],
                "title": row["title"],
                "url": f"/property/{row['unique_id']}",
                "image_url": row["image_url"],
                "postcode": row["postcode"],
                "commute_minutes": row["commute_minutes"],
                "value_rating": row["value_rating"],
                "one_line": row["one_line"],
            }
            for row in rows
        ]

    async def get_properties_paginated(
        self,
        *,
        sort: str = "newest",
        min_price: int | None = None,
        max_price: int | None = None,
        bedrooms: int | None = None,
        min_rating: int | None = None,
        area: str | None = None,
        page: int = 1,
        per_page: int = 24,
        property_type: str | None = None,
        outdoor_space: str | None = None,
        natural_light: str | None = None,
        pets: str | None = None,
        value_rating: str | None = None,
        hob_type: str | None = None,
        floor_level: str | None = None,
        building_construction: str | None = None,
        office_separation: str | None = None,
        hosting_layout: str | None = None,
        hosting_noise_risk: str | None = None,
        broadband_type: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[list[PropertyListItem], int]:
        """Get paginated properties with optional filters.

        Returns:
            Tuple of (property dicts, total count).
        """
        conn = await self._get_connection()

        where_sql, params = self._build_filter_clauses(
            min_price=min_price,
            max_price=max_price,
            bedrooms=bedrooms,
            min_rating=min_rating,
            area=area,
            property_type=property_type,
            outdoor_space=outdoor_space,
            natural_light=natural_light,
            pets=pets,
            value_rating=value_rating,
            hob_type=hob_type,
            floor_level=floor_level,
            building_construction=building_construction,
            office_separation=office_separation,
            hosting_layout=hosting_layout,
            hosting_noise_risk=hosting_noise_risk,
            broadband_type=broadband_type,
            tags=tags,
        )

        order_map = {
            "newest": "p.first_seen DESC",
            "price_asc": "p.price_pcm ASC",
            "price_desc": "p.price_pcm DESC",
            "rating_desc": "COALESCE(q.overall_rating, 0) DESC, p.first_seen DESC",
        }
        is_fit_sort = sort == "fit_desc"
        order_sql = order_map.get(sort, "p.first_seen DESC")

        # Count total
        count_cursor = await conn.execute(
            f"""
            SELECT COUNT(*) FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
            """,
            params,
        )
        count_row = await count_cursor.fetchone()
        total = count_row[0] if count_row else 0

        # Subquery: first non-EPC gallery image as fallback thumbnail
        gallery_subquery = """
            (SELECT pi.url FROM property_images pi
             WHERE pi.property_unique_id = p.unique_id
             AND pi.image_type = 'gallery'
             AND LOWER(pi.url) NOT LIKE '%epc%'
             AND LOWER(pi.url) NOT LIKE '%energy-performance%'
             AND LOWER(pi.url) NOT LIKE '%energy_performance%'
             ORDER BY pi.id LIMIT 1) as first_gallery_url
        """

        if is_fit_sort:
            # Fit sort: fetch all matching rows, sort in Python by computed score
            cursor = await conn.execute(
                f"""
                SELECT p.*, q.overall_rating as quality_rating,
                       q.condition_concerns as quality_concerns,
                       q.concern_severity as quality_severity,
                       q.analysis_json,
                       {gallery_subquery}
                FROM properties p
                LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
                WHERE {where_sql}
                """,
                params,
            )
        else:
            # Standard sort: paginate in SQL
            offset = (page - 1) * per_page
            cursor = await conn.execute(
                f"""
                SELECT p.*, q.overall_rating as quality_rating,
                       q.condition_concerns as quality_concerns,
                       q.concern_severity as quality_severity,
                       q.analysis_json,
                       {gallery_subquery}
                FROM properties p
                LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                [*params, per_page, offset],
            )
        rows = await cursor.fetchall()

        properties: list[PropertyListItem] = []
        for row in rows:
            prop_dict = dict(row)
            self._parse_json_fields(prop_dict)
            # Prefer enriched gallery image (EPC URLs already filtered by subquery)
            # over scraper thumbnail which may be low-res or expired
            if prop_dict.get("first_gallery_url"):
                prop_dict["image_url"] = prop_dict["first_gallery_url"]
            # Extract quality fields from analysis_json
            if prop_dict.get("analysis_json"):
                try:
                    analysis = json.loads(prop_dict["analysis_json"])
                    prop_dict["quality_summary"] = analysis.get("summary", "")
                    value = analysis.get("value") or {}
                    prop_dict["value_rating"] = value.get("quality_adjusted_rating") or value.get(
                        "rating"
                    )
                    # Extended quality fields for card display
                    # Defensive: filter out junk entries (commas, empties) from
                    # old analyses where Claude returned malformed lists
                    raw_hl = analysis.get("highlights")
                    raw_ll = analysis.get("lowlights")
                    prop_dict["highlights"] = (
                        [t for t in raw_hl if isinstance(t, str) and t.strip() not in ("", ",")]
                        if isinstance(raw_hl, list)
                        else None
                    )
                    prop_dict["lowlights"] = (
                        [t for t in raw_ll if isinstance(t, str) and t.strip() not in ("", ",")]
                        if isinstance(raw_ll, list)
                        else None
                    )
                    prop_dict["one_line"] = analysis.get("one_line")
                    listing_ext = analysis.get("listing_extraction") or {}
                    prop_dict["property_type"] = listing_ext.get("property_type")
                    prop_dict["epc_rating"] = listing_ext.get("epc_rating")
                    # Marcel fit score + breakdown + lifestyle icons
                    # Inject area hosting tolerance so dashboard matches detail page
                    postcode = prop_dict.get("postcode") or ""
                    outcode = postcode.split()[0] if postcode else None
                    if outcode:
                        ht = HOSTING_TOLERANCE.get(outcode)
                        if ht:
                            analysis["_area_hosting_tolerance"] = ht.get("rating")
                    bedrooms = prop_dict.get("bedrooms", 0) or 0
                    prop_dict["fit_score"] = compute_fit_score(analysis, bedrooms)
                    prop_dict["fit_breakdown"] = compute_fit_breakdown(analysis, bedrooms)
                    prop_dict["lifestyle_icons"] = compute_lifestyle_icons(analysis, bedrooms)
                except (json.JSONDecodeError, TypeError):
                    prop_dict["quality_summary"] = ""
                    prop_dict["value_rating"] = None
                    prop_dict["highlights"] = None
                    prop_dict["lowlights"] = None
                    prop_dict["one_line"] = None
                    prop_dict["property_type"] = None
                    prop_dict["epc_rating"] = None
                    prop_dict["fit_score"] = None
                    prop_dict["fit_breakdown"] = None
                    prop_dict["lifestyle_icons"] = None
            else:
                prop_dict["quality_summary"] = ""
                prop_dict["value_rating"] = None
                prop_dict["highlights"] = None
                prop_dict["lowlights"] = None
                prop_dict["one_line"] = None
                prop_dict["property_type"] = None
                prop_dict["epc_rating"] = None
                prop_dict["fit_score"] = None
                prop_dict["fit_breakdown"] = None
                prop_dict["lifestyle_icons"] = None
            properties.append(cast(PropertyListItem, prop_dict))

        if is_fit_sort:
            # Sort by fit_score descending (None → bottom), then by first_seen
            properties.sort(
                key=lambda p: (p.get("fit_score") is not None, p.get("fit_score") or 0),
                reverse=True,
            )
            # Manual pagination
            offset = (page - 1) * per_page
            properties = properties[offset : offset + per_page]

        return properties, total

    async def get_property_detail(self, unique_id: str) -> PropertyDetailItem | None:
        """Get full property detail including quality analysis and images.

        Args:
            unique_id: Property unique ID.

        Returns:
            Dict with property data, quality analysis, and images, or None.
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT p.*, q.analysis_json, q.overall_rating as quality_rating
            FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE p.unique_id = ?
            """,
            (unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        prop_dict = dict(row)
        self._parse_json_fields(prop_dict)

        # Parse quality analysis
        if prop_dict.get("analysis_json"):
            prop_dict["quality_analysis"] = PropertyQualityAnalysis.model_validate_json(
                prop_dict["analysis_json"]
            )
        else:
            prop_dict["quality_analysis"] = None

        # Get images
        images = await self.get_property_images(unique_id)
        prop_dict["gallery_images"] = [img for img in images if img.image_type == "gallery"]
        prop_dict["floorplan_images"] = [img for img in images if img.image_type == "floorplan"]

        return cast(PropertyDetailItem, prop_dict)

    async def get_property_count(self) -> int:
        """Get total number of tracked properties.

        Returns:
            Count of properties in database.
        """
        conn = await self._get_connection()
        cursor = await conn.execute("SELECT COUNT(*) FROM properties")
        row = await cursor.fetchone()
        return row[0] if row else 0

    @staticmethod
    def _parse_json_fields(prop_dict: dict[str, Any]) -> None:
        """Parse common JSON-encoded fields in a property dict (mutates in place)."""
        if prop_dict.get("sources"):
            prop_dict["sources_list"] = json.loads(prop_dict["sources"])
        else:
            prop_dict["sources_list"] = [prop_dict.get("source", "")]

        if prop_dict.get("source_urls"):
            prop_dict["source_urls_dict"] = json.loads(prop_dict["source_urls"])
        else:
            prop_dict["source_urls_dict"] = {}

        if prop_dict.get("descriptions_json"):
            prop_dict["descriptions_dict"] = json.loads(prop_dict["descriptions_json"])
        else:
            prop_dict["descriptions_dict"] = {}

    def _row_to_tracked_property(self, row: aiosqlite.Row) -> TrackedProperty:
        """Convert a database row to a TrackedProperty.

        Args:
            row: Database row.

        Returns:
            TrackedProperty instance.
        """
        # Parse datetime fields
        first_seen = datetime.fromisoformat(row["first_seen"])
        available_from = (
            datetime.fromisoformat(row["available_from"]) if row["available_from"] else None
        )
        notified_at = datetime.fromisoformat(row["notified_at"]) if row["notified_at"] else None

        # Build Property
        prop = Property(
            source=PropertySource(row["source"]),
            source_id=row["source_id"],
            url=row["url"],
            title=row["title"],
            price_pcm=row["price_pcm"],
            bedrooms=row["bedrooms"],
            address=row["address"],
            postcode=row["postcode"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            description=row["description"],
            image_url=row["image_url"] if row["image_url"] else None,
            available_from=available_from,
            first_seen=first_seen,
        )

        # Build TrackedProperty
        return TrackedProperty(
            property=prop,
            commute_minutes=row["commute_minutes"],
            transport_mode=(
                TransportMode(row["transport_mode"]) if row["transport_mode"] else None
            ),
            notification_status=NotificationStatus(row["notification_status"]),
            notified_at=notified_at,
        )
