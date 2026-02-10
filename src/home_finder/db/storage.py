"""SQLite storage for tracked properties."""

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from home_finder.logging import get_logger
from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    Property,
    PropertyImage,
    PropertySource,
    TrackedProperty,
    TransportMode,
)

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
        for column, col_type in [
            ("sources", "TEXT"),
            ("source_urls", "TEXT"),
            ("min_price", "INTEGER"),
            ("max_price", "INTEGER"),
            ("descriptions_json", "TEXT"),
        ]:
            with contextlib.suppress(Exception):
                await conn.execute(f"ALTER TABLE properties ADD COLUMN {column} {col_type}")

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
                f"SELECT unique_id FROM properties WHERE unique_id IN ({placeholders})",  # noqa: S608
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

    async def save_merged_property(
        self,
        merged: MergedProperty,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
    ) -> None:
        """Save a merged property with multi-source data.

        Args:
            merged: Merged property to save.
            commute_minutes: Commute time in minutes (if calculated).
            transport_mode: Transport mode used for commute calculation.
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
                sources, source_urls, min_price, max_price, descriptions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                descriptions_json = COALESCE(excluded.descriptions_json, descriptions_json)
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
            ),
        )
        await conn.commit()

        logger.debug(
            "merged_property_saved",
            unique_id=prop.unique_id,
            sources=[s.value for s in merged.sources],
        )

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

    async def save_quality_analysis(self, unique_id: str, analysis: Any) -> None:
        """Save a quality analysis result for a property.

        Args:
            unique_id: Property unique ID.
            analysis: PropertyQualityAnalysis instance.
        """
        conn = await self._get_connection()
        analysis_json = analysis.model_dump_json()

        # Denormalize key fields for SQL filtering
        overall_rating = analysis.overall_rating
        condition_concerns = analysis.condition_concerns
        concern_severity = analysis.concern_severity

        await conn.execute(
            """
            INSERT INTO quality_analyses (
                property_unique_id, analysis_json, overall_rating,
                condition_concerns, concern_severity, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(property_unique_id) DO UPDATE SET
                analysis_json = excluded.analysis_json,
                overall_rating = excluded.overall_rating,
                condition_concerns = excluded.condition_concerns,
                concern_severity = excluded.concern_severity
            """,
            (
                unique_id,
                analysis_json,
                overall_rating,
                condition_concerns,
                concern_severity,
                datetime.now(UTC).isoformat(),
            ),
        )
        await conn.commit()
        logger.debug("quality_analysis_saved", unique_id=unique_id)

    async def get_quality_analysis(self, unique_id: str) -> Any | None:
        """Get quality analysis for a property.

        Args:
            unique_id: Property unique ID.

        Returns:
            PropertyQualityAnalysis if found, None otherwise.
        """
        from home_finder.filters.quality import PropertyQualityAnalysis

        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT analysis_json FROM quality_analyses WHERE property_unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return PropertyQualityAnalysis.model_validate_json(row["analysis_json"])

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
    ) -> tuple[list[dict[str, Any]], int]:
        """Get paginated properties with optional filters.

        Returns:
            Tuple of (property dicts, total count).
        """
        conn = await self._get_connection()

        where_clauses: list[str] = []
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

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        order_map = {
            "newest": "p.first_seen DESC",
            "price_asc": "p.price_pcm ASC",
            "price_desc": "p.price_pcm DESC",
            "rating_desc": "COALESCE(q.overall_rating, 0) DESC, p.first_seen DESC",
        }
        order_sql = order_map.get(sort, "p.first_seen DESC")

        # Count total
        count_cursor = await conn.execute(
            f"""
            SELECT COUNT(*) FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
            """,  # noqa: S608
            params,
        )
        count_row = await count_cursor.fetchone()
        total = count_row[0] if count_row else 0

        # Fetch page
        offset = (page - 1) * per_page
        cursor = await conn.execute(
            f"""
            SELECT p.*, q.overall_rating as quality_rating,
                   q.condition_concerns as quality_concerns,
                   q.concern_severity as quality_severity,
                   q.analysis_json
            FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,  # noqa: S608
            [*params, per_page, offset],
        )
        rows = await cursor.fetchall()

        properties = []
        for row in rows:
            prop_dict = dict(row)
            # Parse sources JSON
            if prop_dict.get("sources"):
                prop_dict["sources_list"] = json.loads(prop_dict["sources"])
            else:
                prop_dict["sources_list"] = [prop_dict.get("source", "")]
            # Parse source_urls JSON
            if prop_dict.get("source_urls"):
                prop_dict["source_urls_dict"] = json.loads(prop_dict["source_urls"])
            else:
                prop_dict["source_urls_dict"] = {}
            # Extract quality summary from analysis_json
            if prop_dict.get("analysis_json"):
                try:
                    analysis = json.loads(prop_dict["analysis_json"])
                    prop_dict["quality_summary"] = analysis.get("summary", "")
                except (json.JSONDecodeError, TypeError):
                    prop_dict["quality_summary"] = ""
            else:
                prop_dict["quality_summary"] = ""
            properties.append(prop_dict)

        return properties, total

    async def get_property_detail(self, unique_id: str) -> dict[str, Any] | None:
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

        # Parse JSON fields
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

        # Parse quality analysis
        if prop_dict.get("analysis_json"):
            from home_finder.filters.quality import PropertyQualityAnalysis

            prop_dict["quality_analysis"] = PropertyQualityAnalysis.model_validate_json(
                prop_dict["analysis_json"]
            )
        else:
            prop_dict["quality_analysis"] = None

        # Get images
        images = await self.get_property_images(unique_id)
        prop_dict["gallery_images"] = [img for img in images if img.image_type == "gallery"]
        prop_dict["floorplan_images"] = [img for img in images if img.image_type == "floorplan"]

        return prop_dict

    async def get_property_count(self) -> int:
        """Get total number of tracked properties.

        Returns:
            Count of properties in database.
        """
        conn = await self._get_connection()
        cursor = await conn.execute("SELECT COUNT(*) FROM properties")
        row = await cursor.fetchone()
        return row[0] if row else 0

    def _row_to_tracked_property(self, row: Any) -> TrackedProperty:
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
