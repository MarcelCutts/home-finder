"""Immutable normalized observations and replay, independent of mutable listings."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from home_finder.models.observations import (
    MAX_BATCH_BYTES,
    SourceObservation,
    SourceProjection,
    canonical_json,
    observation_field_rows,
    project_field_rows,
    sha256_text,
)

ConnectionScope = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]


class ObservationConflict(ValueError):
    """An idempotency identity was reused for different normalized evidence."""


@dataclass(frozen=True)
class ObservationReceipt:
    inserted: int
    replayed: int
    observation_ids: tuple[str, ...]


async def create_observation_schema(conn: aiosqlite.Connection) -> None:
    """Add evidence tables and freeze pre-existing projections without rewriting them."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_observation_baselines (
            source_unique_id TEXT PRIMARY KEY,
            classification TEXT NOT NULL CHECK (classification = 'historical_projection'),
            recorded_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
            snapshot_sha256 TEXT NOT NULL,
            description_state TEXT NOT NULL CHECK (
                description_state IN ('retained', 'unavailable')
            ),
            is_backfilled INTEGER NOT NULL CHECK (is_backfilled IN (0, 1))
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_observations (
            observation_id TEXT PRIMARY KEY CHECK (length(observation_id) = 64),
            producer_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            source_unique_id TEXT NOT NULL,
            origin TEXT NOT NULL CHECK (origin IN ('search', 'detail', 'correction')),
            observed_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (
                json_valid(payload_json) AND length(CAST(payload_json AS BLOB)) <= 250000
            ),
            UNIQUE(producer_id, event_id)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_observations_history
        ON source_observations(source_unique_id, observation_id)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_observation_fields (
            observation_id TEXT NOT NULL REFERENCES source_observations(observation_id),
            source_unique_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            authority INTEGER NOT NULL CHECK (authority IN (1, 2)),
            observed_at TEXT NOT NULL,
            field_state TEXT NOT NULL CHECK (field_state IN ('observed', 'cleared')),
            value_json TEXT NOT NULL CHECK (json_valid(value_json)),
            PRIMARY KEY(observation_id, field_name),
            CHECK ((field_state = 'cleared') = (value_json = 'null'))
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_observation_fields_current
        ON source_observation_fields(source_unique_id, field_name, authority DESC, observed_at DESC)
    """)
    # Baselines contain source-projection columns only, never quality answers,
    # user statuses or notes. Historical first_seen/last_seen remain original
    # row values, not newly certified source observation timestamps.
    columns = (
        "unique_id",
        "source",
        "source_id",
        "url",
        "title",
        "price_pcm",
        "bedrooms",
        "address",
        "postcode",
        "latitude",
        "longitude",
        "description",
        "image_url",
        "available_from",
        "first_seen",
        "last_seen",
        "merged_id",
        "is_backfilled",
        "created_at",
        "is_off_market",
        "off_market_since",
        "off_market_reason",
        "last_checked_at",
        "text_json",
    )
    recorded_at = datetime.now(UTC).isoformat(timespec="microseconds")
    async with conn.execute(f"SELECT {', '.join(columns)} FROM source_listings") as cursor:
        while batch := await cursor.fetchmany(100):
            baselines = []
            for row in batch:
                snapshot = dict(zip(columns, row, strict=True))
                body = canonical_json(snapshot)
                description = snapshot["description"]
                baselines.append(
                    (
                        snapshot["unique_id"],
                        recorded_at,
                        body,
                        sha256_text(body),
                        "retained" if description and description.strip() else "unavailable",
                        snapshot["is_backfilled"],
                    )
                )
            await conn.executemany(
                "INSERT INTO source_observation_baselines "
                "(source_unique_id, classification, recorded_at, snapshot_json, snapshot_sha256, "
                "description_state, is_backfilled) "
                "VALUES (?, 'historical_projection', ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_unique_id) DO NOTHING",
                baselines,
            )
    for table in (
        "source_observation_baselines",
        "source_observations",
        "source_observation_fields",
    ):
        for operation in ("UPDATE", "DELETE"):
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} "
                f"BEFORE {operation} ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'Source evidence is immutable'); END"
            )


class ObservationRepository:
    def __init__(
        self,
        read_connection: ConnectionScope,
        transaction: ConnectionScope,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._read_connection = read_connection
        self._transaction = transaction
        self._clock = clock

    async def ingest(self, observations: list[SourceObservation]) -> ObservationReceipt:
        """Commit a bounded atomic batch; duplicate delivery never updates evidence."""
        if not 1 <= len(observations) <= 100:
            raise ValueError("Observation batch requires 1..100 events")
        # Revalidation also rejects invalid model_copy/model_construct values.
        batch = [
            SourceObservation.model_validate_json(item.payload_json()) for item in observations
        ]
        bodies = [item.payload_json() for item in batch]
        if sum(len(body.encode()) for body in bodies) > MAX_BATCH_BYTES:
            raise ValueError("Observation batch exceeds the 4 MB bound")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Observation receipt clock must include a timezone")
        now = now.astimezone(UTC)
        if any(item.observed_at > now + timedelta(minutes=5) for item in batch):
            raise ValueError("Observation clock is more than five minutes ahead of receipt")
        received = now.isoformat(timespec="microseconds")
        inserted = replayed = 0
        async with self._transaction() as conn:
            for item, body in zip(batch, bodies, strict=True):
                digest = sha256_text(body)
                async with conn.execute(
                    "SELECT observation_id FROM source_observations "
                    "WHERE producer_id = ? AND event_id = ?",
                    (item.producer_id, item.event_id),
                ) as cursor:
                    previous = await cursor.fetchone()
                if previous:
                    if previous[0] != digest:
                        raise ObservationConflict(
                            "Event identity already contains different evidence"
                        )
                    replayed += 1
                    continue
                await conn.execute(
                    "INSERT INTO source_observations "
                    "(observation_id, producer_id, event_id, source_unique_id, origin, "
                    "observed_at, received_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        digest,
                        item.producer_id,
                        item.event_id,
                        item.source_unique_id,
                        item.origin,
                        item.observed_at.isoformat(timespec="microseconds"),
                        received,
                        body,
                    ),
                )
                await conn.executemany(
                    "INSERT INTO source_observation_fields "
                    "(observation_id, source_unique_id, field_name, authority, observed_at, "
                    "field_state, value_json) VALUES "
                    "(:observation_id, :source_unique_id, :field_name, :authority, :observed_at, "
                    ":field_state, :value_json)",
                    observation_field_rows(item),
                )
                inserted += 1
        return ObservationReceipt(inserted, replayed, tuple(item.sha256 for item in batch))

    async def get(self, observation_id: str) -> SourceObservation | None:
        async with (
            self._read_connection() as conn,
            conn.execute(
                "SELECT payload_json FROM source_observations WHERE observation_id = ?",
                (observation_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        if row is None:
            return None
        result = SourceObservation.model_validate_json(row[0])
        if result.sha256 != observation_id:
            raise ValueError("Stored observation checksum mismatch")
        return result

    async def history(
        self, source_unique_id: str, *, after: str = "", limit: int = 100
    ) -> list[SourceObservation]:
        """Keyset pages by checksum; do not mistake this order for observation time."""
        if not 1 <= limit <= 100:
            raise ValueError("History page requires 1..100 events")
        result = []
        size = 0
        async with (
            self._read_connection() as conn,
            conn.execute(
                "SELECT observation_id, payload_json FROM source_observations "
                "WHERE source_unique_id = ? AND observation_id > ? ORDER BY observation_id LIMIT ?",
                (source_unique_id, after, limit),
            ) as cursor,
        ):
            async for row in cursor:
                size += len(row[1].encode())
                if size > MAX_BATCH_BYTES:
                    break  # Return a shorter page; resume after its final checksum.
                item = SourceObservation.model_validate_json(row[1])
                if item.sha256 != row[0]:
                    raise ValueError("Stored observation checksum mismatch")
                result.append(item)
        return result

    async def project(self, source_unique_id: str) -> SourceProjection:
        """Read a projection of exact evidence; do not mutate existing application rows."""
        rows: list[dict[str, Any]] = []
        size = 0
        # Preserve all top-rank ties so contradictory values are not silently
        # resolved by arrival order or by arbitrary observation identifiers.
        async with (
            self._read_connection() as conn,
            conn.execute(
                """WITH ranked AS (
                    SELECT *, DENSE_RANK() OVER (
                        PARTITION BY field_name ORDER BY authority DESC, observed_at DESC
                    ) AS rank FROM source_observation_fields WHERE source_unique_id = ?
                ) SELECT observation_id, field_name, authority, observed_at, field_state, value_json
                  FROM ranked WHERE rank = 1 ORDER BY field_name, observation_id LIMIT 513""",
                (source_unique_id,),
            ) as cursor,
        ):
            async for row in cursor:
                size += len(row["value_json"].encode())
                if len(rows) >= 512 or size > MAX_BATCH_BYTES:
                    raise ValueError(
                        "Projection has too many tied inputs; adjudication is required"
                    )
                rows.append(dict(row))
        return project_field_rows(source_unique_id, rows)

    async def baseline(self, source_unique_id: str) -> dict[str, Any] | None:
        """Historical projection is a separate, explicitly undated evidence category."""
        async with (
            self._read_connection() as conn,
            conn.execute(
                "SELECT * FROM source_observation_baselines WHERE source_unique_id = ?",
                (source_unique_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        body = result.pop("snapshot_json")
        if sha256_text(body) != result["snapshot_sha256"]:
            raise ValueError("Historical baseline checksum mismatch")
        result["snapshot"] = json.loads(body)
        result["source_observed_at"] = None
        return result
