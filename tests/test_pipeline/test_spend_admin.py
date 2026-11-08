"""Operator spending commands: the read-only report and explicit reconciliation.

Neither may race the runner, migrate a database or silently release paid work.
"""

import asyncio
import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from pydantic import ValidationError

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.spend import AttemptTransitionError
from home_finder.models.spend import ConfirmedCost
from home_finder.pipeline.run_lock import runner_lock
from home_finder.pipeline.spend_admin import administer
from home_finder.pipeline.spend_report import read_report
from tests.support.spend import policy, spec


def test_spending_requires_explicit_exact_configuration(monkeypatch):
    monkeypatch.delenv("HOME_FINDER_ANALYSIS_SPEND_POLICY", raising=False)
    assert Settings(_env_file=None).analysis_spend_policy is None
    monkeypatch.setenv(
        "HOME_FINDER_ANALYSIS_SPEND_POLICY",
        '{"per_call_usd":"7","daily_usd":"20","monthly_usd":"100"}',
    )
    assert Settings(_env_file=None).analysis_spend_policy == policy()
    monkeypatch.setenv(
        "HOME_FINDER_ANALYSIS_SPEND_POLICY",
        '{"per_call_usd":0.1,"daily_usd":"20","monthly_usd":"100"}',
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


async def test_reconciliation_requires_exclusive_runner_and_retains_evidence(tmp_path):
    path = tmp_path / "operator.db"
    async with PropertyStorage(str(path)) as storage:
        attempt = await storage.spend.admit(spec(), policy())
        await storage.spend.mark_dispatched(attempt)
    confirmation = ConfirmedCost(amount_usd="0.125", evidence_ref="billing:fixture")
    with runner_lock(str(path)), pytest.raises(ValueError, match="Stop the active runner"):
        await administer(path, attempt, confirmed=confirmation)
    with pytest.raises(AttemptTransitionError):
        await administer(path, attempt, confirmed=None)
    report = await administer(path, attempt, confirmed=confirmation)
    assert report["held_nanousd"] == 0 and report["lifetime_confirmed_nanousd"] == 125_000_000
    async with PropertyStorage(str(path)) as storage, storage._read_connection() as conn:
        row = await storage.spend._attempt(conn, attempt)
        assert json.loads(row["reconciliation_json"])["evidence_ref"] == "billing:fixture"


async def test_cli_release_only_applies_to_an_undispatched_reservation(tmp_path):
    path = tmp_path / "release.db"
    async with PropertyStorage(str(path)) as storage:
        undispatched = await storage.spend.admit(spec("undispatched"), policy())
        dispatched = await storage.spend.admit(spec("dispatched"), policy())
        await storage.spend.mark_dispatched(dispatched)
        held_before = (await storage.spend.report())["held_nanousd"]

    def release(attempt):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "home_finder.pipeline.spend_admin",
                "--database",
                str(path),
                "--attempt",
                attempt,
                "--release-undispatched",
            ],
            capture_output=True,
            timeout=20,
        )

    refused = await asyncio.to_thread(release, dispatched)
    assert refused.returncode == 1
    assert b"Spending was not reconciled" in refused.stderr
    async with PropertyStorage(str(path)) as storage:
        assert (await storage.spend.report())["held_nanousd"] == held_before

    released = await asyncio.to_thread(release, undispatched)
    assert released.returncode == 0, released.stderr.decode()
    async with PropertyStorage(str(path)) as storage:
        report = await storage.spend.report()
    assert report["held_nanousd"] == held_before // 2
    assert [item["state"] for item in report["unresolved"]] == ["dispatched"]


async def test_admin_cannot_create_a_missing_database(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        await administer(path, "a" * 32, confirmed=None)
    assert not path.exists()


async def test_read_report_never_initializes_or_migrates(tmp_path):
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


async def test_read_report_cli_cannot_start_processing(tmp_path):
    """The report reads a ledger without taking the runner or recovering spend."""
    path = tmp_path / "report.db"
    async with PropertyStorage(str(path)) as storage:
        # A runner that started would release this undispatched reservation.
        await storage.spend.admit(spec(), policy())
    before = path.read_bytes()

    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "home_finder.pipeline.spend_report", "--database", str(path)],
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr.decode()
    report = json.loads(result.stdout)
    assert report["attempts"] == 1 and report["held_usd"] == "4.065000000"
    assert [item["state"] for item in report["unresolved"]] == ["reserved"]
    assert path.read_bytes() == before
    assert not path.with_name(path.name + ".runner.lock").exists()
    assert (await read_report(path))["unresolved"] == report["unresolved"]
