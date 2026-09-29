"""Additive provider identities and immutable representation claims.

Neither a resolved provider key nor a shared URL authorizes a home merge. All
legacy rows, ownership, observations and protected history remain untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from home_finder.db.observations import ConnectionScope
from home_finder.logging import get_logger
from home_finder.models.identity import (
    IDENTITY_POLICY_VERSION,
    IdentityAudit,
    SourceIdentityInput,
    SourceIdentityResolution,
    audit_source_identities,
    resolve_source_identity,
)

logger = get_logger(__name__)

IDENTITY_COLUMNS = "unique_id, source, source_id, url, is_backfilled, merged_id"


async def create_identity_schema(conn: aiosqlite.Connection) -> None:
    """Join the migration transaction; add metadata without rewriting source rows."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS provider_listings (
            listing_key TEXT PRIMARY KEY CHECK (length(listing_key) = 64),
            source TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(source, provider_id),
            UNIQUE(listing_key, source)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_identity_claims (
            claim_id TEXT PRIMARY KEY CHECK (length(claim_id) = 64),
            policy_version TEXT NOT NULL,
            source_unique_id TEXT NOT NULL,
            source TEXT NOT NULL,
            declared_source_id TEXT NOT NULL,
            url TEXT NOT NULL,
            is_backfilled INTEGER NOT NULL CHECK (is_backfilled IN (0, 1)),
            listing_key TEXT,
            reason TEXT NOT NULL CHECK (reason IN (
                'provider_id_and_url_agree', 'legacy_url_and_url_agree',
                'unsupported_source', 'unrecognized_listing_url',
                'provider_id_mismatch', 'unresolved_declared_id', 'inconsistent_unique_id'
            )),
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (listing_key, source) REFERENCES provider_listings(listing_key, source),
            CHECK ((listing_key IS NOT NULL) =
                (reason IN ('provider_id_and_url_agree', 'legacy_url_and_url_agree')))
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_identity_claims_source
        ON source_identity_claims(source_unique_id, policy_version)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_identity_claims_listing
        ON source_identity_claims(listing_key, source_unique_id)
    """)
    for table in ("provider_listings", "source_identity_claims"):
        for operation in ("UPDATE", "DELETE"):
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} "
                f"BEFORE {operation} ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'Provider identity evidence is immutable'); END"
            )
    total_rows = 0
    async with conn.execute(f"SELECT {IDENTITY_COLUMNS} FROM source_listings") as cursor:
        while batch := await cursor.fetchmany(100):
            inputs = [SourceIdentityInput.model_validate(dict(row)) for row in batch]
            await record_identity_claims(conn, inputs)
            total_rows += len(inputs)
    logger.info(
        "provider_identity_backfill_prepared",
        policy_version=IDENTITY_POLICY_VERSION,
        source_rows=total_rows,
    )


async def record_identity_claims(
    conn: aiosqlite.Connection, rows: list[SourceIdentityInput]
) -> None:
    """Record exact resolutions or unresolved reasons inside the caller's transaction."""
    recorded_at = datetime.now(UTC).isoformat(timespec="microseconds")
    inserted = 0
    unresolved = 0
    for row in rows:
        result = resolve_source_identity(row)
        if result.listing_key is not None:
            provider_cursor = await conn.execute(
                "INSERT INTO provider_listings (listing_key, source, provider_id, recorded_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(listing_key) DO NOTHING",
                (result.listing_key, row.source, result.provider_id, recorded_at),
            )
            await provider_cursor.close()
        cursor = await conn.execute(
            "INSERT INTO source_identity_claims "
            "(claim_id, policy_version, source_unique_id, source, declared_source_id, url, "
            "is_backfilled, listing_key, reason, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(claim_id) DO NOTHING",
            (
                row.claim_id,
                IDENTITY_POLICY_VERSION,
                row.unique_id,
                row.source,
                row.source_id,
                row.url,
                int(row.is_backfilled),
                result.listing_key,
                result.reason,
                recorded_at,
            ),
        )
        inserted += cursor.rowcount
        unresolved += int(result.listing_key is None)
        await cursor.close()
    logger.debug(
        "source_identity_claims_prepared",
        policy_version=IDENTITY_POLICY_VERSION,
        input_rows=len(rows),
        inserted=inserted,
        unresolved=unresolved,
        # The caller still owns commit/rollback; no raw URL-shaped IDs in logs.
    )


async def record_stored_identities(conn: aiosqlite.Connection, unique_ids: list[str]) -> None:
    """Capture the actual persisted representation, including legacy backfill provenance."""
    for start in range(0, len(unique_ids), 100):
        batch = unique_ids[start : start + 100]
        placeholders = ",".join("?" for _ in batch)
        async with conn.execute(
            f"SELECT {IDENTITY_COLUMNS} FROM source_listings "
            f"WHERE unique_id IN ({placeholders}) ORDER BY unique_id",
            batch,
        ) as cursor:
            rows = [
                SourceIdentityInput.model_validate(dict(row)) for row in await cursor.fetchall()
            ]
        await record_identity_claims(conn, rows)


class IdentityRepository:
    """Read exact identity maps without collapsing advertisements or changing ownership."""

    def __init__(self, read_connection: ConnectionScope) -> None:
        self._read_connection = read_connection

    async def audit(self) -> IdentityAudit:
        async with (
            self._read_connection() as conn,
            conn.execute(
                f"SELECT {IDENTITY_COLUMNS} FROM source_listings ORDER BY unique_id"
            ) as cursor,
        ):
            rows = [
                SourceIdentityInput.model_validate(dict(row)) for row in await cursor.fetchall()
            ]
        return audit_source_identities(rows)

    async def members(self, home_id: str) -> tuple[SourceIdentityResolution, ...]:
        """Return every source representation, including several IDs on the same portal."""
        async with (
            self._read_connection() as conn,
            conn.execute(
                f"SELECT {IDENTITY_COLUMNS} FROM source_listings "
                "WHERE merged_id = ? ORDER BY source, source_id, unique_id",
                (home_id,),
            ) as cursor,
        ):
            return tuple(
                resolve_source_identity(SourceIdentityInput.model_validate(dict(row)))
                for row in await cursor.fetchall()
            )
