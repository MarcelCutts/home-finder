"""Exercise the timing plugin in real serial and distributed pytest sessions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.summarize_test_profile import summarize


@pytest.mark.parametrize("workers", [0, 2])
def test_profile_retains_pass_skip_and_teardown_failure(tmp_path, workers):
    (tmp_path / "test_sample.py").write_text(
        """
import pytest

pytestmark = pytest.mark.xdist_group("one-worker")

@pytest.fixture
def broken_teardown():
    yield
    raise ValueError('intentional teardown failure')

def test_pass():
    assert 2 + 2 == 4

@pytest.mark.skip(reason='intentional skip')
def test_skip():
    pass

def test_teardown(broken_teardown):
    pass
"""
    )
    destination = tmp_path / "reports/profile.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "tests.support.pytest_profile",
            "-n",
            str(workers),
            "--dist=loadgroup",
            f"--{'test-profile' if workers == 0 else 'timing-report'}={destination}",
            "-q",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    profile = json.loads(destination.read_text())
    assert profile["exit_code"] == 1
    assert profile["wall_seconds"] > 0
    assert profile["phase_outcomes"] == {
        "setup:passed": 2,
        "setup:skipped": 1,
        "call:passed": 2,
        "teardown:passed": 2,
        "teardown:failed": 1,
    }
    assert len(profile["workers"]) == workers
    window = profile["test_window"]
    assert window["seconds"] > 0
    assert window["startup_seconds"] >= 0
    assert window["finish_seconds"] >= 0
    assert window["worker_slots"] == max(1, workers)
    # All cases share a group: a second allocated worker is idle and must count
    # against occupancy rather than disappearing from the denominator.
    assert window["occupied_fraction"] <= 1 / max(1, workers) + 0.01
    if workers:
        assert list(window["worker_phase_seconds"].values()).count(0.0) == workers - 1
    assert sum(window["worker_phase_seconds"].values()) == pytest.approx(
        sum(phase["seconds"] for phase in profile["phases"])
    )
    processes = profile["workers"] or {"main": profile["controller"]}
    assert (
        sum(
            fixture["count"]
            for process in processes.values()
            for name, fixture in process["fixtures"].items()
            if name.endswith("::broken_teardown")
        )
        == 1
    )
    assert all(process["selected"] == 3 for process in processes.values())
    summary = summarize(destination)
    assert "test_sample.py" in summary
    assert "teardown:failed" in summary
    assert "overlap across workers" in summary
    assert "wall occupancy, not CPU utilisation" in summary


def test_worker_crash_keeps_completed_phases_and_marks_missing_worker_timing(tmp_path):
    (tmp_path / "test_crash.py").write_text(
        "import os\n\ndef test_completed():\n    assert True\n"
        "\ndef test_crash():\n    os._exit(17)\n"
    )
    destination = tmp_path / "profile.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "tests.support.pytest_profile",
            "-n",
            "1",
            "--max-worker-restart=0",
            f"--test-profile={destination}",
            "-q",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    profile = json.loads(destination.read_text())
    assert profile["exit_code"] == 1
    assert profile["workers"] == {"gw0": {"crashed": True}}
    assert any(
        phase["nodeid"].endswith("::test_completed")
        and phase["phase"] == "call"
        and phase["outcome"] == "passed"
        for phase in profile["phases"]
    )
    assert any(
        phase["nodeid"].endswith("::test_crash") and phase["outcome"] == "failed"
        for phase in profile["phases"]
    )
    assert "timing unavailable; crashed=True" in summarize(destination)
