"""Transaction-local ownership writes shared by ordinary saves and reviewed commands."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import aiosqlite

from home_finder.db.home_groups import read_routes, write_routes
from home_finder.db.homes import ensure_home
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.logging import get_logger
from home_finder.models.home_group import HomeRoute
from home_finder.models.membership import MembershipReceipt

logger = get_logger(__name__)


class MembershipConflict(ValueError):
    """The reviewed revision, map, or idempotency key no longer matches."""


async def membership_revision(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT COALESCE(MAX(revision), 0) FROM membership_decisions") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def write_membership_decision(
    conn: aiosqlite.Connection,
    *,
    decision_id: str,
    operation: str,
    actor: str,
    reason: str,
    evidence: tuple[str, ...],
    expected_revision: int,
    command_json: str,
    changes: dict[str, tuple[str | None, str | None]],
    reverses: str | None = None,
    route_changes: dict[str, tuple[HomeRoute, HomeRoute]] | None = None,
    invalidation_home_ids: tuple[str, ...] = (),
) -> MembershipReceipt:
    """Append evidence and update both projections under the caller's write lock."""
    if not conn.in_transaction:
        raise RuntimeError("Membership changes require an owning write transaction")
    if expected_revision != await membership_revision(conn):
        raise MembershipConflict("stale_membership_revision")
    route_changes = route_changes or {}
    if (not changes and not route_changes) or any(old == new for old, new in changes.values()):
        raise MembershipConflict("empty_membership_change")
    cursor = await conn.execute(
        "INSERT INTO membership_decisions (decision_id, operation, actor, reason, evidence_json, "
        "expected_revision, command_json, reverses, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            operation,
            actor,
            reason,
            json.dumps(evidence),
            expected_revision,
            command_json,
            reverses,
            datetime.now(UTC).isoformat(),
        ),
    )
    assert cursor.lastrowid is not None
    revision = cursor.lastrowid
    await cursor.close()
    for source_id, (old, new) in sorted(changes.items()):
        async with conn.execute(
            "SELECT m.home_id, s.merged_id, h.current_property_id FROM source_memberships m "
            "JOIN source_listings s ON s.unique_id = m.source_unique_id "
            "LEFT JOIN homes h ON h.home_id = m.home_id WHERE m.source_unique_id = ?",
            (source_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["home_id"] != old or row["merged_id"] != row["current_property_id"]:
            raise MembershipConflict("membership_projection_mismatch")
        target_property_id = None
        if new is not None:
            async with conn.execute(
                "SELECT current_property_id FROM homes WHERE home_id = ?", (new,)
            ) as cur:
                target = await cur.fetchone()
            if target is None or target[0] is None:
                raise MembershipConflict("unavailable_destination_home")
            target_property_id = target[0]
        await conn.execute(
            "INSERT INTO membership_changes VALUES (?, ?, ?, ?)", (decision_id, source_id, old, new)
        )
        await conn.execute(
            "UPDATE source_memberships SET home_id = ?, revision = ? WHERE source_unique_id = ?",
            (new, revision, source_id),
        )
        if new is not None:
            await conn.execute(
                "INSERT INTO membership_origins SELECT ?, ? "
                "WHERE NOT EXISTS (SELECT 1 FROM membership_origins WHERE source_unique_id = ?)",
                (source_id, new, source_id),
            )
        await conn.execute(
            "UPDATE source_listings SET merged_id = ? WHERE unique_id = ?",
            (target_property_id, source_id),
        )
    await write_routes(conn, decision_id, revision, route_changes)
    affected = set(invalidation_home_ids)
    affected.update(home for pair in changes.values() for home in pair if home is not None)
    affected.update(route_changes)
    affected.update(
        route.target_home_id
        for pair in route_changes.values()
        for route in pair
        if route.target_home_id is not None
    )
    await conn.executemany(
        "INSERT INTO membership_projection_invalidations VALUES (?, ?)",
        [(decision_id, home_id) for home_id in sorted(affected)],
    )
    logger.debug(
        "membership_decision_prepared",
        routes_changed=len(route_changes),
        decision_id=decision_id,
        operation=operation,
        revision=revision,
        changed=len(changes),
    )
    return MembershipReceipt(
        decision_id=decision_id,
        revision=revision,
        changed=len(changes),
        routes_changed=len(route_changes),
    )


async def link_memberships(
    conn: aiosqlite.Connection,
    links: Sequence[tuple[str, str | None]],
    *,
    operation: Literal["initial_link", "staging_link", "cleanup"] = "initial_link",
    reason: str,
    routes: dict[str, HomeRoute] | None = None,
) -> None:
    """Journal automatic links; ordinary saves can claim only currently unowned rows.

    Staging/cleanup callers must run their preservation gate in this transaction.
    Replaying unchanged links is a no-op, not a new event or revision.
    """
    requested: dict[str, str | None] = {}
    for source_id, property_id in links:
        if source_id in requested and requested[source_id] != property_id:
            raise MergeBlockedError({source_id: ["conflicting_link_targets"]})
        requested[source_id] = property_id
    homes: dict[str, str] = {}
    changes: dict[str, tuple[str | None, str | None]] = {}
    for source_id, property_id in sorted(requested.items()):
        async with conn.execute(
            "SELECT m.home_id, s.merged_id FROM source_listings s JOIN source_memberships m "
            "ON m.source_unique_id = s.unique_id WHERE s.unique_id = ?",
            (source_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise MergeBlockedError({source_id: ["missing_source_listing"]})
        if operation == "initial_link" and row["home_id"] is not None:
            async with conn.execute(
                "SELECT current_property_id FROM homes WHERE home_id = ?", (row["home_id"],)
            ) as cur:
                owner = await cur.fetchone()
            if owner is None or owner[0] != property_id:
                raise MergeBlockedError({source_id: ["membership_review_required"]})
        if property_id is not None and property_id not in homes:
            homes[property_id] = await ensure_home(conn, property_id)
        target = homes[property_id] if property_id is not None else None
        if row["home_id"] == target:
            if row["merged_id"] != property_id:
                raise MembershipConflict("membership_projection_mismatch")
            continue
        if operation == "initial_link" and row["home_id"] is not None:
            raise MergeBlockedError({source_id: ["membership_review_required"]})
        changes[source_id] = (row["home_id"], target)
    current_routes = await read_routes(conn)
    requested_routes = dict(routes or {})
    for home in homes.values():
        if current_routes[home].state == "retired":
            requested_routes[home] = HomeRoute(home_id=home, target_home_id=home)
    route_changes = {
        home: (current_routes[home], route)
        for home, route in requested_routes.items()
        if current_routes[home] != route
    }
    if not changes and not route_changes:
        return
    await write_membership_decision(
        conn,
        decision_id="md_" + uuid4().hex,
        operation=operation,
        actor="pipeline",
        reason=reason,
        evidence=("policy:existing-staging-and-linkage-gates",),
        expected_revision=await membership_revision(conn),
        command_json=json.dumps(
            {
                "version": 2,
                "links": sorted(requested.items()),
                "routes": {home: new.model_dump() for home, (_, new) in route_changes.items()},
            },
            sort_keys=True,
        ),
        route_changes=route_changes,
        changes=changes,
    )
