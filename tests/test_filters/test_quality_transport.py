"""Native SDK boundary: baseline requests, retries, cancellation and ownership."""

import asyncio
import hashlib
import json

import httpx2
import pytest

from home_finder.filters.quality import APIUnavailableError, PropertyQualityFilter
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)

pytestmark = pytest.mark.usefixtures("native_analysis_spending")

CONTENT = [{"type": "text", "text": "Transport compatibility fixture"}]


async def test_metered_requests_preserve_baseline_except_explicit_billing_scope():
    requests = []
    responses = [PHASE1_VISUAL_RESPONSE, PHASE2_EVALUATION_RESPONSE]

    def respond(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        requests.append(request)
        return httpx2.Response(200, json=responses[len(requests) - 1])

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityFilter("fixture", http_client=native) as quality:
        visual = await quality._run_visual_analysis(CONTENT, "fixture:transport")
        evaluation = await quality._run_evaluation(
            visual,
            "fixture:transport",
            description="EPC rating C",
            features=["Shared garden"],
            price_pcm=2000,
            bedrooms=1,
            effective_average=2200,
            area_context=None,
            outcode="E8",
            council_tax_band_c=None,
            crime_summary=None,
            rent_trend=None,
            energy_estimate=None,
            hosting_tolerance=None,
        )
    # Native mocked HTTP snapshots intentionally refreshed for supplied-image
    # coverage/access guidance, outdoor evidence, typed room-area scope and
    # kitchen/living connectivity. The evaluation request is unchanged. Do not refresh these
    # merely to accommodate a transport upgrade. Billing scope, defaults and
    # timeouts remain pinned below; historical provenance is tested separately.
    payloads = [json.loads(r.content) for r in requests]
    for payload in payloads:
        assert payload.pop("service_tier") == "standard_only"
        assert payload.pop("inference_geo") == "global"
    assert [
        hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() for body in payloads
    ] == [
        "0e76cfa7561773d0ff9f98b2b84398cbb860d27d848a6a1d5575f04f5c77fc4a",
        "6b995745404f48c2aeeb7acde3615b9f34087de8738d64bf236556e4d5e6f2d8",
    ]
    assert [r.extensions["timeout"]["read"] for r in requests] == [240.0, 60.0]
    assert [r.extensions["timeout"]["connect"] for r in requests] == [5.0, 5.0]
    assert visual == PHASE1_VISUAL_RESPONSE["content"][0]["input"]
    assert evaluation["value_for_quality"]["rating"] == "good"
    assert vars(quality.token_usage) == {
        "input_tokens": 2415,
        "output_tokens": 499,
        "cache_read_tokens": 2000,
        "cache_creation_tokens": 0,
        "cache_creation_1h_tokens": 0,
        "_estimated_cost_nanousd": 15_330_000,
    }
    assert native.is_closed


@pytest.mark.parametrize("status", [429, 500])
async def test_actual_sdk_retry_preserves_request_and_counts_success_usage(status):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                status,
                json={"type": "error", "error": {"type": "api_error", "message": "fixture"}},
                headers={"retry-after": "0.001"},
            )
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityFilter("fixture", http_client=native) as quality:
        result = await quality._run_visual_analysis(CONTENT, "fixture:retry")
        assert result is not None
        assert len(requests) == 2 and requests[0].content == requests[1].content
        assert requests[1].headers["x-stainless-retry-count"] == "1"
        assert quality.token_usage.input_tokens == PHASE1_VISUAL_RESPONSE["usage"]["input_tokens"]
    assert native.is_closed


async def test_native_timeout_keeps_reservation_without_retry(native_analysis_spending):
    requests = []

    def respond(request):
        requests.append(request)
        raise httpx2.ReadTimeout("fixture timeout", request=request)

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityFilter("fixture", http_client=native) as quality:
        with pytest.raises(APIUnavailableError):
            await quality._run_visual_analysis(CONTENT, "fixture:timeout")
        assert len(requests) == 1
        assert quality.token_usage.input_tokens == quality.token_usage.output_tokens == 0
        assert quality._breaker.failure_count == 1
    assert native.is_closed
    report = await native_analysis_spending[0].spend.report()
    assert report["held_nanousd"] == 4_065_000_000
    assert report["unresolved"][0]["state"] == "unknown"


async def test_cancellation_reaches_native_request_without_retry_or_circuit_failure(
    native_analysis_spending,
):
    entered = asyncio.Event()
    never = asyncio.Event()
    calls = 0

    async def respond(request):
        nonlocal calls
        calls += 1
        entered.set()
        await never.wait()
        raise AssertionError("Cancelled request must not finish normally")

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityFilter("fixture", http_client=native) as quality:
        task = asyncio.create_task(quality._run_visual_analysis(CONTENT, "fixture:cancel"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert calls == 1 and quality._breaker.failure_count == 0
        assert quality.token_usage.input_tokens == quality.token_usage.output_tokens == 0
    assert native.is_closed
    report = await native_analysis_spending[0].spend.report()
    assert report["held_nanousd"] == 4_065_000_000
    assert report["unresolved"][0]["result"] == "cancelled"


async def test_unused_injected_client_is_closed_without_constructing_sdk():
    native = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: pytest.fail("No call")))
    quality = PropertyQualityFilter("fixture", http_client=native)
    assert quality._client is None
    await quality.close()
    await quality.close()
    assert native.is_closed and quality._client is None


async def test_default_production_client_uses_native_sdk_timeout():
    quality = PropertyQualityFilter("fixture")
    try:
        client = quality._get_client()
        assert isinstance(client.timeout, httpx2.Timeout)
        assert client.timeout.read == 240.0 and client.timeout.connect == 5.0
        assert client.max_retries == 1
    finally:
        await quality.close()
    assert client.is_closed()
