"""Provider requests and responses through the real Anthropic SDK client.

Requests go through production client construction, the spend middleware, SDK
serialization and response parsing against a fake httpx2 transport. The last two
tests cover who closes the HTTP client and the default client's timeouts.
"""

import copy
import hashlib
import json

import httpx2
import pytest

from home_finder.analysis.analyzer import (
    _CIRCUIT_BREAKER_THRESHOLD,
    VISUAL_ANALYSIS_TOOL,
    APIUnavailableError,
    PropertyQualityAnalyzer,
)
from home_finder.analysis.prompts import EVALUATION_SYSTEM_PROMPT, VISUAL_ANALYSIS_SYSTEM_PROMPT
from home_finder.models import PropertyImage, PropertySource
from home_finder.models.analysis import AnalysisConfig
from tests.support.analysis_responses import PHASE1_VISUAL_RESPONSE, PHASE2_EVALUATION_RESPONSE
from tests.support.analysis_spend import quality, rows
from tests.support.images import populate_image_cache

# The pinned request digests below were computed for this exact content.
BASELINE_CONTENT = [{"type": "text", "text": "Transport compatibility fixture"}]
NO_SDK_RETRIES = AnalysisConfig.model_validate(
    {"visual": {"sdk_retries": 0}, "evaluation": {"sdk_retries": 0}}
)


def provider_error(status, error_type, message="fixture"):
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": error_type, "message": message}}
    )


def replay(*replies):
    """Answer successive requests with these responses, or with these bodies as HTTP 200."""
    remaining = iter(replies)

    def respond(_request):
        reply = next(remaining)
        return reply if isinstance(reply, httpx2.Response) else httpx2.Response(200, json=reply)

    return respond


@pytest.fixture
def listing(make_merged_property, tmp_path):
    """Two gallery photos and a source-declared floorplan, all in the image cache."""

    def image(name, image_type):
        return PropertyImage(
            url=f"https://example.com/{name}.jpg",
            source=PropertySource.OPENRENT,
            image_type=image_type,
            floorplan_origin="source_declared" if image_type == "floorplan" else "unknown",
        )

    merged = make_merged_property(
        images=(image("gallery-1", "gallery"), image("gallery-2", "gallery")),
        floorplan=image("floorplan", "floorplan"),
        descriptions={PropertySource.OPENRENT: "EPC rating C"},
    )
    populate_image_cache(str(tmp_path), merged)
    return merged, str(tmp_path)


async def test_two_phase_requests_on_the_wire(listing, native_analysis_spending):
    storage, policy = native_analysis_spending
    requests = []
    replies = iter([PHASE1_VISUAL_RESPONSE, PHASE2_EVALUATION_RESPONSE])

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json=next(replies))

    merged, data_dir = listing
    async with quality(storage, policy, respond) as analyzer:
        await analyzer.analyze_single_merged(merged, data_dir=data_dir)

    assert [(r.method, str(r.url)) for r in requests] == [
        ("POST", "https://api.anthropic.com/v1/messages")
    ] * 2
    for request in requests:
        assert request.headers["x-api-key"] == "fixture"
        assert request.headers["content-type"] == "application/json"
        assert "anthropic-version" in request.headers
        assert "anthropic-beta" not in request.headers
    visual, evaluation = (json.loads(r.content) for r in requests)
    for body in (visual, evaluation):
        assert (body["service_tier"], body["inference_geo"]) == ("standard_only", "global")

    # Visual phase: cached photos and the floorplan as base64 image blocks, with
    # the system prompt and the visual tool marked for prompt caching.
    assert (visual["model"], visual["max_tokens"]) == ("claude-sonnet-4-6", 21000)
    assert visual["system"] == [
        {
            "type": "text",
            "text": VISUAL_ANALYSIS_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    images = [block for block in visual["messages"][0]["content"] if block["type"] == "image"]
    assert [block["source"]["type"] for block in images] == ["base64"] * 3
    assert visual["tools"] == [{**VISUAL_ANALYSIS_TOOL, "cache_control": {"type": "ephemeral"}}]
    assert visual["thinking"] == {"type": "adaptive"}
    assert visual["tool_choice"] == {"type": "auto"}

    # Evaluation phase: text only, embedding the visual result, with a JSON schema
    # output format instead of a tool.
    assert (evaluation["model"], evaluation["max_tokens"]) == ("claude-sonnet-4-6", 4096)
    assert evaluation["system"][0]["text"] == EVALUATION_SYSTEM_PROMPT
    prompt = evaluation["messages"][0]["content"]
    assert isinstance(prompt, str)
    assert "<visual_analysis>" in prompt and "</visual_analysis>" in prompt
    assert evaluation["output_config"]["format"]["type"] == "json_schema"
    assert not {"tools", "tool_choice", "thinking"} & set(evaluation)


async def test_metered_requests_preserve_baseline_except_explicit_billing_scope(
    native_analysis_spending,
):
    storage, policy = native_analysis_spending
    requests = []
    responses = [PHASE1_VISUAL_RESPONSE, PHASE2_EVALUATION_RESPONSE]

    def respond(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        requests.append(request)
        return httpx2.Response(200, json=responses[len(requests) - 1])

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityAnalyzer(
        "fixture", http_client=native, spend_repository=storage.spend, spend_policy=policy
    ) as analyzer:
        visual = await analyzer._run_visual_analysis(BASELINE_CONTENT, "fixture:transport")
        evaluation = await analyzer._run_evaluation(
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
    # The digests pin both request bodies byte for byte. Refresh them for a
    # deliberate prompt or schema change, never to accommodate an SDK or
    # transport upgrade. The middleware's billing scope is asserted separately.
    payloads = [json.loads(r.content) for r in requests]
    for payload in payloads:
        assert payload.pop("service_tier") == "standard_only"
        assert payload.pop("inference_geo") == "global"
    assert [
        hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() for body in payloads
    ] == [
        "0e76cfa7561773d0ff9f98b2b84398cbb860d27d848a6a1d5575f04f5c77fc4a",
        # Acoustic insulation values already include their dB unit.
        "57a586a3b167319228e680f0a757dcc4c1d2ed606adb84560a5b0b8bc92a2f9c",
    ]
    assert [r.extensions["timeout"]["read"] for r in requests] == [240.0, 60.0]
    assert [r.extensions["timeout"]["connect"] for r in requests] == [5.0, 5.0]
    assert visual == PHASE1_VISUAL_RESPONSE["content"][0]["input"]
    assert evaluation["value_for_quality"]["rating"] == "good"
    assert vars(analyzer.token_usage) == {
        "input_tokens": 2415,
        "output_tokens": 499,
        "cache_read_tokens": 2000,
        "cache_creation_tokens": 0,
        "cache_creation_1h_tokens": 0,
        "_estimated_cost_nanousd": 15_330_000,
    }
    assert native.is_closed


@pytest.mark.parametrize(
    ("description", "expected_epc"),
    [("EPC rating C", "C"), ("No rating stated.", "unknown")],
    ids=["claim-supported-by-listing", "claim-without-listing-support"],
)
async def test_two_phase_responses_complete_the_assessment(
    listing, native_analysis_spending, description, expected_epc
):
    """Both parsed responses fill the assessment; the EPC claim is checked against the listing.

    Phrasing variants of that check are covered by test_utils/test_cost_calculator.py.
    """
    storage, policy = native_analysis_spending
    merged, data_dir = listing
    merged = merged.model_copy(update={"descriptions": {merged.canonical.source: description}})
    respond = replay(PHASE1_VISUAL_RESPONSE, PHASE2_EVALUATION_RESPONSE)

    async with quality(storage, policy, respond) as analyzer:
        _, analysis = await analyzer.analyze_single_merged(merged, data_dir=data_dir)

    # Visual phase
    assert analysis.kitchen.overall_quality == "modern"
    assert analysis.kitchen.hob_type == "gas"
    assert analysis.condition.overall_condition == "good"
    assert analysis.light_space.natural_light == "excellent"
    assert analysis.space.living_room_sqm == 24.0
    assert analysis.space.hosting_layout == "good"
    assert analysis.overall_rating == 4
    assert analysis.condition_concerns is False
    # Evaluation phase (the response claims EPC C)
    assert analysis.listing_extraction.epc_rating == expected_epc
    assert analysis.listing_extraction.broadband_type == "fttc"
    assert {"Gas hob", "Dedicated office room"} <= set(analysis.highlights)
    assert "Victorian" in analysis.one_line
    assert analysis.value.quality_adjusted_rating == "good"
    assert [row["result"] for row in await rows(storage)] == ["success", "success"]


async def test_cache_usage_reaches_token_usage_and_settlement(listing, native_analysis_spending):
    storage, policy = native_analysis_spending
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    visual["usage"]["cache_creation_input_tokens"] = 50  # No breakdown: five-minute writes.
    evaluation = copy.deepcopy(PHASE2_EVALUATION_RESPONSE)
    evaluation["usage"]["cache_creation_input_tokens"] = 17
    evaluation["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 7,
        "ephemeral_1h_input_tokens": 10,
    }
    merged, data_dir = listing

    async with quality(storage, policy, replay(visual, evaluation)) as analyzer:
        await analyzer.analyze_single_merged(merged, data_dir=data_dir)

    usage = analyzer.token_usage
    assert (usage.cache_read_tokens, usage.cache_creation_tokens) == (1200 + 800, 50 + 17)
    assert usage.cache_creation_1h_tokens == 10
    settled = [json.loads(row["settlement_json"])["usage"] for row in await rows(storage)]
    assert [
        (u["cache_read_tokens"], u["cache_write_5m_tokens"], u["cache_write_1h_tokens"])
        for u in settled
    ] == [(1200, 50, 0), (800, 7, 10)]


@pytest.mark.parametrize("failed_phase", ["visual", "evaluation"])
async def test_provider_outage_in_either_phase_raises_api_unavailable(
    listing, native_analysis_spending, failed_phase
):
    """An outage propagates out of the batch helper instead of becoming a failed assessment."""
    storage, policy = native_analysis_spending
    outage = provider_error(500, "api_error")
    respond = replay(outage) if failed_phase == "visual" else replay(PHASE1_VISUAL_RESPONSE, outage)
    merged, data_dir = listing

    async with quality(storage, policy, respond, analysis_config=NO_SDK_RETRIES) as analyzer:
        with pytest.raises(APIUnavailableError):
            await analyzer.analyze_merged_properties([merged], data_dir=data_dir)
        assert analyzer._breaker.failure_count == 1

    attempts = await rows(storage)
    assert [(row["phase"], row["result"]) for row in attempts] == (
        [("visual", "transient")]
        if failed_phase == "visual"
        else [("visual", "success"), ("evaluation", "transient")]
    )
    assert attempts[-1]["state"] == "settled" and attempts[-1]["accounted_nanousd"] == 0


async def test_consecutive_rate_limits_open_the_circuit_before_another_request(
    listing, native_analysis_spending
):
    storage, policy = native_analysis_spending
    calls = []

    def respond(request):
        calls.append(request)
        return provider_error(429, "rate_limit_error")

    merged, data_dir = listing
    async with quality(storage, policy, respond, analysis_config=NO_SDK_RETRIES) as analyzer:
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            with pytest.raises(APIUnavailableError):
                await analyzer.analyze_single_merged(merged, data_dir=data_dir)
        assert analyzer._breaker.state == "open"
        with pytest.raises(APIUnavailableError, match="Circuit breaker is open"):
            await analyzer.analyze_single_merged(merged, data_dir=data_dir)

    assert len(calls) == _CIRCUIT_BREAKER_THRESHOLD
    attempts = await rows(storage)
    assert [row["result"] for row in attempts] == ["rate_limit"] * _CIRCUIT_BREAKER_THRESHOLD


async def test_unused_injected_client_is_closed_without_constructing_sdk():
    native = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: pytest.fail("No call")))
    analyzer = PropertyQualityAnalyzer("fixture", http_client=native)
    assert analyzer._client is None
    await analyzer.close()
    await analyzer.close()
    assert native.is_closed and analyzer._client is None


async def test_default_production_client_uses_native_sdk_timeout():
    analyzer = PropertyQualityAnalyzer("fixture")
    try:
        client = analyzer._get_client()
        assert isinstance(client.timeout, httpx2.Timeout)
        assert client.timeout.read == 240.0 and client.timeout.connect == 5.0
        assert client.max_retries == 1
    finally:
        await analyzer.close()
    assert client.is_closed()
