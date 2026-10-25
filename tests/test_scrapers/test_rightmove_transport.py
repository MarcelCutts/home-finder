"""Search transport health, bounds, cleanup and application cancellation."""

import asyncio
import json
import signal
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from bs4 import BeautifulSoup
from tenacity import wait_none

from home_finder.scrapers import rightmove
from home_finder.scrapers.base import ScrapeCancelled
from home_finder.scrapers.rightmove import RightmoveScraper

SEARCH = {"min_price": 1800, "max_price": 2500, "min_bedrooms": 1, "max_bedrooms": 2, "area": "E8"}
EMPTY = '<div class="l-searchResults"><div class="no-results">No properties found</div></div>'


@pytest.fixture
def bounded_scraper(monkeypatch):
    monkeypatch.setattr(rightmove, "SCRAPER_WAIT", wait_none())
    scraper = RightmoveScraper()
    scraper.MAX_PAGES = 4
    scraper.PAGE_DELAY_SECONDS = 0
    return scraper


@pytest.fixture
def clients(monkeypatch):
    """Keep the real clients visible so tests check cleanup, not just returned data."""
    created = []
    factory = httpx.AsyncClient

    def create(**kwargs):
        client = factory(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(rightmove.httpx, "AsyncClient", create)
    return created


@pytest.mark.parametrize("status,attempts", [(403, 1), (404, 1), (429, 3), (500, 3)])
async def test_http_failures_are_bounded_and_unhealthy(
    bounded_scraper, httpx_mock, clients, status, attempts
):
    for _ in range(attempts):
        httpx_mock.add_response(status_code=status, text="Access denied")
    result = await bounded_scraper.scrape(**SEARCH)
    assert len(httpx_mock.get_requests()) == attempts
    assert result.pages_failed == 1 and result.pages_fetched == 0
    assert result.stop_reason == "page_error"
    assert not result.is_healthy and not result.properties
    assert bounded_scraper.should_skip_remaining_areas is (status in {403, 429})
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_failure_exhausts_exactly_the_attempt_limit(
    bounded_scraper, httpx_mock, error
):
    for _ in range(bounded_scraper.MAX_FETCH_ATTEMPTS):
        httpx_mock.add_exception(error("fixture failure"))
    result = await bounded_scraper.scrape(**SEARCH)
    assert len(httpx_mock.get_requests()) == bounded_scraper.MAX_FETCH_ATTEMPTS
    assert result.pages_failed == 1 and not result.is_healthy
    assert result.stop_reason == "page_error"


async def test_transient_errors_recover_without_marking_page_failed(
    bounded_scraper, httpx_mock, fixtures_path
):
    httpx_mock.add_response(status_code=429)
    httpx_mock.add_response(status_code=503)
    httpx_mock.add_response(text=(fixtures_path / "rightmove_next_search.html").read_text())
    result = await bounded_scraper.scrape(**SEARCH, max_results=1)
    assert len(httpx_mock.get_requests()) == 3
    assert result.is_healthy and len(result.properties) == 1 and len(result.observations) == 2
    assert not bounded_scraper.should_skip_remaining_areas


@pytest.mark.parametrize("status,attempts", [(403, 1), (429, 3), (503, 3)])
async def test_later_page_failure_stops_and_keeps_received_observations(
    bounded_scraper, httpx_mock, fixtures_path, status, attempts
):
    httpx_mock.add_response(text=(fixtures_path / "rightmove_next_search.html").read_text())
    for _ in range(attempts):
        httpx_mock.add_response(status_code=status)
    result = await bounded_scraper.scrape(**SEARCH)
    assert result.pages_fetched == 1 and result.pages_failed == 1
    assert len(result.observations) == 2 and result.properties == result.observations
    assert not result.is_healthy
    assert result.stop_reason == "page_error"
    assert len(httpx_mock.get_requests()) == attempts + 1
    assert httpx_mock.get_requests()[-1].url.params["index"] == "24"


@pytest.mark.parametrize("status,attempts", [(403, 1), (429, 3)])
@pytest.mark.parametrize("received_page", [False, True])
async def test_blocked_source_does_not_retry_other_pages_or_areas(
    bounded_scraper, monkeypatch, httpx_mock, fixtures_path, status, attempts, received_page
):
    from home_finder.pipeline.collection import scrape_all_platforms

    monkeypatch.setattr("home_finder.pipeline.collection.RightmoveScraper", lambda: bounded_scraper)
    monkeypatch.setattr("home_finder.pipeline.collection.random.shuffle", lambda areas: None)
    area_delay = AsyncMock()
    monkeypatch.setattr(bounded_scraper, "area_delay", area_delay)
    if received_page:
        httpx_mock.add_response(text=(fixtures_path / "rightmove_next_search.html").read_text())
    for _ in range(attempts):
        httpx_mock.add_response(status_code=status)
    observations = []

    async def retain(properties):
        observations.extend(properties)

    properties, metrics = await scrape_all_platforms(
        **{key: value for key, value in SEARCH.items() if key != "area"},
        search_areas=["E8", "E9", "N15"],
        only_scrapers={"rightmove"},
        on_observations=retain,
    )
    assert len(httpx_mock.get_requests()) == attempts + int(received_page)
    assert bounded_scraper.should_skip_remaining_areas
    assert len(metrics) == 1 and metrics[0].areas_attempted == 1
    assert metrics[0].pages_failed == 1 and not metrics[0].is_healthy
    assert len(properties) == len(observations) == (2 if received_page else 0)


async def test_transient_rate_limit_does_not_skip_the_next_area(
    bounded_scraper, monkeypatch, httpx_mock
):
    from home_finder.pipeline.collection import scrape_all_platforms

    monkeypatch.setattr("home_finder.pipeline.collection.RightmoveScraper", lambda: bounded_scraper)
    monkeypatch.setattr("home_finder.pipeline.collection.random.shuffle", lambda areas: None)
    monkeypatch.setattr(bounded_scraper, "area_delay", AsyncMock())
    httpx_mock.add_response(status_code=429)
    httpx_mock.add_response(text=EMPTY)
    httpx_mock.add_response(text=EMPTY)
    _, metrics = await scrape_all_platforms(
        **{key: value for key, value in SEARCH.items() if key != "area"},
        search_areas=["E8", "E9"],
        only_scrapers={"rightmove"},
    )
    assert len(httpx_mock.get_requests()) == 3
    assert not bounded_scraper.should_skip_remaining_areas
    assert metrics[0].areas_attempted == 2 and metrics[0].is_healthy


@pytest.mark.parametrize("html,errors", [(EMPTY, 0), ("<title>Verify you are human</title>", 1)])
async def test_successful_http_distinguishes_empty_results_from_challenge(
    bounded_scraper, httpx_mock, html, errors
):
    httpx_mock.add_response(text=html)
    result = await bounded_scraper.scrape(**SEARCH)
    assert result.pages_fetched == 1 and result.pages_failed == 0
    assert result.parse_errors == errors
    assert result.is_healthy is (errors == 0)


async def test_total_deadline_cancels_a_response_that_does_not_finish(
    bounded_scraper, httpx_mock, clients
):
    bounded_scraper.REQUEST_TIMEOUT_SECONDS = 0.02
    cancelled = []

    async def stalled(request):
        try:
            await asyncio.Future()
        finally:
            cancelled.append(request.url)

    for _ in range(bounded_scraper.MAX_FETCH_ATTEMPTS):
        httpx_mock.add_callback(stalled)
    result = await asyncio.wait_for(bounded_scraper.scrape(**SEARCH), 2)
    assert len(cancelled) == 3
    assert result.pages_failed == 1 and not result.is_healthy
    assert clients[0].is_closed


@pytest.mark.parametrize("boundary", ["request", "backoff", "page_delay"])
async def test_cancel_propagates_at_every_wait_without_returning_success(
    bounded_scraper, httpx_mock, clients, monkeypatch, fixtures_path, boundary
):
    reached = asyncio.Event()
    if boundary == "request":

        async def blocked(request):
            reached.set()
            await asyncio.Future()

        httpx_mock.add_callback(blocked)
    elif boundary == "backoff":
        httpx_mock.add_response(status_code=429)

        def long_wait(state):
            reached.set()
            return 60

        monkeypatch.setattr(rightmove, "SCRAPER_WAIT", long_wait)
    else:
        bounded_scraper.MAX_PAGES = 2
        html = (fixtures_path / "rightmove_next_search.html").read_text()
        soup = BeautifulSoup(html, "html.parser")
        payload = json.loads(soup.find("script", id="__NEXT_DATA__").string)
        payload["props"]["pageProps"]["searchResults"]["properties"][1]["bedrooms"] = None
        httpx_mock.add_response(
            text='<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>"
        )

        async def page_delay(seconds):
            reached.set()
            await asyncio.Future()

        monkeypatch.setattr(rightmove.asyncio, "sleep", page_delay)

    task = asyncio.create_task(bounded_scraper.scrape(**SEARCH))
    await asyncio.wait_for(reached.wait(), 2)
    task.cancel()
    with pytest.raises(ScrapeCancelled) as exc:
        await asyncio.wait_for(task, 2)
    assert exc.value.result.stop_reason == "cancelled"
    assert not exc.value.result.is_healthy and task.cancelled()
    if boundary == "page_delay":
        assert exc.value.result.pages_fetched == 1
        assert len(exc.value.result.observations) == 1
        assert exc.value.result.parse_errors == 1
    assert len(httpx_mock.get_requests()) == 1
    assert clients[0].is_closed


def test_asyncio_runner_interrupt_callback_is_not_replaced_or_swallowed(monkeypatch):
    """Invoke the real Runner callback directly; register/send no OS signals."""
    callbacks = []
    factory = httpx.AsyncClient
    created = []
    later_stage = Mock()
    real_signal = signal.signal
    real_getsignal = signal.getsignal
    virtual_handler = signal.default_int_handler

    async def request(_request):
        # Runner installs its application handler before starting the coroutine.
        handler = callbacks[0]
        handler(signal.SIGINT, None)
        await asyncio.Future()

    def client(**kwargs):
        instance = factory(transport=httpx.MockTransport(request), **kwargs)
        created.append(instance)
        return instance

    def capture_signal(sig, handler):
        nonlocal virtual_handler
        if sig != signal.SIGINT:
            return real_signal(sig, handler)
        previous = virtual_handler
        virtual_handler = handler
        callbacks.append(handler)
        return previous

    def read_signal(sig):
        return virtual_handler if sig == signal.SIGINT else real_getsignal(sig)

    monkeypatch.setattr(signal, "getsignal", read_signal)
    monkeypatch.setattr(signal, "signal", capture_signal)
    monkeypatch.setattr(rightmove.httpx, "AsyncClient", client)

    async def application():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop, "add_signal_handler", Mock(side_effect=AssertionError("override"))
        )
        await RightmoveScraper().scrape(**SEARCH)
        later_stage()

    with asyncio.Runner() as runner, pytest.raises(KeyboardInterrupt):
        runner.run(application())
    later_stage.assert_not_called()
    assert created[0].is_closed


async def test_real_pipeline_cancellation_does_not_enter_enrichment_or_analysis(
    monkeypatch, httpx_mock, tmp_path, clients
):
    from home_finder.config import Settings
    from home_finder.db import PropertyStorage
    from home_finder.pipeline.flow import _run_pipeline_with_storage

    reached = asyncio.Event()

    async def blocked(request):
        reached.set()
        await asyncio.Future()

    httpx_mock.add_callback(blocked)
    later_stages = [
        "home_finder.pipeline.candidates._enrich_candidates",
        "home_finder.pipeline.flow.process_initial_assessments",
        "home_finder.pipeline.flow.process_reanalysis_queue",
    ]
    mocks = []
    for path in later_stages:
        mock = AsyncMock(side_effect=AssertionError("later stage reached"))
        monkeypatch.setattr(path, mock)
        mocks.append(mock)
    notifier = Mock(side_effect=AssertionError("notification reached"))
    monkeypatch.setattr("home_finder.pipeline.flow.TelegramNotifier", notifier)
    settings = Settings(_env_file=None, search_areas="e8", data_dir=str(tmp_path))
    async with PropertyStorage(":memory:") as storage:
        task = asyncio.create_task(
            _run_pipeline_with_storage(settings, storage, only_scrapers={"rightmove"}, dry_run=True)
        )
        await asyncio.wait_for(reached.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    for mock in mocks:
        mock.assert_not_awaited()
    notifier.assert_not_called()
    assert all(client.is_closed for client in clients)
