"""Operator cost actions cannot race the runner or silently release paid work."""

import asyncio
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.spend import AttemptTransitionError
from home_finder.models.spend import ConfirmedCost
from home_finder.pipeline.run_lock import runner_lock
from home_finder.pipeline.spend_admin import administer
from tests.test_db.test_spend import policy, spec


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
        attempt = await storage.spend.admit(spec(), policy())
    result = await asyncio.to_thread(
        subprocess.run,
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
    assert result.returncode == 0, result.stderr.decode()
    async with PropertyStorage(str(path)) as storage:
        assert (await storage.spend.report())["held_nanousd"] == 0


async def test_admin_cannot_create_a_missing_database(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        await administer(path, "a" * 32, confirmed=None)
    assert not path.exists()
