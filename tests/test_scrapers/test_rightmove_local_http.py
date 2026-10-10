"""Real HTTP search checks restricted to loopback, with no portal requests or signals."""

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from home_finder.scrapers.base import ScrapeCancelled
from home_finder.scrapers.rightmove import RightmoveScraper

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])
SEARCH = {"min_price": 1800, "max_price": 2500, "min_bedrooms": 1, "max_bedrooms": 2, "area": "E8"}


@pytest.fixture
async def local_http(monkeypatch, fixtures_path):
    html = (fixtures_path / "rightmove_next_search.html").read_bytes()
    state = SimpleNamespace(
        requests=[],
        status=200,
        # Exercise parsing a body as large as the retained stall fixture.
        body=html + b"<!--" + b"x" * 1_000_000 + b"-->",
        slow=False,
        started=asyncio.Event(),
        disconnected=asyncio.Event(),
    )
    connections = set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        connections.add(task)
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            state.requests.append(request.split(b"\r\n", 1)[0])
            state.started.set()
            if state.slow == "drip":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n")
                try:
                    while True:
                        writer.write(b"x")
                        await writer.drain()
                        await asyncio.sleep(0.01)
                except ConnectionError:
                    state.disconnected.set()
                return
            if state.slow:
                # Send no headers; observe the client closing on timeout/cancellation.
                await reader.read()
                state.disconnected.set()
                return
            writer.write(
                f"HTTP/1.1 {state.status} Fixture\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(state.body)}\r\n"
                "Connection: close\r\n\r\n".encode()
                + state.body
            )
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            connections.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(RightmoveScraper, "BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(RightmoveScraper, "MAX_PAGES", 1)
    # Requests to any configured external proxy remain forbidden by pytest-socket.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()
        pending = list(connections)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def test_local_large_page_completes_once_with_all_observations(local_http):
    result = await asyncio.wait_for(RightmoveScraper().scrape(**SEARCH, max_results=1), 3)
    assert len(local_http.requests) == 1
    assert result.is_healthy and result.pages_fetched == 1
    assert len(result.properties) == 1 and len(result.observations) == 2
    assert result.stop_reason == "result_limit"


async def test_local_http_error_is_a_failed_page(local_http):
    local_http.status = 403
    result = await asyncio.wait_for(RightmoveScraper().scrape(**SEARCH), 3)
    assert len(local_http.requests) == 1
    assert result.pages_failed == 1 and result.pages_fetched == 0
    assert not result.is_healthy


@pytest.mark.parametrize("slow", [True, "drip"])
async def test_local_slow_response_times_out_and_closes_connection(local_http, monkeypatch, slow):
    local_http.slow = slow
    monkeypatch.setattr(RightmoveScraper, "REQUEST_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(RightmoveScraper, "MAX_FETCH_ATTEMPTS", 1)
    result = await asyncio.wait_for(RightmoveScraper().scrape(**SEARCH), 3)
    await asyncio.wait_for(local_http.disconnected.wait(), 1)
    assert len(local_http.requests) == 1
    assert result.pages_failed == 1 and not result.is_healthy


async def test_local_cancel_closes_connection_and_preserves_application_signal_handler(
    local_http, monkeypatch
):
    local_http.slow = True
    later_stage = Mock()
    loop = asyncio.get_running_loop()
    add_handler = Mock(side_effect=AssertionError("scraper replaced signal handler"))
    remove_handler = Mock(side_effect=AssertionError("scraper removed signal handler"))
    monkeypatch.setattr(loop, "add_signal_handler", add_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_handler)

    async def application():
        await RightmoveScraper().scrape(**SEARCH)
        later_stage()

    task = asyncio.create_task(application())
    await asyncio.wait_for(local_http.started.wait(), 2)
    task.cancel()
    with pytest.raises(ScrapeCancelled):
        await asyncio.wait_for(task, 1)
    await asyncio.wait_for(local_http.disconnected.wait(), 1)
    assert task.cancelled() and len(local_http.requests) == 1
    later_stage.assert_not_called()
    add_handler.assert_not_called()
    remove_handler.assert_not_called()
