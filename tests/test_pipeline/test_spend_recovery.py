"""Runner takeover frees execution without erasing potentially billed work."""

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.spend import SpendRepository
from home_finder.main import run_pipeline
from home_finder.models.spend import ConfirmedCost, SpendDeferred, usd_text
from home_finder.pipeline.analysis import run_reanalysis
from home_finder.pipeline.runner import (
    RunSkipped,
    exclusive_runner,
    recover_abandoned_spend,
)
from tests.test_db.test_spend import policy, settlement, spec


async def ledger(storage):
    async with (
        storage._read_connection() as conn,
        conn.execute("SELECT * FROM paid_attempts ORDER BY admitted_at") as cursor,
    ):
        return {row["attempt_id"]: dict(row) for row in await cursor.fetchall()}


async def test_takeover_retains_money_history_and_cross_period_exposure(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "recovery.db"))
    now = datetime(2026, 9, 30, 23, 59, 50, tzinfo=UTC)
    async with PropertyStorage(settings.database_path) as storage:
        repo = storage._spend = SpendRepository(
            storage._read_connection, storage._transaction, clock=lambda: now
        )
        ids = {}
        for state in ("reserved", "dispatched", "unknown", "settled", "confirmed", "released"):
            ids[state] = attempt = await repo.admit(spec(state), policy(daily_usd="100"))
            if state == "released":
                await repo.release_undispatched(attempt)
            elif state != "reserved":
                await repo.mark_dispatched(attempt)
                if state == "unknown":
                    await repo.mark_unknown(attempt, "timeout", request_id="kept-request-id")
                elif state == "settled":
                    await repo.settle_usage(attempt, settlement())
                elif state == "confirmed":
                    await repo.reconcile(
                        attempt, ConfirmedCost(amount_usd="0.12", evidence_ref="billing:fixture")
                    )
            now += timedelta(seconds=1)
        before = await ledger(storage)
        reserve = before[ids["dispatched"]]["reserved_nanousd"]
        now += timedelta(days=2)

        async def body(*args, **kwargs):
            after = await ledger(storage)
            assert after[ids["reserved"]]["state"] == "released"
            assert after[ids["reserved"]]["result"] == "abandoned_before_dispatch"
            assert after[ids["reserved"]]["accounted_nanousd"] == 0
            assert after[ids["reserved"]]["completed_at"] is not None
            assert after[ids["dispatched"]]["state"] == "unknown"
            assert after[ids["dispatched"]]["result"] == "process_exit"
            for state, attempt in ids.items():
                changed = {
                    key for key, value in before[attempt].items() if after[attempt][key] != value
                }
                assert changed == {
                    "reserved": {"state", "result", "accounted_nanousd", "completed_at"},
                    "dispatched": {"state", "result"},
                }.get(state, set())
            report = await repo.report()
            assert report["in_flight"] == 0
            assert report["held_nanousd"] == 2 * reserve
            assert report["day_committed_nanousd"] == 2 * reserve
            assert report["month_committed_nanousd"] == 2 * reserve
            assert report["oldest_unresolved_at"] == before[ids["dispatched"]]["admitted_at"]
            assert report["oldest_unresolved_age_seconds"] == 2 * 86400 + 5
            with pytest.raises(SpendDeferred, match="operation_unresolved"):
                await repo.admit(spec("dispatched"), policy())
            # One more request would exceed this cap despite zero current usage.
            cap = usd_text(2 * reserve)
            with pytest.raises(SpendDeferred, match="daily_limit"):
                await repo.admit(
                    spec("over-budget"),
                    policy(per_call_usd=usd_text(reserve), daily_usd=cap, monthly_usd=cap),
                )
            live = await repo.admit(spec("new-run"), policy(max_in_flight=1))
            await recover_abandoned_spend(storage)  # Never recover the current run twice.
            assert (await ledger(storage))[live]["state"] == "reserved"

        with patch("home_finder.main._run_pipeline_with_storage", side_effect=body) as work:
            await run_pipeline(settings, storage=storage)
            work.assert_awaited_once()


@pytest.mark.parametrize("provided", [False, True])
async def test_recovery_requires_this_runner_task_and_database(tmp_path, provided):
    settings = Settings(_env_file=None, database_path=":memory:")
    async with PropertyStorage(":memory:") as storage, PropertyStorage(":memory:") as other:
        with pytest.raises(RuntimeError, match="ownership"):
            await recover_abandoned_spend(storage)

        @exclusive_runner
        async def entry(settings, *, storage=None):
            target = storage or own
            await recover_abandoned_spend(target)
            with pytest.raises(RuntimeError, match="ownership"):
                await recover_abandoned_spend(other)
            with pytest.raises(RuntimeError, match="ownership"):
                await asyncio.create_task(recover_abandoned_spend(target))

        own = storage
        await entry(settings, **({"storage": storage} if provided else {}))


async def test_competing_runner_cannot_recover_live_requests(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "live.db"))
    entered = asyncio.Event()
    async with PropertyStorage(settings.database_path) as storage:

        async def body(*args, **kwargs):
            attempt = await storage.spend.admit(spec(), policy())
            await storage.spend.mark_dispatched(attempt)
            entered.set()
            await asyncio.Future()

        with patch("home_finder.main._run_pipeline_with_storage", side_effect=body):
            owner = asyncio.create_task(run_pipeline(settings, storage=storage))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                before = await ledger(storage)
                assert await run_pipeline(settings) == RunSkipped.ALREADY_RUNNING
                assert await run_reanalysis(settings) == RunSkipped.ALREADY_RUNNING
                assert await ledger(storage) == before
            finally:
                owner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await owner


@pytest.mark.parametrize("entry", [run_pipeline, run_reanalysis])
async def test_process_exit_then_new_owner_recovers_before_any_work(tmp_path, entry):
    path = tmp_path / "crashed.db"
    script = """
import asyncio, os, sys
from home_finder.db import PropertyStorage
from home_finder.pipeline.runner import runner_lock
from tests.test_db.test_spend import spec, policy
async def run():
    with runner_lock(sys.argv[1]) as acquired:
        assert acquired
        async with PropertyStorage(sys.argv[1]) as storage:
            await storage.spend.admit(spec('undispatched'), policy())
            attempt = await storage.spend.admit(spec('possibly-billed'), policy())
            await storage.spend.mark_dispatched(attempt)
            os._exit(23)
asyncio.run(run())
"""
    child = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, str(path)], capture_output=True, timeout=20
    )
    assert child.returncode == 23, child.stderr.decode()
    settings = Settings(_env_file=None, database_path=str(path))

    async def check_recovered(*args, **kwargs):
        async with PropertyStorage(str(path)) as storage:
            rows = list((await ledger(storage)).values())
        assert [row["state"] for row in rows] == ["released", "unknown"]
        assert rows[1]["accounted_nanousd"] is None
        return []

    with (
        patch("home_finder.main._run_pipeline_with_storage", side_effect=check_recovered),
        patch(
            "home_finder.db.pipeline_repo.PipelineRepository.get_reanalysis_queue",
            side_effect=check_recovered,
        ),
    ):
        await entry(settings)
        async with PropertyStorage(str(path)) as storage:
            before = await ledger(storage)
        await entry(settings)
        async with PropertyStorage(str(path)) as storage:
            assert await ledger(storage) == before


async def test_cancelled_recovery_rolls_back_both_transitions(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "cancel.db"))
    async with PropertyStorage(settings.database_path) as storage:
        await storage.spend.admit(spec("reserved"), policy())
        attempt = await storage.spend.admit(spec("dispatched"), policy())
        await storage.spend.mark_dispatched(attempt)
        before = await ledger(storage)
        conn = storage._conn
        assert conn is not None
        execute = conn.execute
        interrupted = asyncio.Event()

        async def block_second_update(sql, *args, **kwargs):
            if "UPDATE paid_attempts SET state = 'unknown'" in sql:
                interrupted.set()
                await asyncio.Future()
            return await execute(sql, *args, **kwargs)

        monkeypatch.setattr(conn, "execute", block_second_update)
        task = asyncio.create_task(run_pipeline(settings, storage=storage))
        try:
            await asyncio.wait_for(interrupted.wait(), 3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        monkeypatch.setattr(conn, "execute", execute)
        assert await ledger(storage) == before
        with patch("home_finder.main._run_pipeline_with_storage", new_callable=AsyncMock):
            await run_pipeline(settings, storage=storage)
        assert [row["state"] for row in (await ledger(storage)).values()] == ["released", "unknown"]
