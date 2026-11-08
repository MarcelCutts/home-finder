"""A small run's lifetime and total-attempt caps hold across native calls and restarts."""

import copy
import json
from datetime import UTC, datetime

import httpx2
import pytest

from home_finder.analysis.analyzer import APIUnavailableError, PropertyQualityAnalyzer
from home_finder.analysis.request_budget import input_reservation_tokens
from home_finder.db import PropertyStorage
from home_finder.db.spend import SpendRepository
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import (
    AttemptSpec,
    BillableUsage,
    SpendDeferred,
    SpendPolicy,
    UsageSettlement,
)
from tests.support.analysis_responses import PHASE1_VISUAL_RESPONSE, PHASE2_EVALUATION_RESPONSE
from tests.support.analysis_spend import CONTENT

SONNET46 = "claude-sonnet-4-6"


def small_run_config():
    """Sonnet 4.6 with a bounded input and output: $0.64788 reserved per request."""
    phase = {
        "model": SONNET46,
        "reasoning": "adaptive",
        "effort": "low",
        "sdk_retries": 0,
        "input_token_limit": 140_000,
        "max_output_tokens": 8192,
    }
    return AnalysisConfig.model_validate({"visual": phase, "evaluation": phase})


def small_run_policy(**overrides):
    """One dollar in total, two paid attempts, one at a time."""
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
        provenance=PropertyQualityAnalyzer(
            "fixture", analysis_config=small_run_config()
        )._analysis_provenance(),
    )


def analyzer_for(storage, respond):
    return PropertyQualityAnalyzer(
        "fixture",
        analysis_config=small_run_config(),
        spend_repository=storage.spend,
        spend_policy=small_run_policy(),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
    )


async def test_small_run_settles_under_one_dollar_and_stops_at_the_total_attempt_cap(
    cached_analysis_property,
):
    requests = []
    outputs = [copy.deepcopy(PHASE1_VISUAL_RESPONSE), copy.deepcopy(PHASE2_EVALUATION_RESPONSE)]

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
        async with analyzer_for(storage, respond) as analyzer:
            merged, data_dir = cached_analysis_property
            _, result = await analyzer.analyze_single_merged(merged, data_dir=data_dir)
            assert result.overall_rating is not None
            assert result.analysis_provenance.input_budget_policy is not None
            with pytest.raises(SpendDeferred, match="total_attempt_limit"):
                await analyzer._run_visual_analysis(CONTENT, "fixture:extra")
        report = await storage.spend.report()
        assert len(requests) == report["attempts"] == 2
        assert 0 < report["lifetime_accounted_nanousd"] < 1_000_000_000
        assert report["held_nanousd"] == 0


async def test_unknown_outcome_holds_its_reservation_against_the_lifetime_cap():
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        raise httpx2.ReadTimeout("fixture")

    async with PropertyStorage(":memory:") as storage:
        async with analyzer_for(storage, respond) as analyzer:
            with pytest.raises(APIUnavailableError):
                await analyzer._run_visual_analysis(CONTENT, "fixture:unknown")
        assert calls == 1
        assert (await storage.spend.report())["held_nanousd"] == 647_880_000
        with pytest.raises(SpendDeferred, match="lifetime_limit"):
            await storage.spend.admit(spec("b"), small_run_policy())


async def test_lifetime_cap_survives_restart_and_month_boundary(tmp_path):
    path = str(tmp_path / "smoke.db")
    cap = small_run_policy(lifetime_usd="0.90")  # $0.30 settled plus $0.64788 reserved exceeds it.
    async with PropertyStorage(path) as storage:
        repo = SpendRepository(
            storage._read_connection,
            storage._transaction,
            clock=lambda: datetime(2026, 9, 30, tzinfo=UTC),
        )
        attempt = await repo.admit(spec(), cap)
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
            await repo.admit(spec("b"), cap)
