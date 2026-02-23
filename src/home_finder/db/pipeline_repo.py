"""Pipeline repository — methods for pipeline runs, analysis retry, and reanalysis."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from home_finder.pipeline.event_recorder import PropertyEvent

import aiosqlite

from home_finder.db.row_mappers import build_merged_insert_columns, row_to_merged_property
from home_finder.logging import get_logger
from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    PropertyImage,
    PropertyQualityAnalysis,
    TransportMode,
)

logger = get_logger(__name__)


class PipelineRepository:
    """Database operations for the pipeline: runs, analysis retry, reanalysis."""

    def __init__(
        self,
        get_connection: Callable[[], Coroutine[Any, Any, aiosqlite.Connection]],
        get_property_images: Callable[[str], Coroutine[Any, Any, list[PropertyImage]]],
        save_quality_analysis: Callable[..., Coroutine[Any, Any, None]],
        transaction: Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]],
    ) -> None:
        self._get_connection = get_connection
        self._get_property_images = get_property_images
        self._save_quality_analysis = save_quality_analysis
        self._transaction = transaction

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

    async def update_pipeline_run(self, run_id: int, **counts: int | float) -> None:
        """Update count columns on a pipeline run.

        Args:
            run_id: The pipeline run ID.
            **counts: Column name/value pairs to update (e.g. scraped_count=42,
                scraping_seconds=12.3).
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

    async def save_scraper_runs(
        self, pipeline_run_id: int | None, metrics_list: list[dict[str, Any]]
    ) -> None:
        """Persist per-scraper performance metrics for a pipeline run.

        Args:
            pipeline_run_id: The pipeline run ID (may be None for dry runs).
            metrics_list: List of dicts with scraper metric fields.
        """
        if not metrics_list:
            return
        conn = await self._get_connection()
        for m in metrics_list:
            await conn.execute(
                """
                INSERT INTO scraper_runs (
                    pipeline_run_id, scraper_name, started_at, completed_at,
                    duration_seconds, areas_attempted, areas_completed,
                    properties_found, pages_fetched, pages_failed,
                    parse_errors, is_healthy, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pipeline_run_id,
                    m["scraper_name"],
                    m["started_at"],
                    m.get("completed_at"),
                    m.get("duration_seconds"),
                    m.get("areas_attempted", 0),
                    m.get("areas_completed", 0),
                    m.get("properties_found", 0),
                    m.get("pages_fetched", 0),
                    m.get("pages_failed", 0),
                    m.get("parse_errors", 0),
                    m.get("is_healthy", True),
                    m.get("error_message"),
                ),
            )
        await conn.commit()
        logger.debug("scraper_runs_saved", count=len(metrics_list))

    # ------------------------------------------------------------------
    # Floorplan-dropped properties (prevent re-enrichment)
    # ------------------------------------------------------------------

    async def save_dropped_properties(
        self,
        dropped: list[MergedProperty],
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> None:
        """Batch save properties dropped by the floorplan gate.

        Saves with notification_status='dropped' and enrichment_status='enriched'
        so they are marked as "seen" by filter_new_merged() and won't be
        re-enriched on future pipeline runs.

        Uses INSERT ... ON CONFLICT DO NOTHING — if somehow already in DB,
        don't overwrite existing data.

        Args:
            dropped: Properties dropped at the floorplan gate.
            commute_lookup: Commute data keyed by unique_id.
        """
        if not dropped:
            return

        conn = await self._get_connection()
        for merged in dropped:
            prop = merged.canonical
            commute_info = commute_lookup.get(prop.unique_id)
            commute_minutes = commute_info[0] if commute_info else None
            transport_mode = commute_info[1] if commute_info else None

            columns, values = build_merged_insert_columns(
                merged,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
                notification_status=NotificationStatus.DROPPED,
            )
            col_list = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)

            await conn.execute(
                f"""
                INSERT INTO properties ({col_list}, enrichment_status)
                VALUES ({placeholders}, 'enriched')
                ON CONFLICT(unique_id) DO NOTHING
                """,
                values,
            )

            # Save images (important for cross-run dedup image matching)
            images = list(merged.images)
            if merged.floorplan:
                images.append(merged.floorplan)
            if images:
                img_rows = [
                    (prop.unique_id, img.source.value, str(img.url), img.image_type)
                    for img in images
                ]
                await conn.executemany(
                    """
                    INSERT OR IGNORE INTO property_images
                    (property_unique_id, source, url, image_type)
                    VALUES (?, ?, ?, ?)
                    """,
                    img_rows,
                )

        await conn.commit()
        logger.info("dropped_properties_saved", count=len(dropped))

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

            columns, values = build_merged_insert_columns(
                merged,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
                notification_status=NotificationStatus.PENDING_ANALYSIS,
            )
            col_list = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)

            await conn.execute(
                f"""
                INSERT INTO properties ({col_list}, enrichment_status)
                VALUES ({placeholders}, 'enriched')
                ON CONFLICT(unique_id) DO UPDATE SET
                    notification_status = excluded.notification_status,
                    enrichment_status = 'enriched',
                    commute_minutes = COALESCE(excluded.commute_minutes, commute_minutes),
                    transport_mode = COALESCE(excluded.transport_mode, transport_mode),
                    latitude = COALESCE(excluded.latitude, latitude),
                    longitude = COALESCE(excluded.longitude, longitude),
                    postcode = COALESCE(excluded.postcode, postcode),
                    sources = excluded.sources,
                    source_urls = excluded.source_urls,
                    min_price = excluded.min_price,
                    max_price = excluded.max_price,
                    descriptions_json = COALESCE(excluded.descriptions_json, descriptions_json)
                """,
                values,
            )

            # Save images
            images = list(merged.images)
            if merged.floorplan:
                images.append(merged.floorplan)
            if images:
                img_rows = [
                    (prop.unique_id, img.source.value, str(img.url), img.image_type)
                    for img in images
                ]
                await conn.executemany(
                    """
                    INSERT OR IGNORE INTO property_images
                    (property_unique_id, source, url, image_type)
                    VALUES (?, ?, ?, ?)
                    """,
                    img_rows,
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

        results = [
            await row_to_merged_property(row, get_property_images=self._get_property_images)
            for row in rows
        ]

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

        Saves quality data (if any), increments ``analysis_attempts``, and
        sets notification_status to 'pending'.  Only transitions properties
        that are currently 'pending_analysis'.

        Uses a single commit for both the quality save and the status update
        to prevent inconsistent state on crash.

        Args:
            unique_id: Property unique ID.
            quality_analysis: Analysis result, or None if analysis was skipped.
        """
        async with self._transaction() as conn:
            if quality_analysis:
                await self._save_quality_analysis(unique_id, quality_analysis, _commit=False)

            await conn.execute(
                """
                UPDATE properties
                SET notification_status = ?,
                    analysis_attempts = COALESCE(analysis_attempts, 0) + 1
                WHERE unique_id = ?
                  AND notification_status = ?
                """,
                (
                    NotificationStatus.PENDING.value,
                    unique_id,
                    NotificationStatus.PENDING_ANALYSIS.value,
                ),
            )
        logger.debug("analysis_completed", unique_id=unique_id)

    async def reset_failed_analyses(self, *, max_analysis_attempts: int = 3) -> int:
        """Reset properties with fallback analysis for re-analysis.

        Finds properties where quality analysis ran but produced only the
        minimal fallback (overall_rating IS NULL), indicating the API failed.
        Deletes the fallback quality data and transitions them back to
        'pending_analysis' so the next pipeline run re-analyzes them.

        Properties that have already been retried ``max_analysis_attempts``
        times are skipped and logged as permanently failed.

        Args:
            max_analysis_attempts: Maximum number of analysis retries before
                giving up on a property.

        Returns:
            Number of properties reset.
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT p.unique_id, p.analysis_attempts FROM properties p
            JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE q.overall_rating IS NULL
              AND p.notification_status != ?
            """,
            (NotificationStatus.PENDING_ANALYSIS.value,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        retryable = [
            row["unique_id"]
            for row in rows
            if (row["analysis_attempts"] or 0) < max_analysis_attempts
        ]
        exhausted = [
            row["unique_id"]
            for row in rows
            if (row["analysis_attempts"] or 0) >= max_analysis_attempts
        ]

        if exhausted:
            logger.warning(
                "analysis_retries_exhausted",
                count=len(exhausted),
                unique_ids=exhausted,
                max_attempts=max_analysis_attempts,
            )

        if not retryable:
            return 0

        ids = retryable
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

        results = [
            await row_to_merged_property(row, get_property_images=self._get_property_images)
            for row in rows
        ]

        logger.info("loaded_reanalysis_queue", count=len(results))
        return results

    # ------------------------------------------------------------------
    # Property events (T4: audit trail)
    # ------------------------------------------------------------------

    async def insert_property_events(self, run_id: int, events: list[PropertyEvent]) -> None:
        """Bulk-insert property events for a pipeline run.

        Args:
            run_id: The pipeline run ID.
            events: List of PropertyEvent objects to persist.
        """
        if not events:
            return
        conn = await self._get_connection()
        rows = [
            (
                run_id,
                e.property_id,
                e.source,
                e.event_type,
                e.stage,
                json.dumps(e.metadata) if e.metadata else None,
            )
            for e in events
        ]
        await conn.executemany(
            """
            INSERT INTO property_events
                (run_id, property_id, source, event_type, stage, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()
        logger.debug("property_events_inserted", count=len(events), run_id=run_id)

    async def cleanup_old_events(self, keep_runs: int = 30) -> int:
        """Delete property events from runs older than the last N.

        Args:
            keep_runs: Number of most recent pipeline runs to keep events for.

        Returns:
            Number of rows deleted.
        """
        conn = await self._get_connection()
        cursor = await conn.execute(
            """
            DELETE FROM property_events
            WHERE run_id NOT IN (
                SELECT id FROM pipeline_runs ORDER BY id DESC LIMIT ?
            )
            """,
            (keep_runs,),
        )
        await conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("old_property_events_cleaned", deleted=deleted, keep_runs=keep_runs)
        return deleted

    # ------------------------------------------------------------------
    # Re-analysis support
    # ------------------------------------------------------------------

    async def complete_reanalysis(
        self,
        unique_id: str,
        analysis: PropertyQualityAnalysis,
    ) -> None:
        """Save updated quality analysis and clear the re-analysis flag.

        Does NOT touch notification_status — property stays 'sent'.
        Uses a single commit for both the quality save and the flag clear
        to prevent inconsistent state on crash.

        Args:
            unique_id: Property unique ID.
            analysis: New quality analysis result.
        """
        async with self._transaction() as conn:
            await self._save_quality_analysis(unique_id, analysis, _commit=False)

            await conn.execute(
                """
                UPDATE quality_analyses
                SET reanalysis_requested_at = NULL
                WHERE property_unique_id = ?
                """,
                (unique_id,),
            )
        logger.debug("reanalysis_completed", unique_id=unique_id)
