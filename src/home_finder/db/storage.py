"""SQLite storage for tracked properties."""

from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from home_finder.logging import get_logger
from home_finder.models import (
    NotificationStatus,
    Property,
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        cursor = await conn.execute(
            "SELECT * FROM properties ORDER BY first_seen DESC"
        )
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
                datetime.now().isoformat(),
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

    async def filter_new(self, properties: list[Property]) -> list[Property]:
        """Filter to only properties not yet seen.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties not in the database.
        """
        new_properties = []
        for prop in properties:
            if not await self.is_seen(prop.unique_id):
                new_properties.append(prop)
        return new_properties

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
            datetime.fromisoformat(row["available_from"])
            if row["available_from"]
            else None
        )
        notified_at = (
            datetime.fromisoformat(row["notified_at"]) if row["notified_at"] else None
        )

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
