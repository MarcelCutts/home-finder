"""Preservation gates for retiring pending rows during canonical matching.

Established homes need a reversible membership operation. This narrow gate only
permits retirement of staging rows before user, delivery or analysis history exists.
Run it inside the transaction that will change membership and retire those rows.
"""

from collections.abc import Iterable

import aiosqlite

_PROTECTED_CHILDREN = (
    ("property_images", "property_unique_id"),
    ("quality_analyses", "property_unique_id"),
    ("status_events", "property_unique_id"),
    ("viewing_messages", "property_unique_id"),
    ("price_history", "property_unique_id"),
    ("enquiry_log", "property_unique_id"),
    ("source_aliases", "anchor_id"),
    ("paid_attempts", "property_id"),
)


class MergeBlockedError(Exception):
    """A proposed retirement or reassociation requires history-aware review."""

    def __init__(self, blockers: dict[str, list[str]]) -> None:
        self.blockers = blockers
        super().__init__("Merge requires review: " + ", ".join(sorted(blockers)))


async def staging_blockers(
    conn: aiosqlite.Connection, unique_ids: Iterable[str]
) -> dict[str, list[str]]:
    """Inspect live state under the caller's write lock, without modifying it."""
    blockers: dict[str, list[str]] = {}
    scope = set(unique_ids)
    for uid in tuple(scope):
        async with conn.execute(
            "SELECT member.current_property_id FROM homes root "
            "JOIN home_routes r ON r.target_home_id = root.home_id "
            "JOIN homes member ON member.home_id = r.home_id "
            "WHERE root.current_property_id = ? AND member.current_property_id IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM home_routes active WHERE active.home_id = root.home_id "
            "AND active.target_home_id = root.home_id AND active.state = 'active')",
            (uid,),
        ) as cur:
            scope.update(row[0] for row in await cur.fetchall())
    for uid in sorted(scope):
        reasons: list[str] = []
        async with conn.execute("SELECT * FROM properties WHERE unique_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        if row is not None:
            if row["enrichment_status"] != "pending":
                reasons.append("established_property")
            if row["notification_status"] != "pending_enrichment" or row["notified_at"]:
                reasons.append("delivery_state")
            if row["user_status"] != "new":
                reasons.append("user_state")
            if row["analysis_attempts"]:
                reasons.append("analysis_attempts")
        for table, column in _PROTECTED_CHILDREN:
            async with conn.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (uid,)
            ) as cur:
                if await cur.fetchone() is not None:
                    reasons.append(table)
        # A reviewed donor can have no current members and still be the target
        # of a later split or reversal. It is no longer disposable staging.
        async with conn.execute(
            "SELECT 1 FROM membership_projection_invalidations i JOIN homes h USING(home_id) "
            "JOIN membership_decisions d USING(decision_id) "
            "WHERE h.origin_property_id = ? "
            "AND d.operation IN ('merge', 'split', 'reversal') LIMIT 1",
            (uid,),
        ) as cur:
            if await cur.fetchone() is not None:
                reasons.append("reviewed_membership")
        if reasons:
            blockers[uid] = reasons
    return blockers


async def ensure_staging_merge(
    conn: aiosqlite.Connection, anchor_id: str, absorbed_ids: list[str]
) -> None:
    """Reject the whole group before any source link or projection is changed."""
    async with conn.execute(
        "SELECT 1 FROM active_properties WHERE unique_id = ?", (anchor_id,)
    ) as cur:
        if await cur.fetchone() is None:
            raise MergeBlockedError({anchor_id: ["missing_anchor"]})
    blockers = await staging_blockers(conn, absorbed_ids)
    for uid in absorbed_ids:
        async with conn.execute(
            "SELECT merged_id FROM source_listings WHERE unique_id = ?", (uid,)
        ) as cur:
            source = await cur.fetchone()
        if source is None:
            blockers.setdefault(uid, []).append("missing_source_listing")
        elif source["merged_id"] not in {None, anchor_id, *absorbed_ids}:
            blockers.setdefault(uid, []).append("source_owned_by_other_canonical")
    if blockers:
        raise MergeBlockedError(blockers)
