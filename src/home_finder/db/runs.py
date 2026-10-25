"""Run accounting, scraper metrics, and buffered diagnostic events.

Processing transitions belong to ProcessingRepository; this repository writes
observability only, using the same owned connection/transaction boundaries.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from home_finder.logging import get_logger

if TYPE_CHECKING:
    from home_finder.pipeline.event_recorder import PropertyEvent

logger = get_logger(__name__)


class RunRepository:
    """Persist run/event telemetry independently of property processing state."""

    def __init__(
        self,
        read_connection: Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]],
        transaction: Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]],
    ) -> None:
        self._read_connection = read_connection
        self._transaction = transaction

    async def create_pipeline_run(self) -> int:
        """Create a new pipeline run record.

        Returns:
            The ID of the new run.
        """
        async with self._transaction() as conn:
            cursor = await conn.execute(
                "INSERT INTO pipeline_runs (started_at, status) VALUES (?, 'running')",
                (datetime.now(UTC).isoformat(),),
            )
        return cursor.lastrowid  # type: ignore[return-value]

    async def update_pipeline_run(self, run_id: int, **metrics: int | float) -> None:
        """Update metrics on a pipeline run.

        Args:
            run_id: The pipeline run ID.
            **metrics: Column name/value pairs to update (e.g. scraped_count=42,
                scraping_seconds=12.3).
        """
        if not metrics:
            return
        async with self._transaction() as conn:
            set_clauses = ", ".join(f"{k} = ?" for k in metrics)
            values = list(metrics.values())
            values.append(run_id)
            await conn.execute(
                f"UPDATE pipeline_runs SET {set_clauses} WHERE id = ?",
                values,
            )

    async def complete_pipeline_run(
        self,
        run_id: int,
        status: str,
        *,
        error_message: str | None = None,
    ) -> None:
        """Finalize a pipeline run with its terminal status.

        Args:
            run_id: The pipeline run ID.
            status: Terminal status (completed, partial, failed or cancelled).
            error_message: Error message if status is 'failed'.
        """
        async with self._transaction() as conn:
            now = datetime.now(UTC).isoformat()
            # Calculate duration from started_at
            cursor = await conn.execute(
                "SELECT started_at FROM pipeline_runs WHERE id = ?", (run_id,)
            )
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

    async def get_last_pipeline_run(self) -> dict[str, Any] | None:
        """Get the most recent terminal pipeline run, including partial/cancelled.

        Returns:
            Dict with run data, or None if no runs exist.
        """
        async with self._read_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM pipeline_runs
                WHERE status IN ('completed', 'partial', 'failed', 'cancelled')
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
            pipeline_run_id: The pipeline run ID, or None for unattributed metrics.
            metrics_list: List of dicts with scraper metric fields.
        """
        if not metrics_list:
            return
        async with self._transaction() as conn:
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
        logger.debug("scraper_runs_saved", count=len(metrics_list))

    async def insert_property_events(self, run_id: int, events: list[PropertyEvent]) -> None:
        """Bulk-insert property events for a pipeline run.

        Args:
            run_id: The pipeline run ID.
            events: List of PropertyEvent objects to persist.
        """
        if not events:
            return
        async with self._transaction() as conn:
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
        logger.debug("property_events_inserted", count=len(events), run_id=run_id)

    async def cleanup_old_events(self, keep_runs: int = 30) -> int:
        """Delete property events from runs older than the last N.

        Args:
            keep_runs: Number of most recent pipeline runs to keep events for.

        Returns:
            Number of rows deleted.
        """
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM property_events
                WHERE run_id NOT IN (
                    SELECT id FROM pipeline_runs ORDER BY id DESC LIMIT ?
                )
                """,
                (keep_runs,),
            )
        deleted = cursor.rowcount
        if deleted:
            logger.info("old_property_events_cleaned", deleted=deleted, keep_runs=keep_runs)
        return deleted
