"""SQLite storage for tracked properties."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import aiosqlite

from home_finder.data.area_context import HOSTING_TOLERANCE
from home_finder.db.migrations import run_migrations
from home_finder.db.pipeline_repo import PipelineRepository
from home_finder.db.row_mappers import (
    build_base_insert,
    build_merged_insert_columns,
    row_to_merged_property,
    row_to_property,
)
from home_finder.db.source_listing_ops import link_source_listings_by_url
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
    UserStatus,
)

# Default lookback window for cross-platform dedup anchors
_DEDUP_LOOKBACK_DAYS: Final = 30

# Re-export TypedDicts for backward compatibility
__all__ = ["PropertyStorage"]

logger = get_logger(__name__)

_CONNECTION_PRAGMAS: Final[tuple[str, ...]] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA cache_size=-32000",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA optimize=0x10002",
)

_CLOSE_PRAGMAS: Final[tuple[str, ...]] = (
    "PRAGMA optimize",
    "PRAGMA wal_checkpoint(PASSIVE)",
)


class PropertyStorage:
    """SQLite-based storage for tracked properties.

    Core property CRUD and domain methods live directly on this class.
    Pipeline operations: ``self.pipeline`` (PipelineRepository).
    Web dashboard queries: ``self.web`` (WebQueryService).
    """

    # Interval between SELECT 1 health probes (seconds)
    _HEALTH_CHECK_INTERVAL: Final = 30.0

    def __init__(self, db_path: str) -> None:
        """Initialize storage with database path.

        Args:
            db_path: Path to SQLite database file, or ":memory:" for in-memory.
        """
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._last_health_check: float = 0.0
        self._ensure_directory()
        self._web = WebQueryService(self._get_connection, self.get_property_images)
        self._pipeline = PipelineRepository(
            self._get_connection,
            self.get_property_images,
            self.save_quality_analysis,
            self._transaction,
        )

    @property
    def pipeline(self) -> PipelineRepository:
        """Pipeline run tracking, analysis retry, and enrichment retry operations."""
        return self._pipeline

    @property
    def web(self) -> WebQueryService:
        """Read-only web dashboard query operations."""
        return self._web

    def _ensure_directory(self) -> None:
        """Ensure the directory for the database exists."""
        if self.db_path != ":memory:":
            path = Path(self.db_path)
            path.parent.mkdir(parents=True, exist_ok=True)

    async def _get_connection(self) -> aiosqlite.Connection:
        """Get or create the database connection, reconnecting if dead.

        Uses a throttled ``SELECT 1`` probe every 30 s to detect hung or
        corrupted connections, then reconnects with full PRAGMA configuration.
        """
        if self._conn is not None:  # noqa: SIM102
            # Throttled SELECT 1 probe
            if (time.monotonic() - self._last_health_check) > self._HEALTH_CHECK_INTERVAL:
                try:
                    await asyncio.wait_for(
                        self._conn.execute("SELECT 1"), timeout=5.0
                    )
                    self._last_health_check = time.monotonic()
                except Exception:
                    logger.warning("db_connection_dead", db_path=self.db_path)
                    # Don't await close() — if the worker thread is hung,
                    # close() queues through the same SimpleQueue and hangs too.
                    # Let GC / __del__ clean up the orphaned connection.
                    self._conn = None

        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            for pragma in _CONNECTION_PRAGMAS:
                await self._conn.execute(pragma)
            self._last_health_check = time.monotonic()
        return self._conn

    async def __aenter__(self) -> PropertyStorage:
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            try:
                for pragma in _CLOSE_PRAGMAS:
                    await self._conn.execute(pragma)
            except Exception:
                logger.debug("close_pragmas_failed", exc_info=True)
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Explicit transaction scope with automatic rollback on error.

        Usage:
            async with self._transaction() as conn:
                await conn.execute(...)
                await conn.execute(...)
            # Commits on clean exit, rolls back on exception
        """
        conn = await self._get_connection()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

    async def initialize(self) -> None:
        """Initialize the database schema via versioned migrations."""
        conn = await self._get_connection()
        version = await run_migrations(conn)
        await self._backfill_fit_scores(conn)
        # Commit needed for _backfill_fit_scores DML; migrations handle their own commits.
        await conn.commit()
        logger.info("database_initialized", db_path=self.db_path, schema_version=version)

    async def _backfill_fit_scores(self, conn: aiosqlite.Connection) -> None:
        """Backfill fit_score for rows that lack it or have an outdated version."""
        from home_finder.filters.fit_score import FIT_SCORE_VERSION

        cursor = await conn.execute(
            """
            SELECT q.property_unique_id, q.analysis_json, p.bedrooms, p.postcode
            FROM quality_analyses q
            JOIN properties p ON p.unique_id = q.property_unique_id
            WHERE q.analysis_json IS NOT NULL
              AND (q.fit_score IS NULL
                   OR q.fit_score_version IS NULL
                   OR q.fit_score_version != ?)
            """,
            (FIT_SCORE_VERSION,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return

        updates: list[tuple[int | None, int, str]] = []
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
                updates.append((score, FIT_SCORE_VERSION, row["property_unique_id"]))

        if updates:
            await conn.executemany(
                "UPDATE quality_analyses SET fit_score = ?, fit_score_version = ?"
                " WHERE property_unique_id = ?",
                updates,
            )
            logger.info(
                "fit_score_backfill_complete", updated=len(updates), version=FIT_SCORE_VERSION
            )

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
        # Keep source_listings in sync
        await self._upsert_source_listing(conn, prop, merged_id=prop.unique_id)
        await conn.commit()

        logger.debug("property_saved", unique_id=prop.unique_id)

    async def is_seen(self, unique_id: str) -> bool:
        """Check if a property has been seen before.

        A property is "seen" if it has been promoted to a golden record
        (``merged_id IS NOT NULL`` in ``source_listings``).  Unlinked
        scrape-time entries are not considered "seen" — they haven't
        passed through the pipeline yet.

        Args:
            unique_id: Unique property identifier.

        Returns:
            True if property exists in source_listings with a merged_id.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT 1 FROM source_listings WHERE unique_id = ? AND merged_id IS NOT NULL LIMIT 1",
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
        """Batch-check which unique IDs have been promoted to golden records.

        Only source_listings with ``merged_id IS NOT NULL`` are considered
        "seen" — unlinked scrape-time entries haven't passed through the
        pipeline yet and should not block processing.

        Args:
            unique_ids: List of unique IDs to check.

        Returns:
            Set of IDs that have been promoted in source_listings.
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
                f"SELECT unique_id FROM source_listings"
                f" WHERE unique_id IN ({placeholders}) AND merged_id IS NOT NULL",
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
        """Filter to only merged properties not yet promoted to golden records.

        A merged property is considered "seen" if its canonical unique_id has
        ``merged_id IS NOT NULL`` in ``source_listings`` — i.e. it was promoted
        to a golden record in a prior run.  Listings that were only scraped but
        never saved (filtered out, failed enrichment) are *not* considered seen
        and will be re-processed.

        Args:
            properties: List of merged properties to filter.

        Returns:
            List of merged properties not yet promoted.
        """
        seen = await self._get_seen_ids([m.canonical.unique_id for m in properties])
        return [m for m in properties if m.canonical.unique_id not in seen]

    async def delete_property(self, unique_id: str) -> None:
        """Delete a property and all related rows from the database.

        Used to clean up unenriched rows consumed by cross-platform anchor merges.
        Linked source_listings have their ``merged_id`` set to NULL automatically
        via the FK ``ON DELETE SET NULL``, so they can re-enter the pipeline.
        """
        async with self._transaction() as conn:
            for table in (
                "property_images",
                "quality_analyses",
                "status_events",
                "viewing_messages",
                "price_history",
                "enquiry_log",
            ):
                await conn.execute(
                    f"DELETE FROM {table} WHERE property_unique_id = ?",
                    (unique_id,),
                )
            await conn.execute(
                "DELETE FROM properties WHERE unique_id = ?",
                (unique_id,),
            )
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
        if not rows:
            logger.debug("loaded_dedup_anchors", count=0, days=days)
            return []

        # Batch-load source_listings for all properties
        unique_ids = [row["unique_id"] for row in rows]
        sl_by_merged: dict[str, list[aiosqlite.Row]] = {}
        chunk_size = 500
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            sl_cursor = await conn.execute(
                f"SELECT * FROM source_listings WHERE merged_id IN ({placeholders})",
                chunk,
            )
            for sl in await sl_cursor.fetchall():
                sl_by_merged.setdefault(sl["merged_id"], []).append(sl)

        results = [
            await row_to_merged_property(
                row,
                source_listings=sl_by_merged.get(row["unique_id"]),
                get_property_images=self.get_property_images,
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
        *,
        absorbed_ids: list[str] | None = None,
    ) -> None:
        """Update golden record's denormalized caches from source_listings.

        Called after cross-run dedup links new source listings to an anchor.
        Rebuilds JSON columns and price range from source_listings rows.

        Args:
            existing_unique_id: The unique_id of the existing DB record.
            merged: The MergedProperty containing combined source data
                (used only for saving new images).
            absorbed_ids: unique_ids of source_listings to link to this
                golden record before rebuilding.  When provided, sets
                ``merged_id`` on matching source_listings rows.
        """
        async with self._transaction() as conn:
            # Link absorbed source listings to this golden record
            if absorbed_ids:
                await conn.executemany(
                    "UPDATE source_listings SET merged_id = ? WHERE unique_id = ?",
                    [(existing_unique_id, uid) for uid in absorbed_ids],
                )

            # Rebuild denormalized caches from source_listings
            cursor = await conn.execute(
                """SELECT source, url, description, price_pcm
                   FROM source_listings WHERE merged_id = ?
                   ORDER BY last_seen ASC""",
                (existing_unique_id,),
            )
            rows = await cursor.fetchall()
            if not rows:
                logger.warning("update_merged_sources_no_listings", unique_id=existing_unique_id)
                return

            # Deduplicate sources (same platform may appear via multiple listings)
            # ORDER BY last_seen ASC ensures last-write-wins for URLs/descriptions
            sources = list(dict.fromkeys(r["source"] for r in rows))
            source_urls = {r["source"]: r["url"] for r in rows}
            descriptions = {
                r["source"]: r["description"]
                for r in rows
                if r["description"]
            }
            prices = [r["price_pcm"] for r in rows]

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
                    json.dumps(sources),
                    json.dumps(source_urls),
                    json.dumps(descriptions) if descriptions else None,
                    min(prices),
                    max(prices),
                    datetime.now(UTC).isoformat(),
                    existing_unique_id,
                ),
            )

            # Save any new images from the merged property
            new_images = list(merged.images)
            if merged.floorplan:
                new_images.append(merged.floorplan)
            if new_images:
                await self.save_property_images(existing_unique_id, new_images, _commit=False)

        logger.info(
            "merged_sources_updated",
            unique_id=existing_unique_id,
            sources=sources,
            min_price=min(prices),
            max_price=max(prices),
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
        # Keep source_listings in sync — write canonical source
        await self._upsert_source_listing(
            conn, merged.canonical, merged_id=merged.canonical.unique_id
        )
        # Link non-canonical source_listings by URL (in-run dedup absorbed sources)
        await link_source_listings_by_url(
            conn, merged.canonical.unique_id, merged.source_urls
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

    async def save_property_images(
        self, unique_id: str, images: list[PropertyImage], *, _commit: bool = True
    ) -> None:
        """Save property images to the database.

        Args:
            unique_id: Property unique ID.
            images: List of images to save.
            _commit: Whether to commit the transaction. Pass False when
                called from a parent operation that manages its own commit.
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
        if _commit:
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

    async def get_property_images_and_row(
        self, unique_id: str
    ) -> tuple[list[PropertyImage], Property | None]:
        """Load property images and the property row in a single call.

        Combines get_property_images() and row_to_property() to avoid two
        separate round-trips when both are needed (e.g. cache loading).

        Args:
            unique_id: Property unique ID.

        Returns:
            Tuple of (images, property_or_none).
        """
        conn = await self._get_connection()

        # Images
        img_cursor = await conn.execute(
            """
            SELECT source, url, image_type
            FROM property_images
            WHERE property_unique_id = ?
            ORDER BY image_type, id
            """,
            (unique_id,),
        )
        img_rows = await img_cursor.fetchall()
        images = [
            PropertyImage(
                source=PropertySource(r["source"]),
                url=r["url"],
                image_type=r["image_type"],
            )
            for r in img_rows
        ]

        # Property row
        prop_cursor = await conn.execute(
            "SELECT * FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        prop_row = await prop_cursor.fetchone()
        prop = row_to_property(prop_row) if prop_row is not None else None

        return images, prop

    async def get_properties_needing_commute(self) -> list[Property]:
        """Get properties with coordinates but no commute data.

        Used by the --backfill-commute command to find properties that
        can be sent to the TravelTime API.

        Returns:
            List of Property objects with lat/lon but no commute_minutes.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            SELECT * FROM properties
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND commute_minutes IS NULL
            """
        )
        rows = await cursor.fetchall()
        return [row_to_property(row) for row in rows]

    async def update_commute_data(
        self,
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> int:
        """Batch update commute_minutes and transport_mode for properties.

        Args:
            commute_lookup: Mapping of unique_id -> (minutes, transport_mode).

        Returns:
            Number of rows updated.
        """
        if not commute_lookup:
            return 0
        conn = await self._get_connection()
        await conn.executemany(
            """
            UPDATE properties
            SET commute_minutes = ?, transport_mode = ?
            WHERE unique_id = ?
            """,
            [
                (minutes, mode.value, unique_id)
                for unique_id, (minutes, mode) in commute_lookup.items()
            ],
        )
        await conn.commit()
        return len(commute_lookup)

    # ------------------------------------------------------------------
    # Source listings (Layer 1 of golden record pattern)
    # ------------------------------------------------------------------

    async def _upsert_source_listing(
        self,
        conn: aiosqlite.Connection,
        prop: Property,
        *,
        merged_id: str | None = None,
    ) -> None:
        """Upsert a single property into source_listings (no commit).

        Silently skips if the source_listings table doesn't exist yet
        (pre-migration 004 DBs that save properties before migrations run).
        """
        now = datetime.now(UTC).isoformat()
        try:
            await conn.execute(
            """
            INSERT INTO source_listings (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, latitude, longitude,
                description, image_url, available_from, first_seen, last_seen,
                merged_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(unique_id) DO UPDATE SET
                price_pcm = excluded.price_pcm,
                title = excluded.title,
                description = excluded.description,
                image_url = excluded.image_url,
                last_seen = excluded.last_seen,
                latitude = COALESCE(excluded.latitude, latitude),
                longitude = COALESCE(excluded.longitude, longitude),
                postcode = COALESCE(excluded.postcode, postcode),
                merged_id = COALESCE(excluded.merged_id, merged_id)
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
                now,
                merged_id,
            ),
        )
        except aiosqlite.OperationalError as e:
            if "no such table" in str(e).lower():
                return
            raise

    async def upsert_source_listings(self, properties: list[Property]) -> int:
        """Record scraped properties in source_listings (Layer 1).

        Called at scrape time, before any filtering. Updates last_seen
        and mutable fields for existing listings.

        Returns number of rows upserted.
        """
        if not properties:
            return 0
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()
        rows = [
            (
                p.unique_id,
                p.source.value,
                p.source_id,
                str(p.url),
                p.title,
                p.price_pcm,
                p.bedrooms,
                p.address,
                p.postcode,
                p.latitude,
                p.longitude,
                p.description,
                str(p.image_url) if p.image_url else None,
                p.available_from.isoformat() if p.available_from else None,
                p.first_seen.isoformat(),
                now,
            )
            for p in properties
        ]
        await conn.executemany(
            """
            INSERT INTO source_listings (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, postcode, latitude, longitude,
                description, image_url, available_from, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(unique_id) DO UPDATE SET
                price_pcm = excluded.price_pcm,
                title = excluded.title,
                description = excluded.description,
                image_url = excluded.image_url,
                last_seen = excluded.last_seen,
                latitude = COALESCE(excluded.latitude, latitude),
                longitude = COALESCE(excluded.longitude, longitude),
                postcode = COALESCE(excluded.postcode, postcode)
            """,
            rows,
        )
        await conn.commit()
        return len(rows)

    async def link_source_listings(
        self, links: list[tuple[str, str]]
    ) -> None:
        """Link source listings to their golden record.

        Each tuple is (source_listing_unique_id, merged_property_unique_id).
        """
        if not links:
            return
        conn = await self._get_connection()
        await conn.executemany(
            "UPDATE source_listings SET merged_id = ? WHERE unique_id = ?",
            [(merged_id, uid) for uid, merged_id in links],
        )
        await conn.commit()

    async def get_all_known_source_ids(self) -> dict[str, set[str]]:
        """Get all source_ids grouped by property source.

        Queries ``source_listings`` — the single authority for every
        source ID ever seen — so scraper early-stop correctly skips
        previously-seen listings.

        Returns:
            Dict mapping source name to set of source_ids.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT source, source_id FROM source_listings"
        )
        rows = await cursor.fetchall()
        result: dict[str, set[str]] = {}
        for source, source_id in rows:
            result.setdefault(source, set()).add(source_id)
        return result

    async def save_quality_analysis(
        self, unique_id: str, analysis: PropertyQualityAnalysis, *, _commit: bool = True
    ) -> None:
        """Save a quality analysis result for a property.

        Args:
            unique_id: Property unique ID.
            analysis: PropertyQualityAnalysis instance.
            _commit: Whether to commit the transaction. Pass False when
                called from a parent operation that manages its own commit.
        """
        conn = await self._get_connection()

        # Use model_dump(mode="json") + json.dumps() instead of model_dump_json()
        # to guarantee valid JSON. Pydantic's Rust serializer can produce invalid
        # JSON for edge cases (e.g. NaN floats), while json.dumps() is strict.
        try:
            analysis_json = json.dumps(analysis.model_dump(mode="json"))
        except (ValueError, TypeError) as e:
            logger.error(
                "analysis_json_serialization_failed",
                unique_id=unique_id,
                error=str(e),
                exc_info=True,
            )
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

        from home_finder.filters.fit_score import FIT_SCORE_VERSION

        await conn.execute(
            """
            INSERT INTO quality_analyses (
                property_unique_id, analysis_json, overall_rating,
                condition_concerns, concern_severity, epc_rating,
                has_outdoor_space, red_flag_count, fit_score,
                fit_score_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(property_unique_id) DO UPDATE SET
                analysis_json = excluded.analysis_json,
                overall_rating = excluded.overall_rating,
                condition_concerns = excluded.condition_concerns,
                concern_severity = excluded.concern_severity,
                epc_rating = excluded.epc_rating,
                has_outdoor_space = excluded.has_outdoor_space,
                red_flag_count = excluded.red_flag_count,
                fit_score = excluded.fit_score,
                fit_score_version = excluded.fit_score_version
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
                FIT_SCORE_VERSION,
                datetime.now(UTC).isoformat(),
            ),
        )
        if _commit:
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

    async def update_floor_area(
        self, unique_id: str, floor_area_sqm: float, floor_area_source: str
    ) -> None:
        """Update floor area for a property (only if not already set)."""
        conn = await self._get_connection()
        await conn.execute(
            """
            UPDATE properties
            SET floor_area_sqm = ?, floor_area_source = ?
            WHERE unique_id = ? AND floor_area_sqm IS NULL
            """,
            (floor_area_sqm, floor_area_source, unique_id),
        )
        await conn.commit()

    # ------------------------------------------------------------------
    # User status tracking (Ticket 7)
    # ------------------------------------------------------------------

    async def update_user_status(
        self,
        unique_id: str,
        new_status: UserStatus,
        *,
        note: str | None = None,
        source: str = "web",
    ) -> UserStatus | None:
        """Update property user_status and log the event.

        Returns the previous status, or None if property not found.
        """
        async with self._transaction() as conn:
            cursor = await conn.execute(
                "SELECT user_status FROM properties WHERE unique_id = ?",
                (unique_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            from_status = row["user_status"] or "new"

            await conn.execute(
                "UPDATE properties SET user_status = ? WHERE unique_id = ?",
                (new_status.value, unique_id),
            )
            await conn.execute(
                """INSERT INTO status_events
                   (property_unique_id, from_status, to_status, note, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (unique_id, from_status, new_status.value, note, source),
            )
        logger.debug(
            "user_status_updated",
            unique_id=unique_id,
            from_status=from_status,
            to_status=new_status.value,
        )
        return UserStatus(from_status)

    async def get_status_history(self, unique_id: str) -> list[dict[str, Any]]:
        """Get status change history for a property, ordered chronologically."""
        conn = await self._get_connection()
        cursor = await conn.execute(
            """SELECT from_status, to_status, note, source, created_at
               FROM status_events
               WHERE property_unique_id = ?
               ORDER BY created_at ASC""",
            (unique_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Viewing messages (Ticket 8)
    # ------------------------------------------------------------------

    async def get_viewing_message(self, unique_id: str) -> str | None:
        """Get cached viewing message for a property."""
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT message FROM viewing_messages WHERE property_unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
        return row["message"] if row else None

    async def save_viewing_message(self, unique_id: str, message: str) -> None:
        """Save or replace a viewing message for a property."""
        conn = await self._get_connection()
        await conn.execute(
            """INSERT INTO viewing_messages (property_unique_id, message)
               VALUES (?, ?)
               ON CONFLICT(property_unique_id) DO UPDATE SET
                   message = excluded.message,
                   created_at = CURRENT_TIMESTAMP""",
            (unique_id, message),
        )
        await conn.commit()

    async def delete_viewing_message(self, unique_id: str) -> None:
        """Delete cached viewing message (for regeneration)."""
        conn = await self._get_connection()
        await conn.execute(
            "DELETE FROM viewing_messages WHERE property_unique_id = ?",
            (unique_id,),
        )
        await conn.commit()

    # ------------------------------------------------------------------
    # Price history (Ticket 10)
    # ------------------------------------------------------------------

    async def detect_and_record_price_change(
        self,
        unique_id: str,
        new_price: int,
        source: str | None = None,
    ) -> int | None:
        """Compare new_price against DB; record change if different.

        When a price change is detected, also recomputes the rule-based
        value rating and fit_score in quality_analyses (if analysis exists).
        Clears the stale LLM quality_adjusted_rating since it was assessed
        at the old price.

        Returns change_amount (negative = drop) or None if no change / not found.
        """
        async with self._transaction() as conn:
            cursor = await conn.execute(
                "SELECT price_pcm FROM properties WHERE unique_id = ?",
                (unique_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            old_price = row["price_pcm"]
            if old_price == new_price:
                return None

            change_amount = new_price - old_price
            await conn.execute(
                """INSERT INTO price_history
                   (property_unique_id, old_price, new_price, change_amount, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (unique_id, old_price, new_price, change_amount, source),
            )
            await conn.execute(
                "UPDATE properties SET price_pcm = ?, price_drop_notified = 0 WHERE unique_id = ?",
                (new_price, unique_id),
            )

            # Recompute value rating and fit_score for the new price
            qa_cursor = await conn.execute(
                "SELECT q.analysis_json, p.postcode, p.bedrooms "
                "FROM quality_analyses q "
                "JOIN properties p ON p.unique_id = q.property_unique_id "
                "WHERE q.property_unique_id = ?",
                (unique_id,),
            )
            qa_row = await qa_cursor.fetchone()
            if qa_row and qa_row["analysis_json"]:
                try:
                    from home_finder.filters.quality import assess_value

                    analysis = json.loads(qa_row["analysis_json"])
                    new_value = assess_value(new_price, qa_row["postcode"], qa_row["bedrooms"] or 0)
                    value_dict = analysis.get("value") or {}
                    value_dict.update(
                        {
                            "area_average": new_value.area_average,
                            "difference": new_value.difference,
                            "rating": new_value.rating,
                            "note": new_value.note,
                            "quality_adjusted_rating": None,
                            "quality_adjusted_note": "",
                        }
                    )
                    analysis["value"] = value_dict
                    updated_json = json.dumps(analysis)

                    # Recompute fit_score
                    from home_finder.filters.fit_score import FIT_SCORE_VERSION

                    postcode = qa_row["postcode"] or ""
                    outcode = postcode.split()[0] if postcode else None
                    if outcode:
                        ht = HOSTING_TOLERANCE.get(outcode)
                        if ht:
                            analysis["_area_hosting_tolerance"] = ht.get("rating")
                    fit = compute_fit_score(analysis, qa_row["bedrooms"] or 0)

                    await conn.execute(
                        "UPDATE quality_analyses "
                        "SET analysis_json = ?, fit_score = ?, fit_score_version = ? "
                        "WHERE property_unique_id = ?",
                        (updated_json, fit, FIT_SCORE_VERSION, unique_id),
                    )
                except (json.JSONDecodeError, TypeError, ImportError):
                    logger.debug(
                        "value_recompute_skipped",
                        unique_id=unique_id,
                        reason="json_or_import_error",
                    )

        logger.info(
            "price_change_detected",
            unique_id=unique_id,
            old_price=old_price,
            new_price=new_price,
            change=change_amount,
        )
        return int(change_amount)

    async def get_price_history(self, unique_id: str) -> list[dict[str, Any]]:
        """Get price change history for a property, newest first."""
        conn = await self._get_connection()
        cursor = await conn.execute(
            """SELECT old_price, new_price, change_amount, source, detected_at
               FROM price_history
               WHERE property_unique_id = ?
               ORDER BY detected_at DESC""",
            (unique_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_unsent_price_drops(self) -> list[dict[str, Any]]:
        """Get properties with unnotified price drops for Telegram alerts."""
        conn = await self._get_connection()
        cursor = await conn.execute(
            """SELECT p.unique_id, p.title, p.postcode, p.first_seen, p.url,
                      ph.old_price, ph.new_price, ph.change_amount
               FROM properties p
               JOIN price_history ph ON ph.property_unique_id = p.unique_id
               WHERE p.price_drop_notified = 0
                 AND ph.change_amount < 0
                 AND p.notification_status = 'sent'
               ORDER BY ph.detected_at DESC"""
        )
        rows = await cursor.fetchall()
        # Deduplicate: only latest drop per property
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for row in rows:
            uid = row["unique_id"]
            if uid not in seen:
                seen.add(uid)
                results.append(dict(row))
        return results

    async def mark_price_drop_notified(self, unique_id: str) -> None:
        """Mark a property's price drop as notified."""
        conn = await self._get_connection()
        await conn.execute(
            "UPDATE properties SET price_drop_notified = 1 WHERE unique_id = ?",
            (unique_id,),
        )
        await conn.commit()

    # ------------------------------------------------------------------
    # Enquiry log (Ticket 9)
    # ------------------------------------------------------------------

    async def log_enquiry(self, result: dict[str, Any]) -> None:
        """Record an enquiry attempt (INSERT or UPDATE on conflict)."""
        conn = await self._get_connection()
        await conn.execute(
            """INSERT INTO enquiry_log
               (property_unique_id, portal, message, status, submitted_at, error, screenshot_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(property_unique_id, portal) DO UPDATE SET
                   status = excluded.status,
                   submitted_at = excluded.submitted_at,
                   error = excluded.error,
                   screenshot_path = excluded.screenshot_path""",
            (
                result["property_unique_id"],
                result["portal"],
                result["message"],
                result["status"],
                result.get("submitted_at"),
                result.get("error"),
                result.get("screenshot_path"),
            ),
        )
        await conn.commit()

    async def get_enquiries_for_property(self, unique_id: str) -> list[dict[str, Any]]:
        """Get all enquiry attempts for a property."""
        conn = await self._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM enquiry_log WHERE property_unique_id = ? ORDER BY created_at DESC",
            (unique_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def has_enquiry(self, unique_id: str, portal: str) -> bool:
        """Check if an enquiry has already been submitted for this property+portal."""
        conn = await self._get_connection()
        cursor = await conn.execute(
            """SELECT 1 FROM enquiry_log
               WHERE property_unique_id = ? AND portal = ? AND status = 'submitted'""",
            (unique_id, portal),
        )
        return await cursor.fetchone() is not None

    # ------------------------------------------------------------------
    # Off-market detection
    # ------------------------------------------------------------------

    async def mark_off_market(
        self, unique_id: str, *, reason: str | None = None
    ) -> bool:
        """Mark a property as off-market. Preserves original off_market_since date.

        Returns True if the row was updated, False if property not found.
        """
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()
        cursor = await conn.execute(
            """
            UPDATE properties
            SET is_off_market = 1,
                off_market_since = COALESCE(off_market_since, ?),
                off_market_reason = COALESCE(off_market_reason, ?)
            WHERE unique_id = ?
            """,
            (now, reason, unique_id),
        )
        await conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info(
                "property_marked_off_market",
                unique_id=unique_id,
                reason=reason,
            )
        return updated

    async def mark_returned_to_market(self, unique_id: str) -> bool:
        """Clear off-market flag and append to history.

        Before clearing, reads current off_market_since/reason and appends
        a ``{"off": ..., "back": ..., "reason": ...}`` entry to
        ``off_market_history``.

        Returns True if the row was updated, False if property not found.
        """
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()

        # Read current off-market state for history
        cursor = await conn.execute(
            "SELECT off_market_since, off_market_reason, off_market_history "
            "FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False

        # Build history entry
        history: list[dict[str, str | None]] = []
        if row["off_market_history"]:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                history = json.loads(row["off_market_history"])
        if row["off_market_since"]:
            history.append({
                "off": row["off_market_since"],
                "back": now,
                "reason": row["off_market_reason"],
            })

        cursor = await conn.execute(
            """
            UPDATE properties
            SET is_off_market = 0,
                off_market_since = NULL,
                off_market_reason = NULL,
                off_market_history = ?
            WHERE unique_id = ?
            """,
            (json.dumps(history) if history else None, unique_id),
        )
        await conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info("property_returned_to_market", unique_id=unique_id)
        return updated

    async def get_properties_for_off_market_check(
        self, *, sources: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Get golden records with per-source URLs for off-market checking.

        For each golden record, prefers source_listings (authoritative) when
        linked rows exist, falling back to properties.source_urls JSON for
        legacy records without linkage.

        Returns list of dicts with keys:
            unique_id, url, source, source_urls, is_off_market,
            source_listings (list of dicts with unique_id, source, url,
            is_off_market, last_checked_at — or empty list for legacy).
        """
        conn = await self._get_connection()

        # Step 1: Get eligible golden records
        cursor = await conn.execute(
            """
            SELECT unique_id, url, source, source_urls, is_off_market
            FROM properties
            WHERE COALESCE(enrichment_status, 'enriched') != 'pending'
              AND notification_status NOT IN ('pending_enrichment', 'pending_analysis', 'dropped')
            ORDER BY first_seen DESC
            """
        )
        rows = await cursor.fetchall()
        golden_records = [dict(row) for row in rows]

        if not golden_records:
            return []

        # Step 2: Batch-fetch all linked source_listings
        all_ids = [r["unique_id"] for r in golden_records]
        placeholders = ",".join("?" * len(all_ids))
        cursor = await conn.execute(
            f"""
            SELECT unique_id, source, url, merged_id, is_off_market, last_checked_at
            FROM source_listings
            WHERE merged_id IN ({placeholders})
            """,
            all_ids,
        )
        sl_rows = await cursor.fetchall()

        # Group source_listings by merged_id
        sl_by_merged: dict[str, list[dict[str, Any]]] = {}
        for sl_row in sl_rows:
            sl = dict(sl_row)
            sl_by_merged.setdefault(sl["merged_id"], []).append(sl)

        # Step 3: Attach source_listings to golden records
        results: list[dict[str, Any]] = []
        for gr in golden_records:
            linked_sls = sl_by_merged.get(gr["unique_id"], [])
            gr["source_listings"] = linked_sls
            results.append(gr)

        # Step 4: Filter by source if requested
        if sources:
            filtered = []
            for r in results:
                # Check source_listings first
                if r["source_listings"] and any(
                    sl["source"] in sources for sl in r["source_listings"]
                ):
                    filtered.append(r)
                    continue
                # Also check primary source and source_urls JSON
                # (covers partial linkage and legacy records)
                if r["source"] in sources:
                    filtered.append(r)
                    continue
                if r.get("source_urls"):
                    try:
                        urls_dict = json.loads(r["source_urls"])
                        if any(s in sources for s in urls_dict):
                            filtered.append(r)
                    except (json.JSONDecodeError, TypeError):
                        pass
            results = filtered

        return results

    # ------------------------------------------------------------------
    # Per-source off-market tracking (source_listings)
    # ------------------------------------------------------------------

    async def mark_source_listing_off_market(
        self, unique_id: str, reason: str
    ) -> bool:
        """Mark a source_listing as off-market. Preserves original timestamp.

        Returns True if the row was updated, False if not found.
        """
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()
        cursor = await conn.execute(
            """
            UPDATE source_listings
            SET is_off_market = 1,
                off_market_since = COALESCE(off_market_since, ?),
                off_market_reason = ?
            WHERE unique_id = ?
            """,
            (now, reason, unique_id),
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def mark_source_listing_active(self, unique_id: str) -> bool:
        """Clear off-market flags on a source_listing (return-to-market).

        Returns True if the row was updated, False if not found.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            UPDATE source_listings
            SET is_off_market = 0, off_market_since = NULL, off_market_reason = NULL
            WHERE unique_id = ?
            """,
            (unique_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def update_source_listing_last_checked(self, unique_id: str) -> bool:
        """Stamp last_checked_at on a source_listing.

        Returns True if the row was updated, False if not found.
        """
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()
        cursor = await conn.execute(
            "UPDATE source_listings SET last_checked_at = ? WHERE unique_id = ?",
            (now, unique_id),
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def update_property_last_checked(self, unique_id: str) -> bool:
        """Stamp last_checked_at on a golden record.

        Returns True if the row was updated, False if not found.
        """
        conn = await self._get_connection()
        now = datetime.now(UTC).isoformat()
        cursor = await conn.execute(
            "UPDATE properties SET last_checked_at = ? WHERE unique_id = ?",
            (now, unique_id),
        )
        await conn.commit()
        return cursor.rowcount > 0

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
