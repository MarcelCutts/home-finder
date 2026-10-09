"""Stable home IDs and durable legacy links; no fuzzy identity decisions.

Existing saved homes each retain their own identity. Only the existing guarded
staging-absorption transaction can add a redirect in this slice. Reviewed changes
to established membership and reversible personal-state projection are separate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import aiosqlite

from home_finder.db.observations import ConnectionScope
from home_finder.logging import get_logger
from home_finder.models.home_identity import HomeTarget, is_home_id

logger = get_logger(__name__)


class HomeIdentityConflict(ValueError):
    """An existing home or legacy link must not be rebound by an ordinary save."""


async def create_home_schema(conn: aiosqlite.Connection) -> None:
    """Add identity and link metadata in the owning migration transaction."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS homes (
            home_id TEXT PRIMARY KEY CHECK (
                length(home_id) = 34 AND substr(home_id, 1, 2) = 'h_' AND
                substr(home_id, 3) NOT GLOB '*[^0-9a-f]*'
            ),
            origin_property_id TEXT NOT NULL UNIQUE,
            current_property_id TEXT UNIQUE REFERENCES properties(unique_id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_aliases (
            alias TEXT NOT NULL,
            home_id TEXT NOT NULL REFERENCES homes(home_id),
            kind TEXT NOT NULL CHECK (kind IN ('canonical', 'legacy', 'staging_source')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (alias, home_id)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_home_aliases_home ON home_aliases(home_id)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_redirects (
            home_id TEXT PRIMARY KEY REFERENCES homes(home_id),
            target_home_id TEXT NOT NULL REFERENCES homes(home_id),
            reason TEXT NOT NULL CHECK (reason = 'staging_absorption'),
            created_at TEXT NOT NULL,
            CHECK (home_id != target_home_id)
        )
    """)
    for table in ("home_aliases", "home_redirects"):
        for operation in ("UPDATE", "DELETE"):
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} "
                f"BEFORE {operation} ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'Home link evidence is immutable'); END"
            )
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS immutable_home_identity
        BEFORE UPDATE OF home_id, origin_property_id, created_at ON homes
        BEGIN SELECT RAISE(ABORT, 'Home identity is immutable'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS immutable_home_delete BEFORE DELETE ON homes
        BEGIN SELECT RAISE(ABORT, 'Home identity is immutable'); END
    """)
    count = 0
    async with conn.execute("SELECT unique_id FROM properties ORDER BY unique_id") as cursor:
        while batch := await cursor.fetchmany(100):
            for row in batch:
                await ensure_home(conn, row["unique_id"])
                count += 1
    # The old alias table is retained verbatim. Multiple claims for an alias
    # remain visible and the resolver refuses different destinations.
    await conn.execute("""
        INSERT INTO home_aliases (alias, home_id, kind, created_at)
        SELECT a.unique_id, h.home_id, 'legacy', a.created_at
        FROM source_aliases a JOIN homes h ON h.origin_property_id = a.anchor_id
        WHERE true ON CONFLICT(alias, home_id) DO NOTHING
    """)
    logger.info("home_identity_backfill_prepared", homes=count)


async def ensure_home(conn: aiosqlite.Connection, property_id: str) -> str:
    """Register a saved canonical row; never reassign a pre-existing alias."""
    async with conn.execute(
        "SELECT h.home_id, h.current_property_id, r.target_home_id "
        "FROM homes h LEFT JOIN home_redirects r ON r.home_id = h.home_id "
        "WHERE h.origin_property_id = ?",
        (property_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is not None:
        if row["target_home_id"] or row["current_property_id"] not in (None, property_id):
            raise HomeIdentityConflict("Ordinary save cannot revive a reassociated home")
        if row["current_property_id"] is None:
            await conn.execute(
                "UPDATE homes SET current_property_id = ? WHERE home_id = ?",
                (property_id, row["home_id"]),
            )
        return str(row["home_id"])
    async with conn.execute("SELECT 1 FROM home_aliases WHERE alias = ?", (property_id,)) as cursor:
        if await cursor.fetchone() is not None:
            raise HomeIdentityConflict("Ordinary save cannot reuse an existing home alias")
    home_id = "h_" + uuid4().hex
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT INTO homes (home_id, origin_property_id, current_property_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        (home_id, property_id, property_id, now),
    )
    await conn.execute(
        "INSERT INTO home_aliases (alias, home_id, kind, created_at) VALUES (?, ?, 'canonical', ?)",
        (property_id, home_id, now),
    )
    logger.debug("home_identity_prepared", home_id=home_id)
    return home_id


async def resolve_home(conn: aiosqlite.Connection, identifier: str) -> HomeTarget | None:
    """Resolve within the caller's scope, including the transaction for mutations."""
    if is_home_id(identifier):
        initial = [identifier]
    else:
        async with conn.execute(
            "SELECT home_id FROM home_aliases WHERE alias = ?", (identifier,)
        ) as cursor:
            initial = [row["home_id"] for row in await cursor.fetchall()]
    destinations = set()
    for home_id in initial:
        visited: set[str] = set()
        while home_id not in visited:
            visited.add(home_id)
            async with conn.execute(
                "SELECT h.current_property_id, r.target_home_id "
                "FROM homes h LEFT JOIN home_redirects r ON r.home_id = h.home_id "
                "WHERE h.home_id = ?",
                (home_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            if row["target_home_id"]:
                home_id = row["target_home_id"]
                continue
            if row["current_property_id"] is None:
                return None
            destinations.add(HomeTarget(home_id=home_id, property_id=row["current_property_id"]))
            break
        else:
            logger.error("home_resolution_refused", reason="redirect_cycle", home_id=home_id)
            return None
    if len(destinations) == 1:
        return destinations.pop()
    if destinations:
        logger.warning("home_resolution_refused", reason="ambiguous_alias", homes=len(destinations))
    return None


async def redirect_staging_homes(
    conn: aiosqlite.Connection, anchor_id: str, absorbed_ids: list[str]
) -> None:
    """Join guarded staging absorption; retain old home IDs and source aliases."""
    anchor_home = await ensure_home(conn, anchor_id)
    now = datetime.now(UTC).isoformat()
    for property_id in absorbed_ids:
        async with conn.execute(
            "SELECT h.home_id, r.target_home_id FROM homes h "
            "LEFT JOIN home_redirects r ON r.home_id = h.home_id "
            "WHERE h.origin_property_id = ?",
            (property_id,),
        ) as cursor:
            old = await cursor.fetchone()
        if old is None:
            async with conn.execute(
                "SELECT 1 FROM home_aliases WHERE alias = ?", (property_id,)
            ) as cursor:
                has_alias = await cursor.fetchone() is not None
            if has_alias:
                if await resolve_home(conn, property_id) != HomeTarget(anchor_home, anchor_id):
                    raise HomeIdentityConflict("Staging source alias has a different destination")
                continue
            await conn.execute(
                "INSERT INTO home_aliases (alias, home_id, kind, created_at) "
                "VALUES (?, ?, 'staging_source', ?) ON CONFLICT(alias, home_id) DO NOTHING",
                (property_id, anchor_home, now),
            )
        elif old["target_home_id"] is None:
            await conn.execute(
                "INSERT INTO home_redirects (home_id, target_home_id, reason, created_at) "
                "VALUES (?, ?, 'staging_absorption', ?)",
                (old["home_id"], anchor_home, now),
            )
        elif old["target_home_id"] != anchor_home:
            if await resolve_home(conn, old["home_id"]) != HomeTarget(anchor_home, anchor_id):
                raise HomeIdentityConflict("Staging alias already redirects to another home")
    logger.debug("home_links_prepared", anchor_home_id=anchor_home, absorbed=len(absorbed_ids))


class HomeRepository:
    def __init__(self, read_connection: ConnectionScope) -> None:
        self._read_connection = read_connection

    async def resolve(self, identifier: str) -> HomeTarget | None:
        async with self._read_connection() as conn:
            return await resolve_home(conn, identifier)
