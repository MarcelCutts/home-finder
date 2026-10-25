"""Spending admission must survive races, ambiguous outcomes and process exit."""

import asyncio
import json
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.db.spend import AttemptTransitionError, SpendDeferred, SpendRepository
from home_finder.models.spend import (
    SONNET_46_FIVE_MINUTE_RATES,
    SONNET_STANDARD_RATES,
    BillableUsage,
    ConfirmedCost,
    ProviderRejection,
    RateCard,
    UsageSettlement,
    nanousd,
    usd_text,
)
from home_finder.pipeline.spend_report import read_report
from tests.support.spend import policy, settlement, spec


@pytest.fixture
async def in_memory_storage():
    async with PropertyStorage(":memory:") as storage:
        yield storage


def test_rates_use_nonoverlapping_exact_units_and_conservative_bound():
    assert SONNET_STANDARD_RATES.cost(settlement().usage) == 3_547_500
    assert usd_text(3_547_500) == "0.003547500"
    assert nanousd(Decimal("0.000000001")) == 1
    assert SONNET_STANDARD_RATES.reservation(spec().phase_config) == 6_315_000_000
    assert SONNET_STANDARD_RATES.reservation(spec(phase="evaluation").phase_config) == 6_061_440_000
    assert SONNET_46_FIVE_MINUTE_RATES.reservation(spec().phase_config) == 4_065_000_000
    assert (
        SONNET_46_FIVE_MINUTE_RATES.reservation(spec(phase="evaluation").phase_config)
        == 3_811_440_000
    )


async def test_new_reservations_do_not_reprice_existing_attempts(in_memory_storage, monkeypatch):
    repo = in_memory_storage.spend
    with monkeypatch.context() as historical:
        historical.setattr(
            "home_finder.db.spend.rate_card_for", lambda model: SONNET_STANDARD_RATES
        )
        old = await repo.admit(spec("legacy-rate"), policy())
    await repo.mark_dispatched(old)
    await repo.mark_unknown(old, "timeout")
    new = await repo.admit(spec("current-rate"), policy())
    async with in_memory_storage._read_connection() as conn:
        before = dict(await repo._attempt(conn, old))
        current = dict(await repo._attempt(conn, new))
    assert before["rates_json"] == SONNET_STANDARD_RATES.model_dump_json()
    assert before["reserved_nanousd"] == 6_315_000_000
    assert current["rates_json"] == SONNET_46_FIVE_MINUTE_RATES.model_dump_json()
    assert current["reserved_nanousd"] == 4_065_000_000
    assert (await repo.report())["held_nanousd"] == 10_380_000_000
    await repo.settle_usage(old, settlement())
    async with in_memory_storage._read_connection() as conn:
        after = dict(await repo._attempt(conn, old))
    for field in ("rates_json", "rate_version", "reserved_nanousd", "provenance_json"):
        assert before[field] == after[field]
    assert (
        RateCard.model_validate_json(after["rates_json"]).reservation(spec().phase_config)
        == (before["reserved_nanousd"])
    )
    assert after["accounted_nanousd"] == 3_547_500


@pytest.mark.parametrize("bad", [True, 0.1, "NaN", "Infinity", "-1", "0.0000000001", "1000001"])
def test_invalid_or_inexact_caps_rejected(bad):
    with pytest.raises(ValidationError):
        policy(per_call_usd=bad)


@pytest.mark.parametrize("bad", [True, -1, 1.5, "10", 10_000_001])
def test_usage_cannot_silently_coerce_or_default_invalid_units(bad):
    with pytest.raises(ValidationError):
        BillableUsage(input_tokens=bad, output_tokens=0)


@pytest.mark.parametrize("bad", ["0.0000000001", "NaN", "Infinity", "-1"])
def test_money_conversion_never_rounds_a_cap(bad):
    with pytest.raises(ValueError):
        nanousd(Decimal(bad))


async def test_competing_connections_cannot_oversubscribe_daily_limit(tmp_path):
    path = str(tmp_path / "spend.db")
    async with PropertyStorage(path) as a, PropertyStorage(path) as b:
        cap = policy(per_call_usd="4.065", daily_usd="8.13", monthly_usd="8.13", max_in_flight=50)
        results = await asyncio.gather(
            *(db.spend.admit(spec(str(i)), cap) for i, db in enumerate([a, b] * 8)),
            return_exceptions=True,
        )
        accepted = [r for r in results if isinstance(r, str)]
        rejected = [r for r in results if isinstance(r, SpendDeferred)]
        assert len(accepted) == 2 and len(rejected) == 14
        assert all(r.reason == "daily_limit" for r in rejected)
        report = await b.spend.report()
        assert report["attempts"] == 2 and report["in_flight"] == 2
        assert report["day_committed_nanousd"] == 8_130_000_000
        assert report["month_committed_usd"] == "8.130000000"


async def test_concurrent_duplicate_operation_admitted_once(in_memory_storage):
    repo = in_memory_storage.spend
    results = await asyncio.gather(
        *(repo.admit(spec(), policy()) for _ in range(5)), return_exceptions=True
    )
    assert sum(isinstance(r, str) for r in results) == 1
    assert all(r.reason == "operation_unresolved" for r in results if isinstance(r, SpendDeferred))
    assert (await repo.report())["attempts"] == 1


@pytest.mark.parametrize(
    "scenario,reason", [("call", "per_call_limit"), ("inflight", "concurrency_limit")]
)
async def test_admission_limits_leave_no_extra_attempt(in_memory_storage, scenario, reason):
    repo = in_memory_storage.spend
    cap = policy(per_call_usd="1") if scenario == "call" else policy(max_in_flight=1)
    if scenario == "inflight":
        await repo.admit(spec("first"), cap)
    with pytest.raises(SpendDeferred, match=reason):
        await repo.admit(spec("next"), cap)
    assert (await repo.report())["attempts"] == (0 if scenario == "call" else 1)


async def test_monthly_limit_includes_earlier_days(in_memory_storage):
    now = datetime(2026, 9, 1, tzinfo=UTC)
    repo = SpendRepository(
        in_memory_storage._read_connection, in_memory_storage._transaction, clock=lambda: now
    )
    cap = policy(per_call_usd="4.065", daily_usd="4.065", monthly_usd="4.065")
    first = await repo.admit(spec(), cap)
    await repo.mark_dispatched(first)
    await repo.mark_unknown(first, "timeout")
    await repo.reconcile(first, ConfirmedCost(amount_usd="1", evidence_ref="billing:fixture-1"))
    now += timedelta(days=1)
    with pytest.raises(SpendDeferred, match="monthly_limit"):
        await repo.admit(spec("tomorrow"), cap)
    assert (await repo.report())["day_accounted_nanousd"] == 0


async def test_unknown_reservation_carries_across_month_and_requires_reconciliation(
    in_memory_storage,
):
    now = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)
    repo = SpendRepository(
        in_memory_storage._read_connection, in_memory_storage._transaction, clock=lambda: now
    )
    cap = policy(per_call_usd="4.065", daily_usd="4.065", monthly_usd="4.065")
    attempt = await repo.admit(spec(), cap)
    await repo.mark_dispatched(attempt)
    await repo.mark_unknown(attempt, "timeout")
    now += timedelta(seconds=2)
    report = await repo.report()
    assert report["in_flight"] == 0
    assert report["day_committed_nanousd"] == report["month_committed_nanousd"] == 4_065_000_000
    with pytest.raises(SpendDeferred, match="daily_limit"):
        await repo.admit(spec("new-month"), cap)
    with pytest.raises(SpendDeferred, match="operation_unresolved"):
        await repo.admit(spec(), cap)
    with pytest.raises(AttemptTransitionError):
        await repo.release_undispatched(attempt)
    evidence = ConfirmedCost(amount_usd="0.12", evidence_ref="billing:fixture-unknown")
    await repo.reconcile(attempt, evidence)
    await repo.reconcile(attempt, evidence)  # Exact duplicate is safe.
    assert (await repo.report())["day_committed_nanousd"] == 0
    assert (await repo.report())["lifetime_accounted_nanousd"] == 120_000_000
    await repo.admit(spec("new-month"), cap)
    with pytest.raises(AttemptTransitionError):
        await repo.settle_usage(attempt, settlement())


async def test_usage_settlement_is_exact_idempotent_and_bounded_by_attempt_limit(in_memory_storage):
    repo = in_memory_storage.spend
    attempt = await repo.admit(spec(), policy())
    with pytest.raises(AttemptTransitionError):
        await repo.settle_usage(attempt, settlement())
    await repo.mark_dispatched(attempt)
    with pytest.raises(AttemptTransitionError):
        await repo.mark_dispatched(attempt)
    await repo.settle_usage(attempt, settlement())
    await repo.settle_usage(attempt, settlement())
    report = await repo.report()
    assert report["held_nanousd"] == 0 and report["day_accounted_nanousd"] == 3_547_500
    assert report["oldest_unresolved_at"] is None
    assert report["oldest_unresolved_age_seconds"] is None
    assert report["lifetime_usage_estimate_nanousd"] == 3_547_500
    assert report["lifetime_confirmed_nanousd"] == 0
    with pytest.raises(AttemptTransitionError):
        await repo.settle_usage(
            attempt, settlement().model_copy(update={"request_id": "different-request"})
        )
    second = await repo.admit(spec(), policy())
    await repo.mark_dispatched(second)
    await repo.settle_usage(second, settlement())
    with pytest.raises(SpendDeferred, match="attempt_limit"):
        await repo.admit(spec(), policy())
    async with in_memory_storage._read_connection() as conn:
        row = await repo._attempt(conn, attempt)
        assert row["result"] == "refusal" and row["cost_basis"] == "provider_usage"
        assert row["request_id"] == "fixture-request"
        assert json.loads(row["settlement_json"])["usage"]["cache_write_1h_tokens"] == 20
        assert "fixture" not in row["provenance_json"]  # No API key or prompt payload.


async def test_undispatched_release_never_frees_a_dispatched_call(in_memory_storage):
    repo = in_memory_storage.spend
    attempt = await repo.admit(spec(), policy(max_attempts_per_operation=1))
    await repo.release_undispatched(attempt)
    assert (await repo.report())["held_nanousd"] == 0
    replacement = await repo.admit(spec(), policy(max_attempts_per_operation=1))
    await repo.mark_dispatched(replacement)
    with pytest.raises(AttemptTransitionError):
        await repo.release_undispatched(replacement)
    assert (await repo.report())["held_nanousd"] == 4_065_000_000


async def test_cancelled_admission_rolls_back_before_dispatch(in_memory_storage, monkeypatch):
    from home_finder.db import spend

    original = spend.spend_report
    entered = asyncio.Event()
    wait = asyncio.Event()

    async def suspended(conn, now):
        entered.set()
        await wait.wait()
        return await original(conn, now)

    monkeypatch.setattr(spend, "spend_report", suspended)
    task = asyncio.create_task(in_memory_storage.spend.admit(spec(), policy()))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(spend, "spend_report", original)
    assert (await in_memory_storage.spend.report())["attempts"] == 0


async def test_process_exit_after_dispatch_keeps_reservation(tmp_path):
    path = tmp_path / "crash.db"
    child = """
import asyncio, os, sys
from home_finder.db import PropertyStorage
from tests.test_db.test_spend import spec, policy
async def run():
    storage = PropertyStorage(sys.argv[1])
    await storage.initialize()
    attempt = await storage.spend.admit(spec(), policy())
    await storage.spend.mark_dispatched(attempt)
    async with storage._read_connection() as conn:
        async with conn.execute("PRAGMA synchronous") as cursor:
            assert (await cursor.fetchone())[0] == 2
    os._exit(23)
asyncio.run(run())
"""
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", child, str(path)], capture_output=True, timeout=20
    )
    assert result.returncode == 23, result.stderr.decode()
    async with PropertyStorage(str(path)) as storage:
        report = await storage.spend.report()
        assert report["held_nanousd"] == 4_065_000_000
        assert report["unresolved"][0]["state"] == "dispatched"
        with pytest.raises(SpendDeferred, match="operation_unresolved"):
            await storage.spend.admit(spec(), policy())


async def test_read_report_never_initializes_or_migrates(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=8")
        conn.execute("CREATE TABLE fixture (value TEXT)")
        conn.commit()
    before = path.read_bytes()
    report = await read_report(path)
    assert report["ledger_available"] is False and report["historical_cost"] == "unknown"
    assert path.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        await read_report(missing)
    assert not missing.exists()


async def test_usage_exceeding_reservation_is_recorded_and_blocks_new_spend(in_memory_storage):
    repo = in_memory_storage.spend
    cap = policy(per_call_usd="7", daily_usd="7", monthly_usd="7")
    attempt = await repo.admit(spec(), cap)
    await repo.mark_dispatched(attempt)
    # Unexpected provider usage is never clipped to the estimate or hidden.
    await repo.settle_usage(
        attempt,
        UsageSettlement(
            usage=BillableUsage(input_tokens=0, output_tokens=500_000), result="success"
        ),
    )
    report = await repo.report()
    assert report["lifetime_accounted_nanousd"] == 7_500_000_000
    assert report["over_reservation_count"] == 1
    with pytest.raises(SpendDeferred, match="reservation_exceeded"):
        await repo.admit(spec("next"), cap)


async def test_ledger_survives_property_deletion(in_memory_storage, sample_property):
    await in_memory_storage.save_property(sample_property)
    attempt_spec = spec().model_copy(update={"property_id": sample_property.unique_id})
    attempt = await in_memory_storage.spend.admit(attempt_spec, policy())
    await in_memory_storage.spend.mark_dispatched(attempt)
    async with in_memory_storage._transaction() as conn:
        await conn.execute(
            "DELETE FROM properties WHERE unique_id = ?", (sample_property.unique_id,)
        )
    assert (await in_memory_storage.spend.report())["held_nanousd"] == 4_065_000_000


async def test_billing_confirmation_preserves_original_usage(in_memory_storage):
    repo = in_memory_storage.spend
    attempt = await repo.admit(spec(), policy())
    await repo.mark_dispatched(attempt)
    await repo.settle_usage(attempt, settlement())
    confirmed = ConfirmedCost(amount_usd="0.003", evidence_ref="invoice:fixture")
    await repo.reconcile(attempt, confirmed)
    await repo.settle_usage(attempt, settlement())
    assert (await repo.report())["lifetime_confirmed_nanousd"] == 3_000_000
    async with in_memory_storage._read_connection() as conn:
        row = await repo._attempt(conn, attempt)
        assert row["settlement_json"] == settlement().model_dump_json()
        assert row["reconciled_at"] is not None
    with pytest.raises(AttemptTransitionError):
        await repo.reconcile(attempt, ConfirmedCost(amount_usd="0", evidence_ref="invoice:changed"))


async def test_complete_provider_error_releases_cost_without_inventing_usage(in_memory_storage):
    repo = in_memory_storage.spend
    attempt = await repo.admit(spec(), policy())
    await repo.mark_dispatched(attempt)
    rejection = ProviderRejection(
        http_status=429, error_type="rate_limit_error", result="rate_limit", request_id="fixture"
    )
    await repo.settle_rejection(attempt, rejection)
    await repo.settle_rejection(attempt, rejection)
    report = await repo.report()
    assert report["provider_rejections"] == 1 and report["held_nanousd"] == 0
    assert report["lifetime_accounted_nanousd"] == 0
    async with in_memory_storage._read_connection() as conn:
        row = await repo._attempt(conn, attempt)
        assert row["settlement_json"] is None and row["cost_basis"] == "provider_error"
    await repo.admit(spec(), policy())
    with pytest.raises(AttemptTransitionError):
        await repo.settle_usage(attempt, settlement())


async def test_uncharged_rejections_still_obey_explicit_total_attempt_cap(in_memory_storage):
    repo = in_memory_storage.spend
    cap = policy(max_attempts_per_operation=1, max_total_attempts=1)
    attempt = await repo.admit(spec(), cap)
    await repo.mark_dispatched(attempt)
    await repo.settle_rejection(
        attempt,
        ProviderRejection(http_status=529, error_type="overloaded_error", result="transient"),
    )
    with pytest.raises(SpendDeferred, match="total_attempt_limit"):
        await repo.admit(spec(), cap)


async def test_read_report_cli_cannot_start_processing(tmp_path):
    path = tmp_path / "report.db"
    async with PropertyStorage(str(path)) as storage:
        await storage.spend.admit(spec(), policy())
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "home_finder.pipeline.spend_report", "--database", str(path)],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode()
    report = json.loads(result.stdout)
    assert report["attempts"] == 1 and report["held_usd"] == "4.065000000"
    assert len(report["unresolved"]) == 1
