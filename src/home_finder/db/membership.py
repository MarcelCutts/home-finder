"""Reviewed, reversible source membership with retained home and user provenance.

Reviewed commands are an operator API, not a fuzzy matcher or a dashboard
projection rebuild. They retain both original home records and issued links.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import aiosqlite

from home_finder.db.membership_ops import (
    MembershipConflict,
    membership_revision,
    write_membership_decision,
)
from home_finder.db.observations import ConnectionScope
from home_finder.logging import get_logger
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
    conn: aiosqlite.Connection, home_ids: tuple[str, ...]
) -> MembershipSnapshot:
    """Include every source representation and each original personal-state owner."""
    scope = tuple(sorted(set(home_ids)))
    if not scope:
        raise MembershipConflict("empty_home_scope")
    members: list[MemberAssignment] = []
    evidence_hash = hashlib.sha256()
    provenance = set(scope)
    for home_id in scope:
        async with conn.execute("SELECT 1 FROM homes WHERE home_id = ?", (home_id,)) as cur:
            if await cur.fetchone() is None:
                raise MembershipConflict("unknown_home")
        async with conn.execute(
            "SELECT s.*, m.home_id, o.home_id AS origin_home_id "
            "FROM source_memberships m JOIN source_listings s ON s.unique_id = m.source_unique_id "
            "LEFT JOIN membership_origins o USING(source_unique_id) WHERE m.home_id = ? "
            "ORDER BY s.unique_id",
            (home_id,),
        ) as cur:
            for member in await cur.fetchall():
                source_id = member["unique_id"]
                members.append(
                    MemberAssignment(source_unique_id=source_id, home_id=member["home_id"])
                )
                if member["origin_home_id"]:
                    provenance.add(member["origin_home_id"])
                evidence_hash.update(json.dumps(dict(member), sort_keys=True).encode())
                # A new receipt or changed persisted source makes old evidence
                # review stale even when its ownership has not changed.
                for table, key in (
                    ("source_observations", "observation_id"),
                    ("source_identity_claims", "claim_id"),
                ):
                    async with conn.execute(
                        f"SELECT {key} FROM {table} WHERE source_unique_id = ? ORDER BY {key}",
                        (source_id,),
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
    )


def _changed(decision: MembershipDecision) -> dict[str, tuple[str | None, str | None]]:
    return {
        old.source_unique_id: (old.home_id, new.home_id)
        for old, new in zip(decision.before.members, decision.after, strict=True)
        if old.home_id != new.home_id
    }


async def _reversal_map(conn: aiosqlite.Connection, decision_id: str) -> dict[str, str | None]:
    async with conn.execute(
        "SELECT revision, operation FROM membership_decisions WHERE decision_id = ?", (decision_id,)
    ) as cur:
        decision = await cur.fetchone()
    if decision is None or decision["operation"] not in {"merge", "split", "reversal"}:
        raise MembershipConflict("only_reviewed_decisions_are_reversible")
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
        row["revision"] != decision["revision"] or row["home_id"] != row["next_home_id"]
        for row in rows
    ):
        raise MembershipConflict("later_decision_touched_membership")
    return {row["source_unique_id"]: row["previous_home_id"] for row in rows}


async def _validate_operation(conn: aiosqlite.Connection, decision: MembershipDecision) -> None:
    changes = _changed(decision)
    if decision.operation == "merge":
        if (
            len({m.home_id for m in decision.before.members}) < 2
            or len({m.home_id for m in decision.after}) != 1
        ):
            raise MembershipConflict("merge_requires_complete_homes_and_one_destination")
    elif decision.operation == "split":
        origins = {old for old, _ in changes.values()}
        if len(origins) != 1 or not origins <= {m.home_id for m in decision.after}:
            raise MembershipConflict("split_must_retain_members_in_its_original_home")
    else:
        assert decision.reverses is not None
        expected = await _reversal_map(conn, decision.reverses)
        if {source: new for source, (_, new) in changes.items()} != expected:
            raise MembershipConflict("reversal_must_restore_the_recorded_map")
    for state in decision.before.personal_states:
        if state.home_id in decision.before.scope_home_ids and state.property_id is None:
            raise MembershipConflict("unavailable_destination_home")


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
                decision_id="md_" + uuid4().hex,
                operation="merge",
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
        actor: str,
        reason: str,
        evidence: tuple[str, ...],
    ) -> MembershipDecision:
        async with self._transaction() as conn:
            before = await membership_snapshot(conn, (source_home_id, *destinations.values()))
            eligible = {m.source_unique_id for m in before.members if m.home_id == source_home_id}
            if not destinations or not destinations.keys() <= eligible:
                raise MembershipConflict("split_members_must_belong_to_the_source_home")
            decision = MembershipDecision(
                decision_id="md_" + uuid4().hex,
                operation="split",
                actor=actor,
                reason=reason,
                evidence=evidence,
                before=before,
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

    async def prepare_reversal(
        self,
        decision_id: str,
        *,
        actor: str,
        reason: str,
        evidence: tuple[str, ...],
    ) -> MembershipDecision:
        async with self._transaction() as conn:
            destinations = await _reversal_map(conn, decision_id)
            async with conn.execute(
                "SELECT command_json FROM membership_decisions WHERE decision_id = ?",
                (decision_id,),
            ) as cur:
                row = await cur.fetchone()
            assert row is not None
            original = MembershipDecision.model_validate_json(row[0])
            before = await membership_snapshot(conn, original.before.scope_home_ids)
            decision = MembershipDecision(
                decision_id="md_" + uuid4().hex,
                operation="reversal",
                actor=actor,
                reason=reason,
                evidence=evidence,
                before=before,
                reverses=decision_id,
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
                    if existing["command_json"] != payload:
                        raise MembershipConflict("decision_id_reused_with_different_payload")
                    receipt = MembershipReceipt(
                        decision_id=decision.decision_id,
                        revision=existing["revision"],
                        changed=len(_changed(decision)),
                        replayed=True,
                    )
                else:
                    current = await membership_snapshot(conn, decision.before.scope_home_ids)
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
                        reverses=decision.reverses,
                    )
                    await conn.executemany(
                        "INSERT INTO membership_projection_invalidations VALUES (?, ?)",
                        [
                            (decision.decision_id, home_id)
                            for home_id in decision.before.scope_home_ids
                        ],
                    )
        except MembershipConflict as exc:
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
            status_conflict=decision.before.has_status_conflict,
            message_conflict=decision.before.has_message_conflict,
        )
        return receipt
