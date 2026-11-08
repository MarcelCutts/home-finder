"""The retired Fly sync script refuses to run, so it cannot replace a running database."""

import os
import subprocess

import pytest


@pytest.mark.parametrize("args", [[], ["--full"], ["--force"]])
def test_retired_sync_script_cannot_call_remote_tools(tmp_path, args):
    marker = tmp_path / "called"
    fake_fly = tmp_path / "fly"
    fake_fly.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
    fake_fly.chmod(0o700)
    run = subprocess.run(
        ["bash", "scripts/sync-to-fly.sh", *args],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        timeout=5,
    )
    assert run.returncode == 2 and b"disabled" in run.stderr
    assert not marker.exists()
