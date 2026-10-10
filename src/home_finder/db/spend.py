"""Atomic paid-attempt admission and durable reservations on one SQLite authority."""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import aiosqlite

from home_finder.logging import get_logger
from home_finder.models.spend import (
    AttemptSpec,
    ConfirmedCost,
    ProviderRejection,
    SpendPolicy,
    UsageSettlement,
    VisualCheckpointWrite,
    nanousd,
    rate_card_for,
    usd_text,
)
from home_finder.models.spend import (
    SpendDeferred as SpendDeferred,
)

ConnectionScope = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]
_HELD = "('reserved', 'dispatched', 'unknown')"
logger = get_logger(__name__)


class AttemptTransitionError(Exception):
    """A stale/duplicate caller cannot dispatch or overwrite a paid attempt."""


def utc_stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Spending timestamps require a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


async def spend_report(conn: aiosqlite.Connection, now: datetime) -> dict[str, Any]:
    """Read a consistent report inside the caller's existing database scope."""
    now = now.astimezone(UTC) if now.tzinfo is not None else now
    timestamp = utc_stamp(now)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paid_attempts'"
    ) as cursor:
        if await cursor.fetchone() is None:
            return {"ledger_available": False, "as_of": timestamp, "historical_cost": "unknown"}
    async with conn.execute(
        f"""SELECT
            COUNT(*) AS attempts,
            COALESCE(SUM(CASE WHEN state IN {_HELD} THEN reserved_nanousd ELSE 0 END), 0)
                AS held_nanousd,
            COALESCE(SUM(CASE WHEN state IN ('reserved', 'dispatched') THEN 1 ELSE 0 END), 0)
                AS in_flight,
            COALESCE(SUM(CASE WHEN state = 'settled' THEN accounted_nanousd ELSE 0 END), 0)
                AS lifetime_accounted_nanousd,
            COALESCE(SUM(CASE WHEN cost_basis = 'provider_usage'
                THEN accounted_nanousd ELSE 0 END), 0) AS lifetime_usage_estimate_nanousd,
            COALESCE(SUM(CASE WHEN cost_basis = 'operator_confirmed'
                THEN accounted_nanousd ELSE 0 END), 0) AS lifetime_confirmed_nanousd,
            COALESCE(SUM(CASE WHEN rejection_json IS NOT NULL THEN 1 ELSE 0 END), 0)
                AS provider_rejections,
            COALESCE(SUM(CASE WHEN accounted_nanousd > reserved_nanousd THEN 1 ELSE 0 END), 0)
                AS over_reservation_count,
            COALESCE(SUM(CASE WHEN state = 'settled' AND admitted_at >= ? AND admitted_at < ?
                THEN accounted_nanousd ELSE 0 END), 0) AS day_accounted_nanousd,
            COALESCE(SUM(CASE WHEN state = 'settled' AND admitted_at >= ? AND admitted_at < ?
                THEN accounted_nanousd ELSE 0 END), 0) AS month_accounted_nanousd
            FROM paid_attempts""",
        (
            utc_stamp(day_start),
            utc_stamp(day_start + timedelta(days=1)),
            utc_stamp(month_start),
            utc_stamp(next_month),
        ),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    report: dict[str, Any] = dict(row)
    report.update(ledger_available=True, as_of=timestamp, currency="USD")
    # Old unresolved reservations carry into every new budget window.
    report["day_committed_nanousd"] = report["day_accounted_nanousd"] + report["held_nanousd"]
    report["month_committed_nanousd"] = report["month_accounted_nanousd"] + report["held_nanousd"]
    for key, value in list(report.items()):
        if key.endswith("_nanousd"):
            report[key.removesuffix("_nanousd") + "_usd"] = usd_text(value)
    async with conn.execute(
        f"""SELECT attempt_id, phase, state, admitted_at, dispatched_at, result,
            request_id, reserved_nanousd FROM paid_attempts
            WHERE state IN {_HELD} ORDER BY admitted_at, attempt_id"""
    ) as cursor:
        report["unresolved"] = [dict(row) for row in await cursor.fetchall()]
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_checkpoints'"
    ) as cursor:
        checkpoints_available = await cursor.fetchone() is not None
    report["visual_checkpoints"] = []
    if checkpoints_available:
        async with conn.execute(
            """SELECT property_id, input_sha256, attempt_id, created_at
               FROM visual_checkpoints ORDER BY created_at, property_id"""
        ) as cursor:
            report["visual_checkpoints"] = [dict(row) for row in await cursor.fetchall()]
    return report


class SpendRepository:
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

    async def admit(self, spec: AttemptSpec, policy: SpendPolicy) -> str:
        """Reserve and record exactly one attempt before any network dispatch."""
        rates = rate_card_for(spec.phase_config.model)
        reserve = rates.reservation(spec.phase_config)
        if reserve > nanousd(policy.per_call_usd):
            raise SpendDeferred("per_call_limit")
        attempt_id = uuid4().hex
        async with self._transaction() as conn:
            now = self._clock()
            async with conn.execute(
                f"SELECT 1 FROM paid_attempts WHERE operation_key = ? AND state IN {_HELD}",
                (spec.operation_key,),
            ) as cursor:
                if await cursor.fetchone():
                    raise SpendDeferred("operation_unresolved")
            async with conn.execute(
                "SELECT COUNT(*) FROM paid_attempts "
                "WHERE operation_key = ? AND state != 'released' "
                "AND NOT (state = 'settled' AND cost_basis = 'provider_error' "
                "AND accounted_nanousd = 0)",
                (spec.operation_key,),
            ) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                attempts = row[0]
            # Uncharged provider rejections remain audited, but cannot use up
            # paid retries. Unknown outcomes and invalid billed output count.
            if attempts >= policy.max_attempts_per_operation:
                raise SpendDeferred("attempt_limit")
            report = await spend_report(conn, now)
            if report["over_reservation_count"]:
                raise SpendDeferred("reservation_exceeded")
            if (
                policy.max_total_attempts is not None
                and report["attempts"] >= policy.max_total_attempts
            ):
                raise SpendDeferred("total_attempt_limit")
            if policy.lifetime_usd is not None and (
                report["lifetime_accounted_nanousd"] + report["held_nanousd"] + reserve
                > nanousd(policy.lifetime_usd)
            ):
                raise SpendDeferred("lifetime_limit")
            if report["in_flight"] >= policy.max_in_flight:
                raise SpendDeferred("concurrency_limit")
            if report["day_committed_nanousd"] + reserve > nanousd(policy.daily_usd):
                raise SpendDeferred("daily_limit")
            if report["month_committed_nanousd"] + reserve > nanousd(policy.monthly_usd):
                raise SpendDeferred("monthly_limit")
            await conn.execute(
                """INSERT INTO paid_attempts (
                    attempt_id, operation_key, property_id, phase, provider, model,
                    request_sha256, provenance_json, configuration_sha256, rate_version,
                    rates_json, policy_json, admitted_at, state, reserved_nanousd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?)""",
                (
                    attempt_id,
                    spec.operation_key,
                    spec.property_id,
                    spec.phase,
                    spec.phase_config.provider,
                    spec.phase_config.model,
                    spec.request_sha256,
                    spec.provenance.model_dump_json(),
                    spec.provenance.fingerprint,
                    rates.version,
                    rates.model_dump_json(),
                    policy.model_dump_json(),
                    utc_stamp(now),
                    reserve,
                ),
            )
        return attempt_id

    async def mark_dispatched(self, attempt_id: str) -> None:
        """Commit before dispatch; a lost acknowledgement cannot permit redispatch."""
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """UPDATE paid_attempts SET state = 'dispatched', dispatched_at = ?
                    WHERE attempt_id = ? AND state = 'reserved'""",
                (utc_stamp(self._clock()), attempt_id),
            )
            if cursor.rowcount != 1:
                raise AttemptTransitionError("Attempt is not reserved")

    async def release_undispatched(self, attempt_id: str) -> None:
        """Only a reservation proven not dispatched may be released without cost evidence."""
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """UPDATE paid_attempts SET state = 'released', completed_at = ?,
                    result = 'cancelled_before_dispatch', accounted_nanousd = 0
                    WHERE attempt_id = ? AND state = 'reserved'""",
                (utc_stamp(self._clock()), attempt_id),
            )
            if cursor.rowcount != 1:
                raise AttemptTransitionError("Only an undispatched reservation can be released")

    async def mark_unknown(
        self, attempt_id: str, reason: str, *, request_id: str | None = None
    ) -> None:
        if reason not in {"timeout", "cancelled", "connection", "process_exit", "invalid_usage"}:
            raise ValueError("Unsupported uncertainty reason")
        if request_id is not None and len(request_id) > 128:
            raise ValueError("Request ID exceeds metadata limit")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """UPDATE paid_attempts SET state = 'unknown', result = COALESCE(result, ?),
                    request_id = COALESCE(request_id, ?)
                    WHERE attempt_id = ? AND state IN ('dispatched', 'unknown')""",
                (reason, request_id, attempt_id),
            )
            if cursor.rowcount != 1:
                raise AttemptTransitionError("Attempt was not dispatched or is already settled")

    async def settle_usage(
        self,
        attempt_id: str,
        settlement: UsageSettlement,
        *,
        checkpoint: VisualCheckpointWrite | None = None,
    ) -> None:
        """Persist reported units even for rejected output; cost remains an estimate."""
        async with self._transaction() as conn:
            row = await self._attempt(conn, attempt_id)
            payload = settlement.model_dump_json()
            if row["state"] == "settled" and row["settlement_json"] == payload:
                return
            if row["state"] not in {"dispatched", "unknown"}:
                raise AttemptTransitionError("Attempt is not awaiting usage")
            # Read the frozen card, rather than silently repricing old attempts.
            from home_finder.models.spend import RateCard

            rates = RateCard.model_validate_json(row["rates_json"])
            if (rates.provider, rates.model, rates.version) != (
                row["provider"],
                row["model"],
                row["rate_version"],
            ):
                raise ValueError("Frozen rate card does not match the admitted attempt")
            cost = rates.cost(settlement.usage)
            if checkpoint is not None and (
                row["phase"] != "visual" or settlement.result != "success"
            ):
                raise AttemptTransitionError("Only successful visual output can be checkpointed")
            await conn.execute(
                """UPDATE paid_attempts SET state = 'settled', completed_at = ?, result = ?,
                    request_id = ?, stop_reason = ?, settlement_json = ?, accounted_nanousd = ?,
                    cost_basis = 'provider_usage' WHERE attempt_id = ?""",
                (
                    utc_stamp(self._clock()),
                    settlement.result,
                    settlement.request_id,
                    settlement.stop_reason,
                    payload,
                    cost,
                    attempt_id,
                ),
            )
            if checkpoint is not None and cost <= row["reserved_nanousd"]:
                # Keep field order: evaluation renders these observations as
                # text. Reordering on recovery would change its request key and
                # accidentally evade an earlier evaluation hold/attempt limit.
                data_json = json.dumps(checkpoint.data, separators=(",", ":"), allow_nan=False)
                await conn.execute(
                    """INSERT INTO visual_checkpoints (
                        property_id, input_sha256, attempt_id, data_json, data_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(property_id) DO UPDATE SET
                        input_sha256=excluded.input_sha256, attempt_id=excluded.attempt_id,
                        data_json=excluded.data_json, data_sha256=excluded.data_sha256,
                        created_at=excluded.created_at""",
                    (
                        row["property_id"],
                        checkpoint.input_sha256,
                        attempt_id,
                        data_json,
                        hashlib.sha256(data_json.encode()).hexdigest(),
                        utc_stamp(self._clock()),
                    ),
                )

    async def get_visual_checkpoint(
        self, property_id: str, input_sha256: str
    ) -> dict[str, Any] | None:
        """Read a compatible receipt-backed result, without changing any spend hold."""
        async with self._read_connection() as conn:
            cursor = await conn.execute(
                """SELECT c.*, a.phase, a.state, a.result, a.cost_basis,
                          a.property_id as attempt_property_id,
                          a.accounted_nanousd, a.reserved_nanousd
                   FROM visual_checkpoints c LEFT JOIN paid_attempts a USING(attempt_id)
                   WHERE c.property_id=?""",
                (property_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        if row["input_sha256"] != input_sha256:
            logger.info("visual_checkpoint_input_changed", property_id=property_id)
            return None
        valid_receipt = (
            row["state"] == "settled"
            and row["phase"] == "visual"
            and row["result"] == "success"
            and row["cost_basis"] == "provider_usage"
            and row["attempt_property_id"] == property_id
            and row["accounted_nanousd"] <= row["reserved_nanousd"]
        )
        try:
            if not valid_receipt or (
                hashlib.sha256(row["data_json"].encode()).hexdigest() != row["data_sha256"]
            ):
                raise ValueError("Invalid checkpoint receipt or digest")
            data = json.loads(row["data_json"])
            if not isinstance(data, dict):
                raise ValueError("Checkpoint must contain observations")
        except (TypeError, ValueError):
            raise SpendDeferred("visual_checkpoint_invalid") from None
        logger.info(
            "visual_checkpoint_loaded",
            property_id=property_id,
            input_sha256=input_sha256,
            attempt_id=row["attempt_id"],
            created_at=row["created_at"],
        )
        return data

    async def settle_rejection(self, attempt_id: str, rejection: ProviderRejection) -> None:
        """Failed API requests are uncharged; client transport failures are not evidence.

        Caller must verify a complete provider error envelope. HTTP status
        alone, an intermediary's HTML, a timeout or disconnect is insufficient.
        """
        async with self._transaction() as conn:
            row = await self._attempt(conn, attempt_id)
            payload = rejection.model_dump_json()
            if row["state"] == "settled" and row["rejection_json"] == payload:
                return
            if row["state"] not in {"dispatched", "unknown"}:
                raise AttemptTransitionError("Attempt is not awaiting provider evidence")
            await conn.execute(
                """UPDATE paid_attempts SET state = 'settled', completed_at = ?, result = ?,
                    request_id = ?, rejection_json = ?, accounted_nanousd = 0,
                    cost_basis = 'provider_error' WHERE attempt_id = ?""",
                (
                    utc_stamp(self._clock()),
                    rejection.result,
                    rejection.request_id,
                    payload,
                    attempt_id,
                ),
            )

    async def reconcile(self, attempt_id: str, confirmed: ConfirmedCost) -> None:
        """Confirm a charge once, retaining any original provider usage estimate."""
        async with self._transaction() as conn:
            row = await self._attempt(conn, attempt_id)
            payload = confirmed.model_dump_json()
            if row["state"] == "settled" and row["reconciliation_json"] == payload:
                return
            if row["state"] not in {"dispatched", "unknown", "settled"} or (
                row["reconciliation_json"] is not None
            ):
                raise AttemptTransitionError(
                    "Only dispatched attempts without a previous confirmation can be reconciled"
                )
            await conn.execute(
                """UPDATE paid_attempts SET state = 'settled',
                    completed_at = COALESCE(completed_at, ?), reconciled_at = ?,
                    reconciliation_json = ?, accounted_nanousd = ?,
                    cost_basis = 'operator_confirmed', result = COALESCE(result, 'unknown')
                    WHERE attempt_id = ?""",
                (
                    utc_stamp(self._clock()),
                    utc_stamp(self._clock()),
                    payload,
                    nanousd(confirmed.amount_usd),
                    attempt_id,
                ),
            )

    @staticmethod
    async def _attempt(conn: aiosqlite.Connection, attempt_id: str) -> aiosqlite.Row:
        async with conn.execute(
            "SELECT * FROM paid_attempts WHERE attempt_id = ?", (attempt_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise AttemptTransitionError("Unknown attempt")
        return row

    async def report(self) -> dict[str, Any]:
        async with self._read_connection() as conn:
            return await spend_report(conn, self._clock())
