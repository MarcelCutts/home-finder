"""Smaller request reservations remain bounded across native calls and restarts."""

import base64
import copy
import json
from datetime import UTC, datetime
from io import BytesIO

import httpx2
import pytest
from PIL import Image
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.db.spend import SpendRepository
from home_finder.filters.quality import APIUnavailableError, PropertyQualityFilter
from home_finder.filters.request_budget import input_reservation_tokens
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import (
    SONNET_STANDARD_RATES,
    AttemptSpec,
    BillableUsage,
    RateCard,
    SpendDeferred,
    SpendPolicy,
    UsageSettlement,
)
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)


def config(**overrides):
    phase = {
        "model": "claude-sonnet-4-6",
        "reasoning": "adaptive",
        "effort": "low",
        "sdk_retries": 0,
        "input_token_limit": 140_000,
        "max_output_tokens": 8192,
        **overrides,
    }
    return AnalysisConfig.model_validate({"visual": phase, "evaluation": phase})


def policy(**overrides):
    return SpendPolicy(
        **{
            "per_call_usd": "1",
            "daily_usd": "1",
            "monthly_usd": "1",
            "lifetime_usd": "1",
            "max_total_attempts": 2,
            "max_in_flight": 1,
            "max_attempts_per_operation": 1,
            **overrides,
        }
    )


def spec(key="a"):
    return AttemptSpec(
        operation_key=key * 64,
        request_sha256=key * 64,
        property_id="fixture:small",
        phase="visual",
        provenance=PropertyQualityFilter(
            "fixture", analysis_config=config()
        )._analysis_provenance(),
    )


def body(content=None):
    return {
        "messages": [{"role": "user", "content": content or [{"type": "text", "text": "flat"}]}]
    }


def test_sonnet_rates_and_invalid_request_modes():
    assert SONNET_STANDARD_RATES.reservation(config().visual) == 962_880_000
    assert (
        SONNET_STANDARD_RATES.cost(
            BillableUsage(
                input_tokens=100,
                output_tokens=200,
                cache_write_5m_tokens=10,
                cache_write_1h_tokens=20,
                cache_read_tokens=300,
            )
        )
        == 3_547_500
    )
    for changes in [
        {"reasoning": "between_tools"},
        {"model": "claude-sonnet-5"},
        {"input_token_limit": True},
    ]:
        with pytest.raises(ValidationError):
            config(**changes)
    with pytest.raises(ValidationError):
        RateCard.model_validate({**SONNET_STANDARD_RATES.model_dump(), "input_nanousd": 2000})


def test_input_reservation_counts_unicode_schema_and_each_static_image():
    buffer = BytesIO()
    Image.new("RGB", (64, 64)).save(buffer, "PNG")
    image = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(buffer.getvalue()).decode(),
        },
    }
    first = body([{"type": "text", "text": "flat"}])
    second = body([{"type": "text", "text": "flat"}, image, image])
    assert input_reservation_tokens(second) >= input_reservation_tokens(first) + 10_000
    assert input_reservation_tokens(
        body([{"type": "text", "text": "🏠" * 100}])
    ) > input_reservation_tokens(first)
    assert (
        input_reservation_tokens({**first, "tools": [{"description": "x" * 1000}]})
        > input_reservation_tokens(first) + 2000
    )
    image["source"]["media_type"] = "image/jpeg"
    with pytest.raises(SpendDeferred, match="unsupported_input_budget"):
        input_reservation_tokens(second)


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": []},
        {"messages": [{"role": "assistant", "content": "prefix"}]},
        body([{"type": "document", "source": {"type": "url", "url": "https://example.com"}}]),
        body([{"type": "image", "source": {"type": "url", "url": "https://example.com/image"}}]),
        body([{"type": "tool_result", "content": "result"}]),
        body([{"type": "text", "text": 100}]),
    ],
)
def test_unknown_payloads_cannot_obtain_a_smaller_reservation(payload):
    with pytest.raises(SpendDeferred, match="unsupported_input_budget"):
        input_reservation_tokens(payload)


async def test_native_two_phase_sonnet_run_settles_under_one_dollar(cached_analysis_property):
    requests = []
    outputs = [copy.deepcopy(PHASE1_VISUAL_RESPONSE), copy.deepcopy(PHASE2_EVALUATION_RESPONSE)]
    for output in outputs:
        output["model"] = "claude-sonnet-4-6"

    def respond(request):
        payload = json.loads(request.content)
        assert input_reservation_tokens(payload) <= 140_000
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["output_config"]["effort"] == "low"
        if not requests:
            assert payload["tool_choice"] == {"type": "auto"}
        requests.append(payload)
        return httpx2.Response(200, json=outputs[len(requests) - 1])

    async with PropertyStorage(":memory:") as storage:
        async with PropertyQualityFilter(
            "fixture",
            analysis_config=config(),
            spend_repository=storage.spend,
            spend_policy=policy(),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        ) as q:
            merged, data_dir = cached_analysis_property
            _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
            assert result.overall_rating is not None
            assert result.analysis_provenance.input_budget_policy is not None
            with pytest.raises(SpendDeferred, match="total_attempt_limit"):
                await q._run_visual_analysis(body()["messages"][0]["content"], "fixture:extra")
        report = await storage.spend.report()
        assert len(requests) == report["attempts"] == 2
        assert 0 < report["lifetime_accounted_nanousd"] < 1_000_000_000
        assert report["held_nanousd"] == 0


async def test_too_large_request_stops_before_admission():
    async with PropertyStorage(":memory:") as storage:
        async with PropertyQualityFilter(
            "fixture",
            analysis_config=config(input_token_limit=20_000),
            spend_repository=storage.spend,
            spend_policy=policy(),
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(lambda _: pytest.fail("No send"))
            ),
        ) as q:
            with pytest.raises(SpendDeferred, match="input_limit"):
                await q._run_visual_analysis(
                    [{"type": "text", "text": "x" * 30_000}], "fixture:large"
                )
        assert (await storage.spend.report())["attempts"] == 0


async def test_unknown_cost_holds_budget_and_no_retry(cached_analysis_property):
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        raise httpx2.ReadTimeout("fixture")

    async with PropertyStorage(":memory:") as storage:
        async with PropertyQualityFilter(
            "fixture",
            analysis_config=config(),
            spend_repository=storage.spend,
            spend_policy=policy(),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        ) as q:
            with pytest.raises(APIUnavailableError):
                await q._run_visual_analysis(body()["messages"][0]["content"], "fixture:unknown")
        assert calls == 1
        assert (await storage.spend.report())["held_nanousd"] == 962_880_000
        with pytest.raises(SpendDeferred, match="lifetime_limit"):
            await storage.spend.admit(spec("b"), policy())


async def test_lifetime_cap_survives_restart_and_month_boundary(tmp_path):
    path = str(tmp_path / "smoke.db")
    async with PropertyStorage(path) as storage:
        repo = SpendRepository(
            storage._read_connection,
            storage._transaction,
            clock=lambda: datetime(2026, 9, 30, tzinfo=UTC),
        )
        attempt = await repo.admit(spec(), policy())
        await repo.mark_dispatched(attempt)
        await repo.settle_usage(
            attempt,
            UsageSettlement(
                usage=BillableUsage(input_tokens=100_000, output_tokens=0), result="success"
            ),
        )
    async with PropertyStorage(path) as storage:
        repo = SpendRepository(
            storage._read_connection,
            storage._transaction,
            clock=lambda: datetime(2026, 10, 1, tzinfo=UTC),
        )
        with pytest.raises(SpendDeferred, match="lifetime_limit"):
            await repo.admit(spec("b"), policy())
