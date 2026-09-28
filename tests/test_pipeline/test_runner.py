"""Cross-process exclusion and completion-relative scheduling, without provider I/O."""

import asyncio
import errno
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.main import run_pipeline, run_scrape_only
from home_finder.pipeline.analysis import run_reanalysis
from home_finder.pipeline.commands import (
    run_backfill_commute,
    run_check_off_market,
    run_dedup_existing,
)
from home_finder.pipeline.runner import RunSkipped, runner_lock
from home_finder.web.app import _pipeline_loop


def test_two_starter_processes_and_crash_release(tmp_path):
    db_path = str(tmp_path / "authority.db")
    hold = """
import sys
from home_finder.pipeline.runner import runner_lock
with runner_lock(sys.argv[1]) as acquired:
    print(acquired, flush=True)
    sys.stdin.read()
"""
    probe = """
import sys
from home_finder.pipeline.runner import runner_lock
with runner_lock(sys.argv[1]) as acquired:
    print(acquired)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", hold, db_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "True"
        denied = subprocess.run(
            [sys.executable, "-c", probe, db_path],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert denied.stdout.strip() == "False"
        child.kill()  # Kernel must release ownership even without Python cleanup.
        child.wait(timeout=5)
        admitted = subprocess.run(
            [sys.executable, "-c", probe, db_path],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert admitted.stdout.strip() == "True"
        assert (tmp_path / "authority.db.runner.lock").exists()
    finally:
        child.kill() if child.poll() is None else None
        child.communicate(timeout=5)


def test_symlink_and_relative_paths_share_lock(tmp_path, monkeypatch):
    db = tmp_path / "authority.db"
    db.touch()
    alias = tmp_path / "alias.db"
    alias.symlink_to(db)
    monkeypatch.chdir(tmp_path)
    with runner_lock(str(db)) as first, runner_lock("alias.db") as second:
        assert first
        assert not second
    with runner_lock("authority.db") as after:
        assert after


def test_lock_errors_do_not_silently_admit_work(tmp_path):
    with (
        patch("home_finder.pipeline.runner.fcntl.flock", side_effect=OSError(errno.ENOLCK, "full")),
        pytest.raises(OSError),
        runner_lock(str(tmp_path / "db")),
    ):
        pytest.fail("Must not enter the operation")


def test_hard_link_alias_rejected(tmp_path):
    db, alias = tmp_path / "db", tmp_path / "alias"
    db.touch()
    alias.hardlink_to(db)
    with pytest.raises(ValueError, match="hard links"), runner_lock(str(alias)):
        pytest.fail("Hard-linked authority must not bypass path-based exclusion")


@pytest.mark.parametrize(
    "entry",
    [
        run_pipeline,
        run_reanalysis,
        run_scrape_only,
        run_backfill_commute,
        run_check_off_market,
        run_dedup_existing,
    ],
)
async def test_entry_points_skip_before_database_or_provider_work(tmp_path, entry):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "db"))
    with runner_lock(settings.database_path), patch.object(PropertyStorage, "initialize") as init:
        assert await entry(settings) == RunSkipped.ALREADY_RUNNING
        init.assert_not_called()


async def test_provided_storage_is_the_lock_authority_and_cancel_releases(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "unused.db"))
    async with PropertyStorage(":memory:") as storage:
        entered = asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        with patch("home_finder.main._run_pipeline_with_storage", side_effect=blocked):
            task = asyncio.create_task(run_pipeline(settings, storage=storage))
            await asyncio.wait_for(entered.wait(), 2)
            assert (
                await run_pipeline(settings=settings, storage=storage) == RunSkipped.ALREADY_RUNNING
            )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with patch("home_finder.main._run_pipeline_with_storage", new_callable=AsyncMock) as body:
            await run_pipeline(settings, storage=storage)
            body.assert_awaited_once()
        assert not (tmp_path / "unused.db.runner.lock").exists()


@pytest.mark.parametrize("start", [0, 3599, 86399])
@pytest.mark.parametrize("interval, jitter", [(1, 0), (1, 300), (55, 0), (55, 300)])
async def test_schedule_waits_after_long_run_across_clock_boundaries(start, interval, jitter):
    now = float(start)
    starts = []
    sleeps = []

    async def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    async def run(*args, **kwargs):
        nonlocal now
        starts.append(now)
        if len(starts) == 2:
            raise asyncio.CancelledError
        now += 7200  # Two-hour run exceeds the configured interval.

    settings = Settings(_env_file=None)
    with (
        patch("home_finder.web.app.asyncio.sleep", side_effect=sleep),
        patch("home_finder.web.app.random.uniform", return_value=jitter) as random,
        patch("home_finder.main.run_pipeline", side_effect=run),
        pytest.raises(asyncio.CancelledError),
    ):
        await _pipeline_loop(settings, interval, AsyncMock())
    assert sleeps == [30, interval * 60 + jitter]
    assert starts[1] - starts[0] == 7200 + interval * 60 + jitter
    random.assert_called_once_with(0, 300)
