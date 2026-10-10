"""Reviewed, reversible source membership with retained home and user provenance.

Reviewed commands are an operator API, not a fuzzy matcher or a dashboard
projection rebuild. They retain both original home records and issued links.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import aiosqlite

from home_finder.db.home_groups import HomeRouteConflict, audit_routes, group_scope, read_routes
from home_finder.db.membership_ops import (
    MembershipConflict,
    membership_revision,
    write_membership_decision,
)
from home_finder.db.observations import ConnectionScope
from home_finder.logging import get_logger
from home_finder.models.home_group import HomeRoute
from home_finder.models.membership import (
    MemberAssignment,
    MembershipAudit,
    MembershipDecision,
    MembershipReceipt,
    MembershipSnapshot,
    PersonalState,
)

logger = get_logger(__name__)


async def membership_snapshot(
    conn: aiosqlite.Connection, home_ids: tuple[str, ...], unowned_source_ids: tuple[str, ...] = ()
) -> MembershipSnapshot:
    """Include every source representation and each original personal-state owner."""
    scope = await group_scope(conn, home_ids)
    if not scope:
        raise MembershipConflict("empty_home_scope")
    members: list[MemberAssignment] = []
    evidence_hash = hashlib.sha256()
    provenance = set(scope)
    marks = ",".join("?" for _ in scope)
    unowned_marks = ",".join("?" for _ in unowned_source_ids) or "NULL"
    async with conn.execute(
        "SELECT s.*, m.home_id, o.home_id AS origin_home_id "
        "FROM source_memberships m JOIN source_listings s ON s.unique_id = m.source_unique_id "
        "LEFT JOIN membership_origins o USING(source_unique_id) "
        f"WHERE m.home_id IN ({marks}) OR (m.home_id IS NULL "
        f"AND m.source_unique_id IN ({unowned_marks})) ORDER BY s.unique_id",
        (*scope, *unowned_source_ids),
    ) as cur:
        source_rows = await cur.fetchall()
    for member in source_rows:
        source_id = member["unique_id"]
        members.append(MemberAssignment(source_unique_id=source_id, home_id=member["home_id"]))
        if member["origin_home_id"]:
            provenance.add(member["origin_home_id"])
        evidence_hash.update(json.dumps(dict(member), sort_keys=True).encode())
        for table, key in (
            ("source_observations", "observation_id"),
            ("source_identity_claims", "claim_id"),
        ):
            async with conn.execute(
                f"SELECT {key} FROM {table} WHERE source_unique_id = ? ORDER BY {key}", (source_id,)
            ) as evidence_cursor:
                evidence_hash.update(
                    json.dumps([r[0] for r in await evidence_cursor.fetchall()]).encode()
                )
    states = []
    for home_id in sorted(provenance):
        async with conn.execute(
            "SELECT h.current_property_id, p.user_status, v.message, "
            "(SELECT COALESCE(MAX(id), 0) FROM status_events "
            "WHERE property_unique_id = h.current_property_id) AS status_revision "
            "FROM homes h LEFT JOIN properties p ON p.unique_id = h.current_property_id "
            "LEFT JOIN viewing_messages v ON v.property_unique_id = h.current_property_id "
            "WHERE h.home_id = ?",
            (home_id,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        digest = (
            hashlib.sha256(row["message"].encode()).hexdigest()
            if row["message"] is not None
            else None
        )
        states.append(
            PersonalState(
                home_id=home_id,
                property_id=row["current_property_id"],
                user_status=row["user_status"],
                last_status_event_id=row["status_revision"],
                viewing_message_sha256=digest,
            )
        )
    return MembershipSnapshot(
        revision=await membership_revision(conn),
        scope_home_ids=scope,
        members=tuple(sorted(members, key=lambda m: m.source_unique_id)),
        source_evidence_sha256=evidence_hash.hexdigest(),
        personal_states=tuple(states),
        routes=tuple(
            route for home, route in sorted((await read_routes(conn)).items()) if home in scope
        ),
    )


def _changed(decision: MembershipDecision) -> dict[str, tuple[str | None, str | None]]:
    return {
        old.source_unique_id: (old.home_id, new.home_id)
        for old, new in zip(decision.before.members, decision.after, strict=True)
        if old.home_id != new.home_id
    }


def _route_changes(decision: MembershipDecision) -> dict[str, tuple[HomeRoute, HomeRoute]]:
    return {
        old.home_id: (old, new)
        for old, new in zip(decision.before.routes, decision.after_routes, strict=True)
        if old != new
    }


async def _split_routes(
    conn: aiosqlite.Connection,
    before: MembershipSnapshot,
    after: tuple[MemberAssignment, ...],
    overrides: dict[str, str] | None = None,
) -> tuple[HomeRoute, ...]:
    assignments = {m.source_unique_id: m.home_id for m in after}
    owners = {m.home_id for m in after} - {None}
    routes = {r.home_id: r for r in before.routes}
    overrides = overrides or {}
    if not overrides.keys() <= routes.keys() or not set(overrides.values()) <= owners:
        raise MembershipConflict("alias_routes_must_choose_reviewed_owners")
    for home, route in routes.items():
        target: str | None
        if home in owners:
            target = home
            if home in overrides and overrides[home] != home:
                raise MembershipConflict("owning_home_cannot_be_an_alias")
        elif home in overrides:
            target = overrides[home]
        else:
            async with conn.execute(
                "SELECT source_unique_id FROM membership_origins WHERE home_id = ?", (home,)
            ) as cur:
                ids = [r[0] for r in await cur.fetchall()]
            if not ids:
                async with conn.execute(
                    "SELECT origin_property_id FROM homes WHERE home_id = ?", (home,)
                ) as cur:
                    row = await cur.fetchone()
                assert row is not None
                ids = [row[0]]
            destinations = {assignments[i] for i in ids if i in assignments} - {None}
            if len(destinations) > 1:
                raise MembershipConflict("ambiguous_alias_split_requires_explicit_route")
            target = next(iter(destinations), route.target_home_id)
        routes[home] = HomeRoute(
            home_id=home, target_home_id=target, state="active" if target else route.state
        )
    return tuple(routes[home] for home in sorted(routes))


async def _reversal_map(
    conn: aiosqlite.Connection, decision_id: str
) -> tuple[dict[str, str | None], dict[str, HomeRoute], tuple[str, ...]]:
    async with conn.execute(
        "SELECT revision, operation, command_json FROM membership_decisions WHERE decision_id = ?",
        (decision_id,),
    ) as cur:
        decision = await cur.fetchone()
    if decision is None or decision["operation"] not in {
        "merge",
        "split",
        "reversal",
        "staging_link",
        "cleanup",
    }:
        raise MembershipConflict("only_reviewed_or_retained_retirement_decisions_are_reversible")
    payload = json.loads(decision["command_json"])
    if payload["version"] == 1 and decision["operation"] not in {"merge", "split", "reversal"}:
        raise MembershipConflict("old_automatic_retirement_has_no_recoverable_route_history")
    async with conn.execute(
        "SELECT 1 FROM membership_decisions WHERE reverses = ?", (decision_id,)
    ) as cur:
        if await cur.fetchone():
            raise MembershipConflict("decision_already_reversed")
    async with conn.execute(
        "SELECT c.source_unique_id, c.previous_home_id, c.next_home_id, m.home_id, m.revision "
        "FROM membership_changes c JOIN source_memberships m USING(source_unique_id) "
        "WHERE c.decision_id = ?",
        (decision_id,),
    ) as cur:
        rows = await cur.fetchall()
    if any(
        r["revision"] != decision["revision"] or r["home_id"] != r["next_home_id"] for r in rows
    ):
        raise MembershipConflict("later_decision_touched_membership")
    async with conn.execute(
        "SELECT c.*, r.revision, r.target_home_id, r.state FROM home_route_changes c "
        "JOIN home_routes r USING(home_id) WHERE c.decision_id = ?",
        (decision_id,),
    ) as cur:
        route_rows = await cur.fetchall()
    if any(
        r["revision"] != decision["revision"]
        or r["target_home_id"] != r["next_target_home_id"]
        or r["state"] != r["next_state"]
        for r in route_rows
    ):
        raise MembershipConflict("later_decision_touched_home_route")
    destinations = {r["source_unique_id"]: r["previous_home_id"] for r in rows}
    routes = {
        r["home_id"]: HomeRoute(
            home_id=r["home_id"],
            target_home_id=r["previous_target_home_id"],
            state=r["previous_state"],
        )
        for r in route_rows
    }
    scope = (
        {r[key] for r in rows for key in ("previous_home_id", "next_home_id")}
        | set(routes)
        | {r.target_home_id for r in routes.values()}
    )
    if "before" in payload:
        scope.update(payload["before"]["scope_home_ids"])
    homes = tuple(sorted(home for home in scope if home is not None))
    if payload["version"] == 1:
        before = await membership_snapshot(conn, homes)
        after = tuple(
            MemberAssignment(
                source_unique_id=m.source_unique_id,
                home_id=destinations.get(m.source_unique_id, m.home_id),
            )
            for m in before.members
        )
        inferred = await _split_routes(conn, before, after)
        routes = {
            new.home_id: new for old, new in zip(before.routes, inferred, strict=True) if old != new
        }
    return destinations, routes, homes


async def _validate_operation(conn: aiosqlite.Connection, decision: MembershipDecision) -> None:
    changes = _changed(decision)
    if decision.operation == "merge":
        owners = {m.home_id for m in decision.after}
        if (
            len(decision.before.scope_home_ids) < 2
            or len(owners) != 1
            or any(
                r.target_home_id not in owners or r.state != "active" for r in decision.after_routes
            )
        ):
            raise MembershipConflict("merge_requires_complete_homes_and_one_destination")
    elif decision.operation == "split":
        origins = {old for old, _ in changes.values()}
        if len(origins) != 1 or not origins <= {m.home_id for m in decision.after}:
            raise MembershipConflict("split_must_retain_members_in_its_original_home")
    else:
        assert decision.reverses is not None
        expected, routes, _ = await _reversal_map(conn, decision.reverses)
        if {source: new for source, (_, new) in changes.items()} != expected or {
            home: new for home, (_, new) in _route_changes(decision).items()
        } != routes:
            raise MembershipConflict("reversal_must_restore_the_recorded_map")
    routes = {r.home_id: r for r in decision.after_routes}
    for route in routes.values():
        if route.state == "active":
            root = routes.get(route.target_home_id or "")
            if root is None or root.state != "active" or root.target_home_id != root.home_id:
                raise MembershipConflict("destination_must_be_a_group_root")
            async with conn.execute(
                "SELECT current_property_id FROM homes WHERE home_id = ?", (root.home_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None or row[0] is None:
                raise MembershipConflict("unavailable_destination_home")
    for member in decision.after:
        if member.home_id is not None and (
            routes[member.home_id].state != "active"
            or routes[member.home_id].target_home_id != member.home_id
        ):
            raise MembershipConflict("owning_home_must_be_a_group_root")


class MembershipRepository:
    def __init__(self, transaction: ConnectionScope) -> None:
        # Even preparation takes a short write scope: multiple SELECTs must see
        # the same revision across independent SQLite connections.
        self._transaction = transaction

    async def inspect(self, home_ids: tuple[str, ...]) -> MembershipSnapshot:
        async with self._transaction() as conn:
            return await membership_snapshot(conn, home_ids)

    async def audit(self) -> MembershipAudit:
        """Replay immutable history in memory and compare both current projections."""
        async with self._transaction() as conn:
            async with conn.execute("SELECT * FROM membership_baselines") as cur:
                expected: dict[str, tuple[str | None, int | None]] = {
                    row["source_unique_id"]: (row["home_id"], None) for row in await cur.fetchall()
                }
            origins = {source: home for source, (home, _) in expected.items() if home is not None}
            errors: dict[str, int] = {}
            decisions = 0
            previous_revision = 0
            async with conn.execute("SELECT * FROM membership_decisions ORDER BY revision") as cur:
                for decision in await cur.fetchall():
                    decisions += 1
                    if decision["expected_revision"] != previous_revision:
                        errors["revision_chain"] = errors.get("revision_chain", 0) + 1
                    previous_revision = decision["revision"]
                    async with conn.execute(
                        "SELECT * FROM membership_changes WHERE decision_id = ?",
                        (decision["decision_id"],),
                    ) as changes_cursor:
                        changes = await changes_cursor.fetchall()
                    if not changes:
                        errors["empty_decision"] = errors.get("empty_decision", 0) + 1
                    for change in changes:
                        source = change["source_unique_id"]
                        if (
                            source not in expected
                            or expected[source][0] != change["previous_home_id"]
                        ):
                            errors["ownership_chain"] = errors.get("ownership_chain", 0) + 1
                        expected[source] = (change["next_home_id"], decision["revision"])
                        if change["next_home_id"] is not None:
                            origins.setdefault(source, change["next_home_id"])
            async with conn.execute("SELECT * FROM source_memberships") as cur:
                actual = {
                    row["source_unique_id"]: (row["home_id"], row["revision"])
                    for row in await cur.fetchall()
                }
            if expected != actual:
                errors["membership_projection"] = sum(
                    expected.get(key) != actual.get(key) for key in expected.keys() | actual.keys()
                )
            async with conn.execute("SELECT * FROM membership_origins") as cur:
                actual_origins = {row[0]: row[1] for row in await cur.fetchall()}
            if origins != actual_origins:
                errors["origin_projection"] = sum(
                    origins.get(key) != actual_origins.get(key)
                    for key in origins.keys() | actual_origins.keys()
                )
            async with conn.execute(
                "SELECT count(*) FROM source_memberships m "
                "LEFT JOIN source_listings s ON s.unique_id = m.source_unique_id "
                "LEFT JOIN homes h ON h.home_id = m.home_id "
                "WHERE s.merged_id IS NOT h.current_property_id "
                "OR (m.home_id IS NOT NULL "
                "AND (s.unique_id IS NULL OR h.current_property_id IS NULL))"
            ) as cur:
                drift = await cur.fetchone()
            assert drift is not None
            if drift[0]:
                errors["legacy_projection"] = int(drift[0])
            async with conn.execute(
                "SELECT count(*) FROM source_listings s LEFT JOIN source_memberships m "
                "ON s.unique_id = m.source_unique_id WHERE m.source_unique_id IS NULL"
            ) as cur:
                missing = await cur.fetchone()
            assert missing is not None
            if missing[0]:
                errors["missing_memberships"] = int(missing[0])
            errors.update(await audit_routes(conn))
            report = MembershipAudit(
                revision=previous_revision,
                decisions=decisions,
                retained_source_memberships=len(expected),
                errors=errors,
                valid=not errors,
            )
        logger.info(
            "membership_audit_finished",
            revision=report.revision,
            decisions=report.decisions,
            errors=report.errors,
            valid=report.valid,
        )
        return report

    async def prepare_merge(
        self,
        target_home_id: str,
        other_home_ids: tuple[str, ...],
        *,
        actor: str,
        reason: str,
        evidence: tuple[str, ...],
    ) -> MembershipDecision:
        async with self._transaction() as conn:
            before = await membership_snapshot(conn, (target_home_id, *other_home_ids))
            decision = MembershipDecision(
                version=2,
                decision_id="md_" + uuid4().hex,
                operation="merge",
                after_routes=tuple(
                    HomeRoute(home_id=h, target_home_id=target_home_id)
                    for h in before.scope_home_ids
                ),
                actor=actor,
                reason=reason,
                evidence=evidence,
                before=before,
                after=tuple(
                    MemberAssignment(source_unique_id=m.source_unique_id, home_id=target_home_id)
                    for m in before.members
                ),
            )
            await _validate_operation(conn, decision)
            return decision

    async def prepare_split(
        self,
        source_home_id: str,
        destinations: dict[str, str],
        *,
        alias_routes: dict[str, str] | None = None,
        actor: str,
        reason: str,
        evidence: tuple[str, ...],
    ) -> MembershipDecision:
        async with self._transaction() as conn:
            before = await membership_snapshot(conn, (source_home_id, *destinations.values()))
            eligible = {m.source_unique_id for m in before.members if m.home_id == source_home_id}
            if not destinations or not destinations.keys() <= eligible:
                raise MembershipConflict("split_members_must_belong_to_the_source_home")
            after = tuple(
                MemberAssignment(
                    source_unique_id=m.source_unique_id,
                    home_id=destinations.get(m.source_unique_id, m.home_id),
                )
                for m in before.members
            )
            decision = MembershipDecision(
                version=2,
                decision_id="md_" + uuid4().hex,
                operation="split",
                actor=actor,
                reason=reason,
                evidence=evidence,
                before=before,
                after=after,
                after_routes=await _split_routes(conn, before, after, alias_routes),
            )
            await _validate_operation(conn, decision)
            return decision

    async def prepare_reversal(
        self,
        decision_id: str,
        *,
        actor: str,
        reason: str,
        evidence: tuple[str, ...],
    ) -> MembershipDecision:
        async with self._transaction() as conn:
            destinations, routes, scope = await _reversal_map(conn, decision_id)
            before = await membership_snapshot(conn, scope, tuple(destinations))
            decision = MembershipDecision(
                version=2,
                decision_id="md_" + uuid4().hex,
                operation="reversal",
                actor=actor,
                reason=reason,
                evidence=evidence,
                before=before,
                reverses=decision_id,
                after_routes=tuple(routes.get(r.home_id, r) for r in before.routes),
                after=tuple(
                    MemberAssignment(
                        source_unique_id=m.source_unique_id,
                        home_id=destinations.get(m.source_unique_id, m.home_id),
                    )
                    for m in before.members
                ),
            )
            await _validate_operation(conn, decision)
            return decision

    async def apply(self, decision: MembershipDecision) -> MembershipReceipt:
        # Revalidate even model_copy/model_construct input; neither is validation.
        decision = MembershipDecision.model_validate_json(decision.model_dump_json())
        payload = decision.model_dump_json()
        try:
            async with self._transaction() as conn:
                async with conn.execute(
                    "SELECT revision, command_json FROM membership_decisions WHERE decision_id = ?",
                    (decision.decision_id,),
                ) as cur:
                    existing = await cur.fetchone()
                if existing is not None:
                    if MembershipDecision.model_validate_json(existing["command_json"]) != decision:
                        raise MembershipConflict("decision_id_reused_with_different_payload")
                    receipt = MembershipReceipt(
                        decision_id=decision.decision_id,
                        revision=existing["revision"],
                        changed=len(_changed(decision)),
                        routes_changed=len(_route_changes(decision)),
                        replayed=True,
                    )
                else:
                    if decision.version != 2:
                        raise MembershipConflict("old_review_requires_refresh")
                    current = await membership_snapshot(
                        conn,
                        decision.before.scope_home_ids,
                        tuple(
                            m.source_unique_id for m in decision.before.members if m.home_id is None
                        ),
                    )
                    if current != decision.before:
                        raise MembershipConflict("stale_membership_or_personal_state_review")
                    await _validate_operation(conn, decision)
                    receipt = await write_membership_decision(
                        conn,
                        decision_id=decision.decision_id,
                        operation=decision.operation,
                        actor=decision.actor,
                        reason=decision.reason,
                        evidence=decision.evidence,
                        expected_revision=decision.before.revision,
                        command_json=payload,
                        changes=_changed(decision),
                        route_changes=_route_changes(decision),
                        reverses=decision.reverses,
                        invalidation_home_ids=decision.before.scope_home_ids,
                    )
        except (MembershipConflict, HomeRouteConflict) as exc:
            logger.warning(
                "membership_decision_refused", decision_id=decision.decision_id, reason=str(exc)
            )
            raise
        except BaseException as exc:
            logger.error(
                "membership_decision_interrupted",
                decision_id=decision.decision_id,
                error_type=type(exc).__name__,
                recovery="inspect_decision_id_before_retry",
            )
            raise
        logger.info(
            "membership_decision_replayed" if receipt.replayed else "membership_decision_committed",
            decision_id=receipt.decision_id,
            revision=receipt.revision,
            operation=decision.operation,
            changed=receipt.changed,
            routes_changed=receipt.routes_changed,
            scoped_homes=len(decision.before.scope_home_ids),
            status_conflict=decision.before.has_status_conflict,
            message_conflict=decision.before.has_message_conflict,
        )
        return receipt
