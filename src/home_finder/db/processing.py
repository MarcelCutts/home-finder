"""Narrow recovery grants for retained, unassessed homes."""

from datetime import UTC, datetime

import aiosqlite


async def request_enrichment_recovery(
    conn: aiosqlite.Connection, property_ids: list[str], *, reason: str
) -> dict[str, str]:
    """Grant one enrichment outcome without resetting any accumulated attempts.

    The caller owns the transaction and runner fence. A fresh accepted source
    membership can request this in the same transaction as the merge.
    """
    ids = list(dict.fromkeys(property_ids))
    if not 1 <= len(ids) <= 20:
        raise ValueError("Select between 1 and 20 property IDs for recovery")
    if not reason.strip() or len(reason) > 200:
        raise ValueError("Recovery needs a nonempty reason of at most 200 characters")
    outcomes: dict[str, str] = {}
    for uid in ids:
        cursor = await conn.execute("SELECT * FROM properties WHERE unique_id=?", (uid,))
        row = await cursor.fetchone()
        if row is None:
            outcomes[uid] = "not_found"
            continue
        if row["analysis_exhausted_reason"] or (row["analysis_attempts"] or 0) >= 3:
            outcomes[uid] = "analysis_exhausted"
            continue
        if row["enrichment_recovery_requested_at"]:
            outcomes[uid] = "already_requested"
            continue
        if row["notification_status"] != "dropped" and not (
            row["notification_status"] == "pending_enrichment"
            and row["enrichment_status"] == "failed"
        ):
            outcomes[uid] = "not_stranded"
            continue
        cursor = await conn.execute(
            "SELECT 1 FROM quality_analyses WHERE property_unique_id=?", (uid,)
        )
        if await cursor.fetchone():
            outcomes[uid] = "has_assessment"
            continue
        cursor = await conn.execute(
            "SELECT 1 FROM notification_deliveries WHERE property_id=?", (uid,)
        )
        if row["notified_at"] or await cursor.fetchone():
            outcomes[uid] = "delivery_history"
            continue
        await conn.execute(
            """UPDATE properties
               SET notification_status='pending_enrichment', enrichment_status='pending',
                   enrichment_recovery_requested_at=?, enrichment_recovery_reason=?
               WHERE unique_id=?""",
            (datetime.now(UTC).isoformat(), reason, uid),
        )
        outcomes[uid] = "requested"
    return outcomes
