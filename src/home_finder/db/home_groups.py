"""Flat group destinations and original personal-action targets."""

from __future__ import annotations

import aiosqlite

from home_finder.logging import get_logger
from home_finder.models.home_group import HomeRoute
from home_finder.models.home_identity import HomeTarget, is_home_id

logger = get_logger(__name__)


class HomeActionConflict(ValueError):
    """The issued link cannot identify one retained personal-state origin."""


class HomeRouteConflict(ValueError):
    """A grouping would leave missing, chained or conflicting destinations."""


async def read_routes(conn: aiosqlite.Connection) -> dict[str, HomeRoute]:
    async with conn.execute("SELECT home_id, target_home_id, state FROM home_routes") as cur:
        return {row["home_id"]: HomeRoute.model_validate(dict(row)) for row in await cur.fetchall()}


async def group_scope(conn: aiosqlite.Connection, home_ids: tuple[str, ...]) -> tuple[str, ...]:
    routes = await read_routes(conn)
    if not home_ids or any(home not in routes for home in home_ids):
        raise HomeRouteConflict("unknown_home_scope")
    targets = {routes[home].target_home_id for home in home_ids} - {None}
    return tuple(
        sorted(set(home_ids) | {home for home, r in routes.items() if r.target_home_id in targets})
    )


async def link_origins(conn: aiosqlite.Connection, identifier: str) -> set[str]:
    if is_home_id(identifier):
        async with conn.execute(
            "SELECT home_id FROM homes WHERE home_id = ?", (identifier,)
        ) as cur:
            return {row[0] for row in await cur.fetchall()}
    # A never-promoted staging alias follows exact source membership. It has no
    # independent saved personal state; an unlinked source no longer resolves.
    async with conn.execute(
        "SELECT CASE WHEN a.kind = 'staging_source' THEN m.home_id ELSE a.home_id END "
        "FROM home_aliases a LEFT JOIN source_memberships m ON m.source_unique_id = a.alias "
        "WHERE a.alias = ?",
        (identifier,),
    ) as cur:
        return {row[0] for row in await cur.fetchall() if row[0] is not None}


async def resolve_group(conn: aiosqlite.Connection, identifier: str) -> HomeTarget | None:
    origins = await link_origins(conn, identifier)
    destinations = set()
    for home in origins:
        async with conn.execute(
            "SELECT h.home_id, h.current_property_id FROM home_routes r "
            "JOIN homes h ON h.home_id = r.target_home_id "
            "JOIN home_routes root ON root.home_id = h.home_id "
            "WHERE r.home_id = ? AND r.state = 'active' AND root.state = 'active' "
            "AND root.target_home_id = root.home_id AND h.current_property_id IS NOT NULL",
            (home,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        destinations.add(HomeTarget(row[0], row[1]))
    if len(destinations) > 1:
        logger.warning("home_resolution_refused", reason="ambiguous_alias", homes=len(destinations))
    return destinations.pop() if len(destinations) == 1 else None


async def resolve_action(conn: aiosqlite.Connection, identifier: str) -> HomeTarget | None:
    origins = await link_origins(conn, identifier)
    if not origins:
        return None
    if len(origins) != 1:
        logger.warning("home_action_refused", reason="ambiguous_origin", homes=len(origins))
        raise HomeActionConflict(
            "This link has multiple saved records. Open a specific saved record."
        )
    home = next(iter(origins))
    async with conn.execute(
        "SELECT current_property_id FROM homes WHERE home_id = ?", (home,)
    ) as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        logger.warning("home_action_refused", reason="unavailable_original", home_id=home)
        raise HomeActionConflict(
            "The original saved record is unavailable; its status cannot be changed."
        )
    return HomeTarget(home, row[0])


async def validate_routes(conn: aiosqlite.Connection) -> None:
    async with conn.execute(
        "SELECT count(*) FROM home_routes r LEFT JOIN homes h ON h.home_id = r.target_home_id "
        "LEFT JOIN home_routes root ON root.home_id = r.target_home_id "
        "WHERE r.state = 'active' AND (h.current_property_id IS NULL OR root.state != 'active' "
        "OR root.target_home_id IS NOT r.target_home_id)"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    if row[0]:
        raise HomeRouteConflict("group_destination_must_be_a_retained_root")
    async with conn.execute(
        "SELECT count(*) FROM source_memberships m "
        "LEFT JOIN home_routes r ON r.home_id = m.home_id "
        "WHERE m.home_id IS NOT NULL AND (r.state IS NOT 'active' "
        "OR r.target_home_id IS NOT m.home_id)"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    if row[0]:
        raise HomeRouteConflict("source_owner_must_be_an_active_root")


async def write_routes(
    conn: aiosqlite.Connection,
    decision_id: str,
    revision: int,
    changes: dict[str, tuple[HomeRoute, HomeRoute]],
) -> None:
    for home, (old, new) in sorted(changes.items()):
        await conn.execute(
            "INSERT INTO home_route_changes VALUES (?, ?, ?, ?, ?, ?)",
            (decision_id, home, old.target_home_id, new.target_home_id, old.state, new.state),
        )
        cur = await conn.execute(
            "UPDATE home_routes SET target_home_id = ?, state = ?, revision = ? "
            "WHERE home_id = ? AND target_home_id IS ? AND state = ?",
            (new.target_home_id, new.state, revision, home, old.target_home_id, old.state),
        )
        if cur.rowcount != 1:
            raise HomeRouteConflict("stale_home_route")
    await validate_routes(conn)


async def audit_routes(conn: aiosqlite.Connection) -> dict[str, int]:
    async with conn.execute("SELECT * FROM home_route_baselines") as cur:
        expected = {r[0]: (r[1], r[2], None) for r in await cur.fetchall()}
    errors: dict[str, int] = {}
    async with conn.execute(
        "SELECT c.*, d.revision FROM home_route_changes c "
        "JOIN membership_decisions d USING(decision_id) ORDER BY d.revision"
    ) as cur:
        for row in await cur.fetchall():
            if expected.get(row["home_id"], ())[:2] != (
                row["previous_target_home_id"],
                row["previous_state"],
            ):
                errors["route_chain"] = errors.get("route_chain", 0) + 1
            expected[row["home_id"]] = (
                row["next_target_home_id"],
                row["next_state"],
                row["revision"],
            )
    async with conn.execute("SELECT * FROM home_routes") as cur:
        actual = {r[0]: (r[1], r[2], r[3]) for r in await cur.fetchall()}
    if expected != actual:
        errors["route_projection"] = sum(
            expected.get(k) != actual.get(k) for k in expected.keys() | actual.keys()
        )
    try:
        await validate_routes(conn)
    except HomeRouteConflict:
        errors["route_invariants"] = 1
    return errors
