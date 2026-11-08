"""GET /diagnostics: the owner's view of pipeline state (protected by the access boundary)."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_diagnostics_before_any_pipeline_run(client: TestClient) -> None:
    data = client.get("/diagnostics").json()
    assert data["pipeline_running"] is False
    assert data["last_run_at"] is None
    assert data["last_run_status"] is None
    assert data["last_run_notified"] is None


@pytest.mark.parametrize("held", [False, True])
async def test_pipeline_running_reflects_the_pipeline_lock(
    app: FastAPI, client: TestClient, held: bool
) -> None:
    app.state.pipeline_lock = asyncio.Lock()
    if held:
        await app.state.pipeline_lock.acquire()
    assert client.get("/diagnostics").json()["pipeline_running"] is held
