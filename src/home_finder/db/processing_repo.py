"""Atomic property preparation, assessment, and recovery transitions."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal

import aiosqlite

from home_finder.db.delivery import enqueue_discovery
from home_finder.db.enrichment_recovery import request_enrichment_recovery
from home_finder.db.image_ops import upsert_property_images
from home_finder.db.listing_text import persist_merged_text
from home_finder.db.merge_safety import MergeBlockedError, ensure_canonical_source_owner
from home_finder.db.row_mappers import (
    ImageReader,
    build_merged_insert_columns,
    row_to_merged_property,
)
from home_finder.db.source_listing_ops import link_source_listings_by_url
from home_finder.logging import get_logger
from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    PropertyQualityAnalysis,
    TransportMode,
)
from home_finder.models.notifications import NotificationPolicy

logger = get_logger(__name__)

# Discovery is queued before enrichment, so both outcomes must promote the
# complete projection on conflict. Missing observations retain prior evidence;
# owner state, delivery history and accumulated attempts are separate fields.
_ENRICHED_PROJECTION_UPDATES = """
    commute_minutes = COALESCE(excluded.commute_minutes, commute_minutes),
    transport_mode = COALESCE(excluded.transport_mode, transport_mode),
    latitude = COALESCE(excluded.latitude, latitude),
    longitude = COALESCE(excluded.longitude, longitude),
    postcode = COALESCE(excluded.postcode, postcode),
    available_from = COALESCE(excluded.available_from, available_from),
    sources = excluded.sources,
    source_urls = excluded.source_urls,
    min_price = excluded.min_price,
    max_price = excluded.max_price,
    floor_area_sqm = COALESCE(excluded.floor_area_sqm, floor_area_sqm),
    floor_area_source = CASE WHEN excluded.floor_area_sqm IS NOT NULL
        THEN excluded.floor_area_source
        ELSE floor_area_source END
"""


@dataclass
class PersistenceResult:
    """Only accepted groups may advance; each refusal leaves its group untouched."""

    accepted: list[MergedProperty] = field(default_factory=list)
    refused: dict[str, dict[str, list[str]]] = field(default_factory=dict)


class ProcessingRepository:
    """Persist prepared evidence and transition enrichment/assessment work."""

    def __init__(
        self,
        read_connection: Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]],
        get_property_images: ImageReader,
        save_quality_analysis: Callable[..., Coroutine[Any, Any, None]],
        transaction: Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]],
    ) -> None:
        self._read_connection = read_connection
        self._get_property_images = get_property_images
        self._save_quality_analysis = save_quality_analysis
        self._transaction = transaction

    # ------------------------------------------------------------------
    # Floorplan-dropped properties (prevent re-enrichment)
    # ------------------------------------------------------------------

    async def save_dropped_properties(
        self,
        dropped: list[MergedProperty],
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> PersistenceResult:
        """Persist independent groups atomically, retaining structured merge refusals.

        A refused group cannot roll back another home or advance to analysis.
        Unexpected storage errors still propagate; committed groups are recoverable.
        """
        result = PersistenceResult()
        for merged in dropped:
            try:
                await self._save_dropped_property(merged, commute_lookup)
            except MergeBlockedError as exc:
                result.refused[merged.unique_id] = exc.blockers
                logger.warning(
                    "pipeline_group_refused",
                    property_id=merged.unique_id,
                    operation="save_dropped_properties",
                    blockers=exc.blockers,
                )
            else:
                result.accepted.append(merged)
        logger.info(
            "dropped_properties_saved", count=len(result.accepted), refused=len(result.refused)
        )
        return result

    async def _save_dropped_property(
        self,
        merged: MergedProperty,
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> None:
        async with self._transaction() as conn:
            prop = merged.canonical
            await ensure_canonical_source_owner(conn, prop.unique_id)
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
                ON CONFLICT(unique_id) DO UPDATE SET
                    notification_status = 'dropped',
                    enrichment_status = 'enriched',
                    enrichment_recovery_requested_at = NULL,
                    {_ENRICHED_PROJECTION_UPDATES}
                WHERE properties.notification_status = 'pending_enrichment'
                  AND properties.enrichment_status = 'pending'
                """,
                values,
            )

            # Keep source_listings in sync
            now = datetime.now(UTC).isoformat()
            await conn.execute(
                """
                INSERT INTO source_listings (
                    unique_id, source, source_id, url, title, price_pcm,
                    bedrooms, address, postcode, latitude, longitude,
                    description, image_url, available_from, first_seen,
                    last_seen, merged_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unique_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
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
                    prop.unique_id,
                ),
            )

            # Link non-canonical source_listings by URL (in-run dedup absorbed sources)
            await link_source_listings_by_url(
                conn, prop.unique_id, merged.source_urls, absorb_pending=True
            )
            await persist_merged_text(conn, merged)

            # Save images (important for cross-run dedup image matching)
            images = list(merged.images)
            if merged.floorplan:
                images.append(merged.floorplan)
            await upsert_property_images(conn, prop.unique_id, images)

    # ------------------------------------------------------------------
    # Enrichment retry
    # ------------------------------------------------------------------

    async def request_enrichment_recovery(
        self, property_ids: list[str], *, reason: str
    ) -> dict[str, str]:
        """Queue selected retained homes for one bounded recovery, preserving all counters."""
        async with self._transaction() as conn:
            return await request_enrichment_recovery(conn, property_ids, reason=reason)

    async def save_unenriched_property(
        self,
        merged: MergedProperty,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
        attempted: bool = True,
    ) -> None:
        """Retain a pending candidate or record an unsuccessful enrichment attempt.

        attempted=False queues discovery without consuming an attempt. Otherwise
        the cumulative counter advances; a consumed recovery grant is cleared
        and the recovery is marked failed. Existing evidence and owner decisions
        remain intact. Repeated queueing never resets attempt counts.
        """
        async with self._transaction() as conn:
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
                VALUES ({placeholders}, 'pending', ?)
                ON CONFLICT(unique_id) DO UPDATE SET
                    enrichment_attempts = enrichment_attempts + excluded.enrichment_attempts,
                    enrichment_status = CASE
                        WHEN enrichment_recovery_requested_at IS NOT NULL
                             AND excluded.enrichment_attempts > 0 THEN 'failed'
                        ELSE enrichment_status END,
                    enrichment_recovery_requested_at = CASE
                        WHEN excluded.enrichment_attempts > 0 THEN NULL
                        ELSE enrichment_recovery_requested_at END
                WHERE properties.enrichment_status = 'pending'
            """,
                [*values, int(attempted)],
            )
            # Keep source_listings in sync
            prop = merged.canonical
            now = datetime.now(UTC).isoformat()
            await conn.execute(
                """
                INSERT INTO source_listings (
                    unique_id, source, source_id, url, title, price_pcm,
                    bedrooms, address, postcode, latitude, longitude,
                    description, image_url, available_from, first_seen,
                    last_seen, merged_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unique_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
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
                    prop.unique_id,
                ),
            )
            # Link non-canonical source_listings by URL (in-run dedup absorbed sources)
            await link_source_listings_by_url(conn, prop.unique_id, merged.source_urls)
            await persist_merged_text(conn, merged)
        logger.debug("unenriched_property_saved", unique_id=merged.canonical.unique_id)

    async def get_unenriched_properties(self, max_attempts: int = 3) -> list[MergedProperty]:
        """Load properties that failed enrichment for retry.

        Args:
            max_attempts: Only return properties with fewer attempts than this.

        Returns:
            List of MergedProperty objects needing enrichment.
        """
        async with self._read_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM properties
                WHERE enrichment_status = 'pending'
                  AND (enrichment_attempts < ? OR enrichment_recovery_requested_at IS NOT NULL)
                ORDER BY first_seen ASC, unique_id ASC
                """,
                (max_attempts,),
            )
            rows = await cursor.fetchall()

            results = [await row_to_merged_property(row, load_images=False) for row in rows]

            logger.info("loaded_unenriched_properties", count=len(results))
            return results

    async def get_unenriched_retry_stats(self, max_attempts: int = 3) -> dict[str, dict[str, int]]:
        """Return retry backlog stats grouped by source and attempt count.

        Returns:
            Dict with 'by_attempts' mapping attempt count to property count.
        """
        async with self._read_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT enrichment_attempts, COUNT(*) as cnt
                FROM properties
                WHERE enrichment_status = 'pending'
                  AND (enrichment_attempts < ? OR enrichment_recovery_requested_at IS NOT NULL)
                GROUP BY enrichment_attempts
                """,
                (max_attempts,),
            )
            rows = await cursor.fetchall()
            by_attempts = {str(row["enrichment_attempts"]): row["cnt"] for row in rows}
            return {"by_attempts": by_attempts}

    async def mark_enriched(
        self,
        unique_id: str,
        *,
        notification_policy: NotificationPolicy | None = None,
    ) -> None:
        """Transition a re-enriched property into the normal notification flow.

        Sets enrichment_status='enriched' and notification_status='pending'
        only for rows that still have notification_status='pending_enrichment'.
        No-op for genuinely new properties (already 'pending').
        """
        async with self._transaction() as conn:
            transition = await conn.execute(
                """
                UPDATE properties
                SET enrichment_status = 'enriched',
                    enrichment_recovery_requested_at = NULL,
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
            if transition.rowcount:
                await enqueue_discovery(conn, unique_id, policy=notification_policy)

    async def expire_unenriched(self, max_attempts: int = 3) -> int:
        """Give up on properties that exceeded max enrichment retries.

        Args:
            max_attempts: Threshold for giving up.

        Returns:
            Number of properties expired.
        """
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                UPDATE properties
                SET enrichment_status = 'failed'
                WHERE enrichment_status = 'pending'
                  AND enrichment_attempts >= ?
                  AND enrichment_recovery_requested_at IS NULL
                """,
                (max_attempts,),
            )
        count = cursor.rowcount
        if count:
            logger.info("expired_unenriched_properties", count=count)
        return count

    # ------------------------------------------------------------------
    # Quality analysis retry (save-before-analyze pattern)
    # ------------------------------------------------------------------

    async def save_pre_analysis_properties(
        self,
        merged_list: list[MergedProperty],
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> PersistenceResult:
        """Persist independent groups atomically, retaining structured merge refusals.

        A refused group cannot roll back another home or advance to analysis.
        Unexpected storage errors still propagate; committed groups are recoverable.
        """
        result = PersistenceResult()
        for merged in merged_list:
            try:
                await self._save_pre_analysis_property(merged, commute_lookup)
            except MergeBlockedError as exc:
                result.refused[merged.unique_id] = exc.blockers
                logger.warning(
                    "pipeline_group_refused",
                    property_id=merged.unique_id,
                    operation="save_pre_analysis_properties",
                    blockers=exc.blockers,
                )
            else:
                result.accepted.append(merged)
        logger.info(
            "pre_analysis_properties_saved", count=len(result.accepted), refused=len(result.refused)
        )
        return result

    async def _save_pre_analysis_property(
        self,
        merged: MergedProperty,
        commute_lookup: dict[str, tuple[int, TransportMode]],
    ) -> None:
        async with self._transaction() as conn:
            prop = merged.canonical
            await ensure_canonical_source_owner(conn, prop.unique_id)
            cursor = await conn.execute(
                "SELECT notification_status, analysis_exhausted_reason, analysis_attempts "
                "FROM properties WHERE unique_id = ?",
                (prop.unique_id,),
            )
            existing = await cursor.fetchone()
            if existing is not None and (existing[1] or (existing[2] or 0) >= 3):
                raise MergeBlockedError({prop.unique_id: ["analysis_attempts_exhausted"]})
            if existing is not None and existing[0] not in {
                "pending",
                "pending_analysis",
                "pending_enrichment",
            }:
                raise MergeBlockedError({prop.unique_id: ["processing_state_requires_recovery"]})
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
                    enrichment_recovery_requested_at = NULL,
                    {_ENRICHED_PROJECTION_UPDATES}
                """,
                values,
            )

            # Keep source_listings in sync
            now = datetime.now(UTC).isoformat()
            await conn.execute(
                """
                INSERT INTO source_listings (
                    unique_id, source, source_id, url, title, price_pcm,
                    bedrooms, address, postcode, latitude, longitude,
                    description, image_url, available_from, first_seen,
                    last_seen, merged_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unique_id) DO UPDATE SET
                    price_pcm = excluded.price_pcm,
                    title = excluded.title,
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
                    prop.unique_id,
                ),
            )

            # Link non-canonical source_listings by URL (in-run dedup absorbed sources)
            await link_source_listings_by_url(
                conn, prop.unique_id, merged.source_urls, absorb_pending=True
            )
            await persist_merged_text(conn, merged)

            # Save images
            images = list(merged.images)
            if merged.floorplan:
                images.append(merged.floorplan)
            await upsert_property_images(conn, prop.unique_id, images)

    async def get_pending_analysis_properties(
        self,
        *,
        exclude_ids: set[str] | None = None,
    ) -> list[MergedProperty]:
        """Load pending initial assessments, including crash recovery and normal deferral.

        Args:
            exclude_ids: Property IDs to exclude (e.g. current batch just saved).

        Returns:
            List of MergedProperty objects with notification_status='pending_analysis'.
        """
        async with self._read_connection() as conn:
            query = """
                SELECT * FROM properties
                WHERE notification_status = ?
                  AND COALESCE(analysis_attempts, 0) < 3
                  AND analysis_exhausted_reason IS NULL
                ORDER BY first_seen ASC
            """
            params: list[Any] = [NotificationStatus.PENDING_ANALYSIS.value]

            if exclude_ids:
                placeholders = ",".join("?" * len(exclude_ids))
                query = f"""
                    SELECT * FROM properties
                    WHERE notification_status = ?
                      AND COALESCE(analysis_attempts, 0) < 3
                      AND analysis_exhausted_reason IS NULL
                      AND unique_id NOT IN ({placeholders})
                    ORDER BY first_seen ASC
                """
                params.extend(exclude_ids)

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()

            results = [
                await row_to_merged_property(
                    row, get_property_images=partial(self._get_property_images, _conn=conn)
                )
                for row in rows
            ]

            if results:
                logger.info(
                    "loaded_pending_analysis_retries_from_db",
                    count=len(results),
                )
            return results

    async def record_analysis_failure(self, unique_id: str) -> bool:
        """Bound failed initial-analysis retries without making delivery eligible."""
        async with self._transaction() as conn:
            await conn.execute(
                """UPDATE properties
                   SET analysis_attempts = COALESCE(analysis_attempts, 0) + 1,
                       analysis_exhausted_reason = CASE
                           WHEN COALESCE(analysis_attempts, 0) + 1 >= 3
                           THEN COALESCE(analysis_exhausted_reason, 'initial_failure_limit')
                           ELSE analysis_exhausted_reason END,
                       analysis_exhausted_at = CASE
                           WHEN COALESCE(analysis_attempts, 0) + 1 >= 3
                           THEN COALESCE(analysis_exhausted_at, ?)
                           ELSE analysis_exhausted_at END
                   WHERE unique_id = ? AND notification_status = 'pending_analysis'""",
                (datetime.now(UTC).isoformat(), unique_id),
            )
            cursor = await conn.execute(
                "SELECT analysis_exhausted_reason FROM properties WHERE unique_id=?", (unique_id,)
            )
            row = await cursor.fetchone()
            return row is not None and row[0] is not None

    async def mark_analysis_exhausted(
        self,
        unique_id: str,
        reason: Literal["initial_failure_limit", "paid_operation_attempt_limit"],
    ) -> None:
        """Retain a terminal processing reason, never resetting money or delivery state."""
        async with self._transaction() as conn:
            await conn.execute(
                """UPDATE properties
                   SET analysis_exhausted_reason = COALESCE(analysis_exhausted_reason, ?),
                       analysis_exhausted_at = COALESCE(analysis_exhausted_at, ?)
                   WHERE unique_id = ?""",
                (reason, datetime.now(UTC).isoformat(), unique_id),
            )

    async def complete_analysis(
        self,
        unique_id: str,
        quality_analysis: PropertyQualityAnalysis | None,
        *,
        notification_policy: NotificationPolicy | None = None,
    ) -> None:
        """Save the assessment and a single durable notification decision atomically.

        Saves quality data (if any), increments ``analysis_attempts``, and
        sets notification_status to 'pending' or 'dashboard_only'. Transitions an
        initial assessment or a re-enriched home awaiting its assessment. The
        latter retains its cumulative initial-analysis counter.

        Uses a single commit for both the quality save and the status update
        to prevent inconsistent state on crash.

        Args:
            unique_id: Property unique ID.
            quality_analysis: Analysis result, or None if analysis was skipped.
        """
        async with self._transaction() as conn:
            if quality_analysis:
                await self._save_quality_analysis(unique_id, quality_analysis, _conn=conn)

            transition = await conn.execute(
                """
                UPDATE properties
                SET notification_status = ?,
                    analysis_attempts = COALESCE(analysis_attempts, 0)
                        + CASE WHEN notification_status='pending_analysis' THEN 1 ELSE 0 END,
                    enrichment_status = 'enriched',
                    enrichment_recovery_requested_at = NULL
                WHERE unique_id = ?
                  AND notification_status IN (?, ?)
                """,
                (
                    NotificationStatus.PENDING.value,
                    unique_id,
                    NotificationStatus.PENDING_ANALYSIS.value,
                    NotificationStatus.PENDING_ENRICHMENT.value,
                ),
            )
            if transition.rowcount:
                await enqueue_discovery(conn, unique_id, policy=notification_policy)
        logger.debug("analysis_completed", unique_id=unique_id)

    async def reset_failed_analyses(self, *, max_analysis_attempts: int = 3) -> int:
        """Reset properties with fallback analysis for re-analysis.

        Finds properties where quality analysis ran but produced only the
        minimal fallback (overall_rating IS NULL), indicating the API failed.
        Undelivered items return to 'pending_analysis'. Delivered fallbacks
        keep their data and notification status and enter the reanalysis queue.

        Properties that have already been retried ``max_analysis_attempts``
        times are skipped and logged as permanently failed.

        Args:
            max_analysis_attempts: Maximum number of analysis retries before
                giving up on a property.

        Returns:
            Number of properties reset.
        """
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT p.unique_id, p.analysis_attempts, p.notification_status FROM properties p
                JOIN quality_analyses q ON p.unique_id = q.property_unique_id
                WHERE q.overall_rating IS NULL
                  AND p.notification_status != ?
                  AND p.analysis_exhausted_reason IS NULL
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
                await conn.executemany(
                    """UPDATE properties SET analysis_exhausted_reason='initial_failure_limit',
                           analysis_exhausted_at=COALESCE(analysis_exhausted_at, ?)
                       WHERE unique_id=?""",
                    [(datetime.now(UTC).isoformat(), uid) for uid in exhausted],
                )
                logger.warning(
                    "analysis_retries_exhausted",
                    count=len(exhausted),
                    unique_ids=exhausted,
                    max_attempts=max_analysis_attempts,
                )

            if not retryable:
                return 0

            # Recover already-delivered fallbacks without creating another alert.
            sent_ids = [
                row["unique_id"]
                for row in rows
                if row["unique_id"] in retryable and row["notification_status"] == "sent"
            ]
            if sent_ids:
                await conn.executemany(
                    """UPDATE quality_analyses
                       SET reanalysis_requested_at = COALESCE(reanalysis_requested_at, ?)
                       WHERE property_unique_id = ?""",
                    [(datetime.now(UTC).isoformat(), uid) for uid in sent_ids],
                )
            ids = [uid for uid in retryable if uid not in sent_ids]
            if not ids:
                return len(sent_ids)
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

        logger.info("reset_failed_analyses", count=len(ids), unique_ids=ids)
        return len(retryable)

    # ------------------------------------------------------------------
    # Re-analysis support
    # ------------------------------------------------------------------

    async def request_reanalysis(self, unique_ids: list[str]) -> int:
        """Mark specific properties for re-analysis.

        Sets reanalysis_requested_at on matching quality_analyses rows.
        Repeated requests coalesce into the first pending request.

        Args:
            unique_ids: Property unique IDs to flag for re-analysis.

        Returns:
            Number of rows updated.
        """
        if not unique_ids:
            return 0
        async with self._transaction() as conn:
            now = datetime.now(UTC).isoformat()
            total = 0
            chunk_size = 500
            for i in range(0, len(unique_ids), chunk_size):
                chunk = unique_ids[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cursor = await conn.execute(
                    f"""
                    UPDATE quality_analyses
                    SET reanalysis_requested_at = COALESCE(reanalysis_requested_at, ?)
                    WHERE property_unique_id IN ({placeholders})
                    """,
                    [now, *chunk],
                )
                total += cursor.rowcount
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
        async with self._transaction() as conn:
            now = datetime.now(UTC).isoformat()

            if all_properties:
                cursor = await conn.execute(
                    """
                    UPDATE quality_analyses
                    SET reanalysis_requested_at = COALESCE(reanalysis_requested_at, ?)
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
                    params.append(f"{outcode.upper()} %")
                or_clause = " OR ".join(conditions)
                cursor = await conn.execute(
                    f"""
                    UPDATE quality_analyses
                    SET reanalysis_requested_at = COALESCE(reanalysis_requested_at, ?)
                    WHERE property_unique_id IN (
                        SELECT p.unique_id FROM properties p
                        WHERE {or_clause}
                    )
                    """,
                    params,
                )

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
        async with self._read_connection() as conn:
            if outcode:
                query = """
                    SELECT p.* FROM properties p
                    JOIN quality_analyses q ON p.unique_id = q.property_unique_id
                    WHERE q.reanalysis_requested_at IS NOT NULL
                      AND p.analysis_exhausted_reason IS NULL
                      AND UPPER(p.postcode) LIKE ?
                    ORDER BY p.first_seen ASC
                """
                params: list[str] = [f"{outcode.upper()} %"]
                cursor = await conn.execute(query, params)
            else:
                cursor = await conn.execute("""
                    SELECT p.* FROM properties p
                    JOIN quality_analyses q ON p.unique_id = q.property_unique_id
                    WHERE q.reanalysis_requested_at IS NOT NULL
                      AND p.analysis_exhausted_reason IS NULL
                    ORDER BY p.first_seen ASC
                """)

            rows = await cursor.fetchall()

            results = [
                await row_to_merged_property(
                    row, get_property_images=partial(self._get_property_images, _conn=conn)
                )
                for row in rows
            ]

            logger.info("loaded_reanalysis_queue", count=len(results))
            return results

    # ------------------------------------------------------------------
    # Re-analysis support
    # ------------------------------------------------------------------

    async def complete_reanalysis(
        self,
        unique_id: str,
        analysis: PropertyQualityAnalysis,
    ) -> None:
        """Save updated quality analysis and clear the re-analysis flag.

        Preserves the existing notification status.
        Uses a single commit for both the quality save and the flag clear
        to prevent inconsistent state on crash.

        Args:
            unique_id: Property unique ID.
            analysis: New quality analysis result.
        """
        async with self._transaction() as conn:
            await self._save_quality_analysis(unique_id, analysis, _conn=conn)

            await conn.execute(
                """
                UPDATE quality_analyses
                SET reanalysis_requested_at = NULL
                WHERE property_unique_id = ?
                """,
                (unique_id,),
            )
        logger.debug("reanalysis_completed", unique_id=unique_id)
