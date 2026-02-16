"""Pipeline repository — methods for pipeline runs, analysis retry, and reanalysis."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

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
        save_quality_analysis: Callable[
            [str, PropertyQualityAnalysis], Coroutine[Any, Any, None]
        ],
    ) -> None:
        self._get_connection = get_connection
        self._get_property_images = get_property_images
        self._save_quality_analysis = save_quality_analysis

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
                    notification_status = excluded.notification_status
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
            await row_to_merged_property(
                row, get_property_images=self._get_property_images
            )
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

        Saves quality data (if any) and sets notification_status to 'pending'.
        Only transitions properties that are currently 'pending_analysis'.

        Args:
            unique_id: Property unique ID.
            quality_analysis: Analysis result, or None if analysis was skipped.
        """
        if quality_analysis:
            await self._save_quality_analysis(unique_id, quality_analysis)

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

        results = [
            await row_to_merged_property(
                row, get_property_images=self._get_property_images
            )
            for row in rows
        ]

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
        await self._save_quality_analysis(unique_id, analysis)

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
