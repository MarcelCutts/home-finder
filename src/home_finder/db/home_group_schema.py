"""Retained home rows with independently replayable group routing."""

from __future__ import annotations

import aiosqlite

from home_finder.logging import get_logger
from home_finder.models.home_group import HomeRoute

logger = get_logger(__name__)


async def _baseline_routes(conn: aiosqlite.Connection) -> list[HomeRoute]:
    """Use exact current ownership/old redirects; preserve ambiguous cases explicitly."""
    async with conn.execute("SELECT * FROM homes ORDER BY home_id") as cursor:
        homes = {row["home_id"]: dict(row) for row in await cursor.fetchall()}
    async with conn.execute("SELECT home_id, target_home_id FROM home_redirects") as cursor:
        redirects: dict[str, str] = {r[0]: r[1] for r in await cursor.fetchall()}
    async with conn.execute("SELECT source_unique_id, home_id FROM source_memberships") as cursor:
        memberships: dict[str, str | None] = {r[0]: r[1] for r in await cursor.fetchall()}
    origins: dict[str, set[str | None]] = {}
    async with conn.execute("SELECT source_unique_id, home_id FROM membership_origins") as cursor:
        for source_id, home_id in await cursor.fetchall():
            origins.setdefault(home_id, set()).add(memberships.get(source_id))
    owners = {owner for owner in memberships.values() if owner is not None}
    candidates: dict[str, HomeRoute] = {}
    for home_id, home in homes.items():
        if home_id in owners:
            if home["current_property_id"] is None:
                raise ValueError("Owned home has no retained storage binding")
            target = home_id
        else:
            destinations = origins.get(home_id, set()) - {None}
            origin_source_owner = memberships.get(home["origin_property_id"])
            if not destinations and origin_source_owner is not None:
                destinations.add(origin_source_owner)
            if len(destinations) > 1:
                candidates[home_id] = HomeRoute(
                    home_id=home_id, target_home_id=None, state="review_required"
                )
                continue
            target = next(iter(destinations), None) or redirects.get(home_id)
            if target is None and home["current_property_id"] is not None:
                target = home_id
        candidates[home_id] = HomeRoute(
            home_id=home_id, target_home_id=target, state="active" if target else "retired"
        )
    result = []
    for home_id, route in candidates.items():
        visited: set[str] = set()
        current = route
        while current.state == "active" and current.target_home_id != current.home_id:
            if current.home_id in visited or current.target_home_id not in candidates:
                current = HomeRoute(home_id=home_id, target_home_id=None, state="review_required")
                break
            visited.add(current.home_id)
            assert current.target_home_id is not None
            current = candidates[current.target_home_id]
        result.append(
            HomeRoute(home_id=home_id, target_home_id=current.target_home_id, state=current.state)
        )
    return result


async def create_home_group_schema(conn: aiosqlite.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_route_baselines (
            home_id TEXT PRIMARY KEY REFERENCES homes(home_id),
            target_home_id TEXT REFERENCES homes(home_id),
            state TEXT NOT NULL CHECK (state IN ('active', 'retired', 'review_required')),
            CHECK ((state = 'active') = (target_home_id IS NOT NULL))
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_routes (
            home_id TEXT PRIMARY KEY REFERENCES home_route_baselines(home_id),
            target_home_id TEXT REFERENCES homes(home_id),
            state TEXT NOT NULL CHECK (state IN ('active', 'retired', 'review_required')),
            revision INTEGER REFERENCES membership_decisions(revision),
            CHECK ((state = 'active') = (target_home_id IS NOT NULL))
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_home_routes_target ON home_routes(target_home_id, home_id)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_route_changes (
            decision_id TEXT NOT NULL REFERENCES membership_decisions(decision_id),
            home_id TEXT NOT NULL REFERENCES homes(home_id),
            previous_target_home_id TEXT REFERENCES homes(home_id),
            next_target_home_id TEXT REFERENCES homes(home_id),
            previous_state TEXT NOT NULL
                CHECK (previous_state IN ('active', 'retired', 'review_required')),
            next_state TEXT NOT NULL CHECK (next_state IN ('active', 'retired', 'review_required')),
            PRIMARY KEY (decision_id, home_id),
            CHECK ((previous_state = 'active') = (previous_target_home_id IS NOT NULL)),
            CHECK ((next_state = 'active') = (next_target_home_id IS NOT NULL)),
            CHECK (previous_target_home_id IS NOT next_target_home_id
                OR previous_state != next_state)
        )
    """)
    baselines = await _baseline_routes(conn)
    for route in baselines:
        await conn.execute(
            "INSERT INTO home_route_baselines SELECT ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM home_route_baselines WHERE home_id = ?)",
            (route.home_id, route.target_home_id, route.state, route.home_id),
        )
    await conn.execute("""
        INSERT INTO home_routes
        SELECT home_id, target_home_id, state, NULL FROM home_route_baselines
        WHERE home_id NOT IN (SELECT home_id FROM home_routes)
    """)
    for table in ("home_route_baselines", "home_route_changes"):
        for action in ("UPDATE", "DELETE"):
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} "
                f"BEFORE {action} ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'Home routing evidence is immutable'); END"
            )
    for table, key in (
        ("home_route_baselines", "home_id = NEW.home_id"),
        ("home_routes", "home_id = NEW.home_id"),
        ("home_route_changes", "home_id = NEW.home_id AND decision_id = NEW.decision_id"),
    ):
        await conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS no_replace_{table} BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {key}) BEGIN "
            "SELECT RAISE(ABORT, 'Home routing evidence cannot be replaced'); END"
        )
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS new_home_route AFTER INSERT ON homes
        BEGIN
            INSERT INTO home_route_baselines VALUES (NEW.home_id, NEW.home_id, 'active');
            INSERT INTO home_routes VALUES (NEW.home_id, NEW.home_id, 'active', NULL);
        END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS home_route_guard BEFORE UPDATE ON home_routes
        WHEN NEW.home_id != OLD.home_id OR NOT EXISTS (
            SELECT 1 FROM home_route_changes c JOIN membership_decisions d USING(decision_id)
            WHERE c.home_id = OLD.home_id AND d.revision = NEW.revision
                AND NEW.revision > COALESCE(OLD.revision, 0)
                AND c.previous_target_home_id IS OLD.target_home_id
                AND c.next_target_home_id IS NEW.target_home_id
                AND c.previous_state = OLD.state AND c.next_state = NEW.state
        )
        BEGIN SELECT RAISE(ABORT, 'Home routing requires a recorded decision'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS home_route_owner_guard BEFORE UPDATE ON home_routes
        WHEN (NEW.state != 'active' OR NEW.target_home_id != NEW.home_id)
            AND EXISTS (SELECT 1 FROM source_memberships WHERE home_id = NEW.home_id)
        BEGIN
            SELECT RAISE(ABORT, 'An owning home must remain an active group root');
        END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS home_route_delete BEFORE DELETE ON home_routes
        BEGIN SELECT RAISE(ABORT, 'Home routing cannot be deleted'); END
    """)
    await conn.execute("""
        CREATE VIEW IF NOT EXISTS active_properties AS
        SELECT p.* FROM properties p JOIN homes h ON h.current_property_id = p.unique_id
        JOIN home_routes r ON r.home_id = h.home_id
        WHERE r.state = 'active' AND r.target_home_id = h.home_id
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS retained_home_binding
        BEFORE UPDATE OF current_property_id ON homes
        WHEN NEW.current_property_id IS NOT OLD.current_property_id AND EXISTS (
            SELECT 1 FROM home_route_changes WHERE home_id = OLD.home_id
        )
        BEGIN SELECT RAISE(ABORT, 'Retained home history requires a membership decision'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS legacy_redirects_closed BEFORE INSERT ON home_redirects
        BEGIN
            SELECT RAISE(ABORT, 'Home redirects are legacy evidence; record a route decision');
        END
    """)
    await conn.execute("""
        CREATE VIEW IF NOT EXISTS group_personal_states AS
        WITH origins(group_home_id, home_id) AS (
            SELECT target_home_id, home_id FROM home_routes WHERE state = 'active'
            UNION
            SELECT m.home_id, o.home_id FROM source_memberships m
            JOIN membership_origins o USING(source_unique_id) WHERE m.home_id IS NOT NULL
        )
        SELECT o.group_home_id, h.home_id, h.current_property_id AS property_id,
            p.title, COALESCE(p.user_status, 'new') AS user_status,
            EXISTS (SELECT 1 FROM viewing_messages v
                WHERE v.property_unique_id = h.current_property_id) AS has_message
        FROM origins o JOIN homes h ON h.home_id = o.home_id
        LEFT JOIN properties p ON p.unique_id = h.current_property_id
    """)
    logger.info(
        "home_route_baselines_prepared",
        homes=len(baselines),
        review_required=sum(route.state == "review_required" for route in baselines),
    )
