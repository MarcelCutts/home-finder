"""Small durable delivery records on the same authority as property state."""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import aiosqlite

from home_finder.db.row_mappers import row_to_merged_property
from home_finder.filters.notification_policy import (
    select_discovery,
    select_price_drop,
    stopped_reason,
)
from home_finder.models import PropertyImage, PropertyQualityAnalysis
from home_finder.models.delivery import DeliveryPlan, DiscoverySnapshot, UnfinishedDeliveryState
from home_finder.models.notifications import TRACKED_USER_STATUSES, NotificationPolicy

ConnectionScope = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]


class DeliveryStateError(ValueError):
    """No safe automatic transition exists for this delivery."""


async def discovery_snapshot(conn: aiosqlite.Connection, uid: str) -> str:
    async with conn.execute("SELECT * FROM properties WHERE unique_id=?", (uid,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise DeliveryStateError("Missing property")

    async def images(property_id: str) -> list[PropertyImage]:
        async with conn.execute(
            """SELECT source,url,image_type,floorplan_origin FROM property_images
               WHERE property_unique_id=? ORDER BY image_type,id""",
            (property_id,),
        ) as cur:
            return [PropertyImage.model_validate(dict(r)) for r in await cur.fetchall()]

    merged = await row_to_merged_property(row, get_property_images=images)
    async with conn.execute(
        "SELECT analysis_json FROM quality_analyses WHERE property_unique_id=?", (uid,)
    ) as cur:
        analysis = await cur.fetchone()
    return DiscoverySnapshot(
        merged=merged,
        commute_minutes=row["commute_minutes"],
        transport_mode=row["transport_mode"],
        quality_analysis=(
            PropertyQualityAnalysis.model_validate_json(analysis[0])
            if analysis and analysis[0]
            else None
        ),
    ).model_dump_json()


async def enqueue_discovery(
    conn: aiosqlite.Connection,
    uid: str,
    *,
    policy: NotificationPolicy | None = None,
) -> None:
    """Select once in the same transaction as the assessment/status transition.

    Reanalysis or configuration changes cannot replay a previously decided event.
    Existing plans, outcomes and uncertainty remain authoritative.
    """
    key = f"discovery:{uid}"
    async with conn.execute(
        "SELECT state FROM notification_deliveries WHERE event_key=?", (key,)
    ) as cur:
        existing = await cur.fetchone()
    if existing is None:
        async with conn.execute(
            """SELECT p.bedrooms,p.postcode,p.user_status,p.is_off_market,q.analysis_json
               FROM properties p LEFT JOIN quality_analyses q ON q.property_unique_id=p.unique_id
               WHERE p.unique_id=?""",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise DeliveryStateError("Missing property")
        try:
            analysis = json.loads(row["analysis_json"]) if row["analysis_json"] else None
        except json.JSONDecodeError:
            analysis = None
        decision = select_discovery(
            analysis,
            row["bedrooms"],
            postcode=row["postcode"],
            user_status=row["user_status"],
            is_off_market=bool(row["is_off_market"]),
            policy=policy or NotificationPolicy(),
        )
        alert = decision.disposition == "alert"
        state = "queued" if alert else "suppressed"
        await conn.execute(
            """INSERT INTO notification_deliveries
               (event_key,property_id,kind,state,reason,snapshot_json,selection_json)
               VALUES (?,?,'discovery',?,?,?,?)""",
            (
                key,
                uid,
                state,
                None if alert else decision.reason,
                await discovery_snapshot(conn, uid) if alert else None,
                decision.model_dump_json(),
            ),
        )
    else:
        state = existing["state"]
    if state == "suppressed":
        await conn.execute(
            """UPDATE properties SET notification_status='dashboard_only'
               WHERE unique_id=? AND notification_status IN ('pending','failed')""",
            (uid,),
        )


async def _price_context(conn: aiosqlite.Connection, uid: str) -> aiosqlite.Row | None:
    async with conn.execute(
        """SELECT p.*, (p.notification_status='sent' OR EXISTS (
               SELECT 1 FROM notification_deliveries d
               WHERE d.event_key='discovery:'||p.unique_id AND d.snapshot_json IS NOT NULL
                 AND d.state NOT IN ('review','suppressed')
           )) AS has_discovery_interest FROM properties p WHERE p.unique_id=?""",
        (uid,),
    ) as cur:
        return await cur.fetchone()


async def enqueue_price_drop(
    conn: aiosqlite.Connection, uid: str, history_id: int, old_price: int, new_price: int
) -> None:
    """Record selection atomically with its price revision, independently of fit."""
    row = await _price_context(conn, uid)
    if row is None:
        raise DeliveryStateError("Missing property")
    decision = select_price_drop(
        user_status=row["user_status"],
        is_off_market=bool(row["is_off_market"]),
        has_discovery_interest=bool(row["has_discovery_interest"]),
    )
    alert = decision.disposition == "alert"
    snapshot = {
        "title": row["title"],
        "postcode": row["postcode"] or "",
        "old_price": old_price,
        "new_price": new_price,
        "unique_id": uid,
        "first_seen": row["first_seen"],
    }
    await conn.execute(
        """INSERT INTO notification_deliveries
           (event_key,property_id,kind,price_history_id,state,reason,snapshot_json,selection_json)
           VALUES (?,?,'price_drop',?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING""",
        (
            f"price:{history_id}",
            uid,
            history_id,
            "queued" if alert else "suppressed",
            None if alert else decision.reason,
            json.dumps(snapshot),
            decision.model_dump_json(),
        ),
    )


async def record_legacy_deliveries(conn: aiosqlite.Connection) -> None:
    """Untracked previous sends have unknown outcomes, not proof of non-delivery."""
    await conn.execute("""INSERT INTO notification_deliveries
        (event_key,property_id,kind,state,reason)
        SELECT 'discovery:'||unique_id, unique_id, 'discovery','review','legacy_untracked'
        FROM properties WHERE notification_status IN ('pending','failed')
        ON CONFLICT(event_key) DO NOTHING""")
    await conn.execute("""INSERT INTO notification_deliveries
        (event_key,property_id,kind,price_history_id,state,reason,snapshot_json)
        SELECT 'price:'||ph.id, p.unique_id, 'price_drop', ph.id, 'review','legacy_untracked',
            json_object('title',p.title,'postcode',COALESCE(p.postcode,''),
                'old_price',ph.old_price,'new_price',ph.new_price,'unique_id',p.unique_id,
                'first_seen',p.first_seen)
        FROM properties p JOIN price_history ph ON ph.property_unique_id=p.unique_id
        WHERE p.notification_status='sent' AND p.price_drop_notified=0
          AND ph.change_amount<0
          AND ph.id=(SELECT MAX(id) FROM price_history WHERE property_unique_id=p.unique_id)
        ON CONFLICT(event_key) DO NOTHING""")


async def delivery_report(
    conn: aiosqlite.Connection, *, state: UnfinishedDeliveryState | None = None
) -> dict[str, Any]:
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_deliveries'"
    ) as cur:
        if await cur.fetchone() is None:
            return {"available": False}
    async with conn.execute(
        "SELECT state,COUNT(*) AS count FROM notification_deliveries GROUP BY state"
    ) as cur:
        counts = {r["state"]: r["count"] for r in await cur.fetchall()}
    async with conn.execute(
        """SELECT event_key,property_id,kind,state,next_part,receipts_json,
            reason,retry_at,created_at,updated_at,plan_sha256,resolution_ref,
            json_array_length(plan_json, '$.parts') AS total_parts
        FROM notification_deliveries WHERE state NOT IN ('complete','suppressed')
            AND (? IS NULL OR state=?)
        ORDER BY created_at,event_key LIMIT 100""",
        (state, state),
    ) as cur:
        records = [dict(r) for r in await cur.fetchall()]
    for record in records:
        record["message_ids"] = json.loads(record.pop("receipts_json"))
    return {"available": True, "counts": counts, "unfinished": records, "limit": 100}


class DeliveryRepository:
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

    async def get(self, key: str) -> dict[str, Any] | None:
        async with (
            self._read_connection() as conn,
            conn.execute("SELECT * FROM notification_deliveries WHERE event_key=?", (key,)) as cur,
        ):
            row = await cur.fetchone()
            return dict(row) if row else None

    async def recover(self) -> None:
        """Only at runner startup while holding the application runner lock."""
        async with self._transaction() as conn:
            await record_legacy_deliveries(conn)
            await conn.execute("""UPDATE notification_deliveries SET state='uncertain',
                reason='interrupted_dispatch',updated_at=CURRENT_TIMESTAMP WHERE state='sending'""")
            await conn.execute("""UPDATE notification_deliveries SET state='suppressed',
                reason='already_sent',updated_at=CURRENT_TIMESTAMP
                WHERE kind='discovery' AND next_part=0 AND state IN ('queued','ready','waiting')
                  AND property_id IN (SELECT unique_id FROM properties
                                      WHERE notification_status='sent')""")

    async def pending(self) -> list[str]:
        async with (
            self._read_connection() as conn,
            conn.execute("""SELECT event_key FROM notification_deliveries
                WHERE state IN ('queued','ready','waiting')
                ORDER BY created_at,event_key""") as cur,
        ):
            return [r[0] for r in await cur.fetchall()]

    async def prepare(self, key: str, recipient: str, plan: DeliveryPlan) -> None:
        body = plan.model_dump_json()
        async with self._transaction() as conn:
            await conn.execute(
                """UPDATE notification_deliveries SET plan_json=?,plan_sha256=?,
                recipient=?,state='ready',reason=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE event_key=? AND state='queued' AND plan_json IS NULL""",
                (body, hashlib.sha256(body.encode()).hexdigest(), recipient, key),
            )

    async def refresh_missing_images(self, key: str) -> None:
        """Before a plan exists, newly cached/enriched media can unblock initial delivery."""
        async with self._transaction() as conn:
            async with conn.execute(
                """SELECT property_id FROM notification_deliveries WHERE event_key=?
                   AND state='queued' AND reason='no_images' AND plan_json IS NULL
                   AND kind='discovery'""",
                (key,),
            ) as cur:
                row = await cur.fetchone()
            if row:
                await conn.execute(
                    "UPDATE notification_deliveries SET snapshot_json=? WHERE event_key=?",
                    (await discovery_snapshot(conn, row[0]), key),
                )

    async def claim(self, key: str, recipient: str, index: int) -> bool:
        now = self._clock().astimezone(UTC).isoformat()
        async with self._transaction() as conn:
            if await self._suppress_if_stopped(conn, key):
                return False
            # A flood-control delay survives a process restart and gates this recipient.
            async with conn.execute(
                """SELECT 1 FROM notification_deliveries
                WHERE recipient=? AND retry_at>? LIMIT 1""",
                (recipient, now),
            ) as cur:
                if await cur.fetchone():
                    return False
            tracked = sorted(TRACKED_USER_STATUSES)
            placeholders = ",".join("?" for _ in tracked)
            cur = await conn.execute(
                f"""UPDATE notification_deliveries SET state='sending',
                    reason=NULL,retry_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE event_key=? AND recipient=? AND next_part=?
                  AND state IN ('ready','waiting') AND (retry_at IS NULL OR retry_at<=?)
                  AND EXISTS (SELECT 1 FROM properties p
                    WHERE p.unique_id=notification_deliveries.property_id
                      AND ((kind='discovery' AND p.notification_status IN ('pending','failed'))
                           OR (kind='price_drop' AND (p.notification_status='sent'
                               OR p.user_status IN ({placeholders})))))""",
                (key, recipient, index, now, *tracked),
            )
            return cur.rowcount == 1

    async def suppress_if_stopped(self, key: str) -> bool:
        """Stop known unsent work; never reinterpret an in-flight/uncertain request."""
        async with self._transaction() as conn:
            return await self._suppress_if_stopped(conn, key)

    async def _suppress_if_stopped(self, conn: aiosqlite.Connection, key: str) -> bool:
        async with conn.execute(
            """SELECT property_id,kind FROM notification_deliveries WHERE event_key=?
               AND state IN ('queued','ready','waiting','blocked')""",
            (key,),
        ) as cur:
            delivery = await cur.fetchone()
        if delivery is None:
            return False
        row = await _price_context(conn, delivery["property_id"])
        if row is None:
            return False
        reason = stopped_reason(row["user_status"], bool(row["is_off_market"]))
        if delivery["kind"] == "price_drop":
            decision = select_price_drop(
                user_status=row["user_status"],
                is_off_market=bool(row["is_off_market"]),
                has_discovery_interest=bool(row["has_discovery_interest"]),
            )
            reason = decision.reason if decision.disposition == "dashboard_only" else None
        if reason is None:
            return False
        # Suppression stops this alert, but Telegram's saved delay still gates
        # every other alert to the same recipient until the original expiry.
        await conn.execute(
            """UPDATE notification_deliveries SET state='suppressed',reason=?,
               updated_at=CURRENT_TIMESTAMP WHERE event_key=?""",
            (reason, key),
        )
        if delivery["kind"] == "discovery":
            await conn.execute(
                """UPDATE properties SET notification_status='dashboard_only'
                   WHERE unique_id=? AND notification_status IN ('pending','failed')""",
                (delivery["property_id"],),
            )
        return True

    async def fail(
        self,
        key: str,
        index: int,
        state: Literal["waiting", "uncertain", "blocked"],
        reason: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        retry_at = (self._clock() + timedelta(seconds=max(1, retry_after or 1))).isoformat()
        async with self._transaction() as conn:
            cur = await conn.execute(
                """UPDATE notification_deliveries SET state=?,reason=?,
                retry_at=?,updated_at=CURRENT_TIMESTAMP
                WHERE event_key=? AND next_part=? AND state='sending'""",
                (state, reason, retry_at if state == "waiting" else None, key, index),
            )
            if cur.rowcount != 1:
                raise DeliveryStateError("Delivery is not in flight")
            if state != "waiting":
                await conn.execute(
                    """UPDATE properties SET notification_status='failed'
                       WHERE unique_id=(SELECT property_id FROM notification_deliveries
                           WHERE event_key=? AND kind='discovery')
                         AND notification_status='pending'""",
                    (key,),
                )

    async def defer(self, key: str, reason: str, *, blocked: bool = False) -> None:
        """Record a pre-dispatch problem without altering any in-flight outcome."""
        async with self._transaction() as conn:
            await conn.execute(
                """UPDATE notification_deliveries SET reason=?,
                   state=CASE WHEN ? THEN 'blocked' ELSE state END,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE event_key=? AND state IN ('queued','ready','waiting')""",
                (reason, blocked, key),
            )

    async def acknowledge(self, key: str, index: int, ids: list[int]) -> None:
        async with self._transaction() as conn:
            await self._acknowledge(conn, key, index, ids)

    async def _acknowledge(
        self,
        conn: aiosqlite.Connection,
        key: str,
        index: int,
        ids: list[int],
        *,
        operator: bool = False,
    ) -> None:
        async with conn.execute(
            "SELECT * FROM notification_deliveries WHERE event_key=?", (key,)
        ) as cur:
            row = await cur.fetchone()
        if (
            row is None
            or row["next_part"] != index
            or row["state"] not in ({"sending", "uncertain"} if operator else {"sending"})
        ):
            raise DeliveryStateError("Receipt does not match the current part")
        plan = self.plan(dict(row))
        if (
            len(ids) != plan.parts[index].receipt_count
            or len(set(ids)) != len(ids)
            or any(type(i) is not int or i <= 0 for i in ids)
        ):
            raise DeliveryStateError("Incomplete or invalid message receipts")
        receipts = json.loads(row["receipts_json"])
        if set(ids).intersection(i for group in receipts for i in group):
            raise DeliveryStateError("Receipt already belongs to an earlier part")
        receipts.append(ids)
        complete = index + 1 == len(plan.parts)
        await conn.execute(
            """UPDATE notification_deliveries SET receipts_json=?, next_part=?,
            state=?,reason=NULL,retry_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE event_key=?""",
            (json.dumps(receipts), index + 1, "complete" if complete else "ready", key),
        )
        if complete:
            if row["kind"] == "discovery":
                await conn.execute(
                    """UPDATE properties SET notification_status='sent',notified_at=?
                    WHERE unique_id=? AND notification_status IN ('pending','failed')""",
                    (self._clock().isoformat(), row["property_id"]),
                )
            else:
                # Completing an older alert cannot consume a newer price revision.
                await conn.execute(
                    """UPDATE properties SET price_drop_notified=1 WHERE unique_id=?
                    AND ?=(SELECT MAX(id) FROM price_history WHERE property_unique_id=?)""",
                    (row["property_id"], row["price_history_id"], row["property_id"]),
                )

    @staticmethod
    def plan(row: dict[str, Any]) -> DeliveryPlan:
        body = row["plan_json"]
        if (
            not isinstance(body, str)
            or hashlib.sha256(body.encode()).hexdigest() != row["plan_sha256"]
        ):
            raise DeliveryStateError("Invalid delivery plan digest")
        return DeliveryPlan.model_validate_json(body)

    async def report(self, *, state: UnfinishedDeliveryState | None = None) -> dict[str, Any]:
        async with self._read_connection() as conn:
            return await delivery_report(conn, state=state)

    async def resolve(
        self,
        key: str,
        action: Literal["retry", "suppress", "acknowledge"],
        evidence_ref: str,
        *,
        message_ids: list[int] | None = None,
    ) -> None:
        """Explicit local operator decision; caller must hold the runner lock."""
        if not evidence_ref or len(evidence_ref) > 200 or any(ord(c) < 32 for c in evidence_ref):
            raise DeliveryStateError("Provide a short non-secret evidence reference")
        async with self._transaction() as conn:
            async with conn.execute(
                "SELECT * FROM notification_deliveries WHERE event_key=?", (key,)
            ) as cur:
                row = await cur.fetchone()
            if row is None or row["state"] not in {"review", "blocked", "uncertain", "sending"}:
                raise DeliveryStateError("This delivery does not need operator reconciliation")
            if action == "acknowledge":
                await self._acknowledge(
                    conn, key, row["next_part"], message_ids or [], operator=True
                )
            elif action == "suppress":
                await conn.execute(
                    """UPDATE notification_deliveries SET state='suppressed',
                    reason='operator_suppressed',updated_at=CURRENT_TIMESTAMP WHERE event_key=?""",
                    (key,),
                )
            else:
                snapshot = row["snapshot_json"]
                if snapshot is None and row["kind"] == "discovery":
                    snapshot = await discovery_snapshot(conn, row["property_id"])
                await conn.execute(
                    """UPDATE notification_deliveries SET state=?,snapshot_json=?,
                    reason=NULL,retry_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE event_key=?""",
                    ("ready" if row["plan_json"] else "queued", snapshot, key),
                )
            await conn.execute(
                "UPDATE notification_deliveries SET resolution_ref=? WHERE event_key=?",
                (evidence_ref, key),
            )
