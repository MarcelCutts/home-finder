"""Detail transport attempt limits, backoff and independent throttle buckets.

The retry policy and client calls run for real. Native curl I/O is replaced at
its session boundary; a virtual clock and Tenacity's sleep seam avoid real waits.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from home_finder.scrapers import detail_fetcher
from home_finder.scrapers.constants import BROWSER_HEADERS
from home_finder.scrapers.detail_fetcher import DetailFetcher

URL = "https://www.rightmove.co.uk/properties/123"


@pytest.fixture
def retry_waits(monkeypatch):
    waits = []
    real_retry = detail_fetcher.retry

    async def record_sleep(seconds):
        waits.append(float(seconds))

    def with_recorded_sleep(**kwargs):
        return real_retry(**kwargs, sleep=record_sleep)

    monkeypatch.setattr(detail_fetcher, "retry", with_recorded_sleep)
    return waits


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=100.0, waits=[])

    async def advance(seconds):
        clock.waits.append(seconds)
        clock.now += seconds

    # Patching the module reference leaves the event loop's asyncio.sleep alone.
    monkeypatch.setattr(
        detail_fetcher,
        "asyncio",
        SimpleNamespace(
            Lock=asyncio.Lock,
            get_event_loop=lambda: SimpleNamespace(time=lambda: clock.now),
            sleep=advance,
        ),
    )
    return clock


@pytest.mark.parametrize(
    "statuses",
    [(200,), (429, 200), (429, 429), (403,), (500,)],
    ids=["success", "rate-limit-recovers", "rate-limit-exhausted", "forbidden", "server-error"],
)
async def test_httpx_attempt_limit_and_final_response(statuses, retry_waits):
    requests = []
    responses = []

    def respond(request):
        requests.append(request)
        response = httpx.Response(statuses[len(requests) - 1], request=request)
        responses.append(response)
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    async with DetailFetcher() as fetcher:
        fetcher._client = client
        if statuses[-1] >= 400:
            with pytest.raises(httpx.HTTPStatusError) as caught:
                await fetcher._httpx_get_with_retry(URL)
            assert caught.value.response is responses[-1]
            assert caught.value.response.status_code == statuses[-1]
        else:
            response = await fetcher._httpx_get_with_retry(URL)
            assert response is responses[-1]
    assert len(requests) == len(statuses)
    assert retry_waits == ([2.0] if len(statuses) == 2 else [])
    assert client.is_closed


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, asyncio.CancelledError])
async def test_transport_errors_and_cancellation_are_not_rate_limit_retries(
    error_type, retry_waits
):
    requests = []

    def fail(request):
        requests.append(request)
        raise error_type("fixture")

    async with DetailFetcher() as fetcher:
        fetcher._client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
        with pytest.raises(error_type):
            await fetcher._httpx_get_with_retry(URL)
    assert len(requests) == 1
    assert retry_waits == []


@pytest.mark.parametrize(
    "statuses",
    [(200,), (429, 200), (429, 429), (403,)],
    ids=["success", "rate-limit-recovers", "rate-limit-exhausted", "forbidden"],
)
@pytest.mark.parametrize("proxy", [None, "http://fixture-proxy:8080"])
async def test_curl_retries_return_final_response_and_preserve_request_options(
    statuses, proxy, retry_waits, clock
):
    responses = [SimpleNamespace(status_code=status) for status in statuses]
    session = SimpleNamespace(get=AsyncMock(side_effect=responses), close=AsyncMock())
    async with DetailFetcher(proxy_url=proxy) as fetcher:
        fetcher._curl_session = session
        result = await fetcher._curl_get_with_retry(URL, timeout=15)
    assert result is responses[-1]
    assert session.get.await_count == len(statuses)
    expected_options = {"impersonate": "chrome", "headers": BROWSER_HEADERS, "timeout": 15}
    if proxy:
        expected_options["proxy"] = proxy
    assert all(
        call.args == (URL,) and call.kwargs == expected_options
        for call in session.get.await_args_list
    )
    if len(statuses) == 2:
        assert len(retry_waits) == 1 and 2 <= retry_waits[0] <= 3
        assert clock.waits == [3.0]
    else:
        assert retry_waits == [] and clock.waits == []
    session.close.assert_awaited_once()


@pytest.mark.parametrize(
    "name,interval,attribute",
    [
        (None, 3.0, "_zoopla_next_time"),
        (None, 0.3, "_otm_next_time"),
        ("openrent", 0.3, "_openrent_next_time"),
        ("img_zoocdn", 0.5, "_img_zoocdn_next_time"),
        ("img_otm", 0.3, "_img_otm_next_time"),
        ("img_openrent", 0.3, "_img_openrent_next_time"),
    ],
)
async def test_each_curl_bucket_spaces_repeated_requests(
    name, interval, attribute, retry_waits, clock
):
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status_code=200)), close=AsyncMock()
    )
    async with DetailFetcher() as fetcher:
        fetcher._curl_session = session
        for _ in range(2):
            await fetcher._curl_get_with_retry(URL, throttle_name=name, min_interval=interval)
        assert getattr(fetcher, attribute) == pytest.approx(100 + 2 * interval)
    assert clock.waits == pytest.approx([interval])
    assert session.get.await_count == 2
    assert retry_waits == []


async def test_detail_and_image_buckets_do_not_delay_each_other(clock, retry_waits):
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status_code=200)), close=AsyncMock()
    )
    async with DetailFetcher() as fetcher:
        fetcher._curl_session = session
        await fetcher._curl_get_with_retry(URL)
        await fetcher._curl_get_with_retry(URL, throttle_name="img_zoocdn", min_interval=0.5)
        assert fetcher._zoopla_next_time == 103
        assert fetcher._img_zoocdn_next_time == 100.5
    assert clock.waits == []


async def test_unsupported_interval_fails_before_opening_native_transport():
    async with DetailFetcher() as fetcher:
        with pytest.raises(ValueError, match="No throttle bucket"):
            await fetcher._curl_get_with_retry(URL, min_interval=0)
        assert fetcher._curl_session is None
