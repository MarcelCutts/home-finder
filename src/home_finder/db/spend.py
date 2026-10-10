"""Atomic paid-attempt admission and durable reservations on one SQLite authority."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import aiosqlite

from home_finder.models.spend import (
    AttemptSpec,
    ConfirmedCost,
    ProviderRejection,
    SpendPolicy,
    UsageSettlement,
    nanousd,
    rate_card_for,
    usd_text,
)
from home_finder.models.spend import (
    SpendDeferred as SpendDeferred,
)

ConnectionScope = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]
_HELD = "('reserved', 'dispatched', 'unknown')"


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
                "SELECT 1 FROM homes h JOIN home_routes r USING(home_id) "
                "WHERE h.current_property_id = ? AND (r.state != 'active' "
                "OR r.target_home_id != h.home_id)",
                (spec.property_id,),
            ) as cursor:
                if await cursor.fetchone():
                    raise SpendDeferred("inactive_home")
            async with conn.execute(
                f"SELECT 1 FROM paid_attempts WHERE operation_key = ? AND state IN {_HELD}",
                (spec.operation_key,),
            ) as cursor:
                if await cursor.fetchone():
                    raise SpendDeferred("operation_unresolved")
            async with conn.execute(
                "SELECT COUNT(*) FROM paid_attempts "
                "WHERE operation_key = ? AND state != 'released'",
                (spec.operation_key,),
            ) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                attempts = row[0]
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
        inactive = False
        async with self._transaction() as conn:
            async with conn.execute(
                "SELECT 1 FROM paid_attempts a "
                "JOIN homes h ON h.current_property_id = a.property_id "
                "JOIN home_routes r USING(home_id) WHERE a.attempt_id = ? AND a.state = 'reserved' "
                "AND (r.state != 'active' OR r.target_home_id != h.home_id)",
                (attempt_id,),
            ) as cursor:
                inactive = await cursor.fetchone() is not None
            if inactive:
                await conn.execute(
                    "UPDATE paid_attempts SET state = 'released', completed_at = ?, "
                    "result = 'cancelled_before_dispatch', accounted_nanousd = 0 "
                    "WHERE attempt_id = ? AND state = 'reserved'",
                    (utc_stamp(self._clock()), attempt_id),
                )
            else:
                cursor = await conn.execute(
                    "UPDATE paid_attempts SET state = 'dispatched', dispatched_at = ? "
                    "WHERE attempt_id = ? AND state = 'reserved'",
                    (utc_stamp(self._clock()), attempt_id),
                )
                if cursor.rowcount != 1:
                    raise AttemptTransitionError("Attempt is not reserved")
        if inactive:
            raise SpendDeferred("inactive_home")

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

    async def settle_usage(self, attempt_id: str, settlement: UsageSettlement) -> None:
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

            cost = RateCard.model_validate_json(row["rates_json"]).cost(settlement.usage)
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
