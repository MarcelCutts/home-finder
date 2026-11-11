"""How each physical provider request is settled in the paid-attempt ledger.

Uses the real SDK over a fake transport: billed output failures, SDK retries,
provider rejections, unknown outcomes and cancellation.
"""

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import anthropic
import httpx2
import pytest

from home_finder.analysis.analyzer import APIUnavailableError, PropertyQualityAnalyzer
from home_finder.analysis.contracts import AnalysisOutputError
from home_finder.models.spend import SpendDeferred
from home_finder.pipeline.assessment import run_concurrent_analysis
from home_finder.pipeline.contracts import AnalysisDisposition, AnalysisOutcome
from tests.support.analysis_config import configuration
from tests.support.analysis_responses import (
    PHASE1_VISUAL_RESPONSE,
    failed_message,
    response,
)
from tests.support.analysis_spend import CONTENT, quality, rows
from tests.support.visual_checkpoints import checkpoint_rows

SONNET46 = "claude-sonnet-4-6"
# The default configuration's visual reservation: its full input context at the
# five-minute cache-write rate plus 21,000 output tokens.
DEFAULT_VISUAL_RESERVATION = 4_065_000_000


def provider_error(status, error_type, message="fixture", **headers):
    return httpx2.Response(
        status,
        headers=headers,
        json={"type": "error", "error": {"type": error_type, "message": message}},
    )


@pytest.mark.parametrize(
    "scenario, kind",
    [
        ("refusal", "refusal"),
        ("max_tokens", "truncated"),
        ("model_context_window_exceeded", "truncated"),
        ("malformed_json", "malformed_output"),
        ("missing_eval_field", "malformed_output"),
    ],
)
async def test_billed_evaluation_failure_is_settled_and_keeps_the_visual_checkpoint(
    native_analysis_spending, cached_analysis_property, scenario, kind
):
    storage, spend_policy = native_analysis_spending
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    evaluation = failed_message("evaluation", scenario)  # Writes 17 cache tokens.
    evaluation["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 7,
        "ephemeral_1h_input_tokens": 10,
    }
    responses = iter([visual, evaluation])

    async def respond(request):
        report = await storage.spend.report()
        # Every HTTP dispatch already has its own committed reservation.
        assert report["in_flight"] == 1 and report["unresolved"][0]["state"] == "dispatched"
        return httpx2.Response(200, json=next(responses), headers={"request-id": "fixture-request"})

    merged, data_dir = cached_analysis_property
    async with quality(storage, spend_policy, respond) as analyzer:
        _, result = await analyzer.analyze_single_merged(merged, data_dir=data_dir)

    assert result.overall_rating is None
    assert (result.analysis_failure.phase, result.analysis_failure.kind) == ("evaluation", kind)
    attempts = await rows(storage)
    assert [(r["phase"], r["result"]) for r in attempts] == [
        ("visual", "success"),
        ("evaluation", kind),
    ]
    assert all(r["state"] == "settled" and r["cost_basis"] == "provider_usage" for r in attempts)
    assert all(r["request_id"] == "fixture-request" for r in attempts)
    usage = json.loads(attempts[1]["settlement_json"])["usage"]
    assert usage["cache_write_5m_tokens"] == 7 and usage["cache_write_1h_tokens"] == 10
    costs = [
        body["usage"]["input_tokens"] * 3000
        + body["usage"]["output_tokens"] * 15000
        + body["usage"]["cache_read_input_tokens"] * 300
        for body in (visual, evaluation)
    ]
    costs[1] += 7 * 3750 + 10 * 6000
    assert [r["accounted_nanousd"] for r in attempts] == costs
    assert analyzer.token_usage.estimated_cost_usd == pytest.approx(sum(costs) / 1e9)
    assert len(await checkpoint_rows(storage)) == 1


@pytest.mark.parametrize(
    "status, config, success, cost",
    [
        (429, None, PHASE1_VISUAL_RESPONSE, 9_609_000),
        (500, None, PHASE1_VISUAL_RESPONSE, 9_609_000),
        (503, configuration(effort="medium", sdk_retries=1), response(), 2_335_000),
    ],
    ids=["rate-limit", "server-error", "sonnet-5-5-unavailable"],
)
async def test_each_sdk_retry_is_admitted_and_settled_separately(
    native_analysis_spending, status, config, success, cost
):
    storage, spend_policy = native_analysis_spending
    calls = []

    async def respond(request):
        calls.append(request)
        attempts = await rows(storage)
        assert len(attempts) == len(calls) and attempts[-1]["state"] == "dispatched"
        if len(calls) == 1:
            return provider_error(
                status, "api_error", **{"retry-after": "0.001", "request-id": "rejected-fixture"}
            )
        return httpx2.Response(200, json=success)

    async with quality(storage, spend_policy, respond, analysis_config=config) as analyzer:
        assert await analyzer._run_visual_analysis(CONTENT, "fixture:retry")
        assert analyzer.token_usage.input_tokens == success["usage"]["input_tokens"]

    assert len(calls) == 2 and calls[0].content == calls[1].content
    assert calls[1].headers["x-stainless-retry-count"] == "1"
    rejected, settled = await rows(storage)
    assert rejected["operation_key"] == settled["operation_key"]
    assert rejected["reserved_nanousd"] == settled["reserved_nanousd"]
    assert rejected["result"] == ("rate_limit" if status == 429 else "transient")
    assert rejected["request_id"] == "rejected-fixture"
    assert rejected["settlement_json"] is None
    assert (rejected["cost_basis"], rejected["accounted_nanousd"]) == ("provider_error", 0)
    assert (settled["cost_basis"], settled["accounted_nanousd"]) == ("provider_usage", cost)
    assert (await storage.spend.report())["held_nanousd"] == 0


@pytest.mark.parametrize("scenario", ["timeout", "disconnect", "gateway_html"])
async def test_ambiguous_outcome_does_not_repeat_even_with_a_new_client(
    native_analysis_spending, scenario
):
    storage, spend_policy = native_analysis_spending
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if scenario == "timeout":
            raise httpx2.ReadTimeout("fixture", request=request)
        if scenario == "disconnect":
            raise httpx2.ReadError("fixture", request=request)
        return httpx2.Response(503, text="<html>Gateway error</html>")

    async with quality(storage, spend_policy, respond) as analyzer:
        with pytest.raises(APIUnavailableError):
            await analyzer._run_visual_analysis(CONTENT, "fixture:uncertain")
        assert analyzer._breaker.failure_count == 1
        assert analyzer.token_usage.input_tokens == analyzer.token_usage.output_tokens == 0
    async with quality(storage, spend_policy, respond) as restarted:
        with pytest.raises(SpendDeferred, match="operation_unresolved"):
            await restarted._run_visual_analysis(CONTENT, "fixture:uncertain")
    assert calls == 1
    report = await storage.spend.report()
    assert report["held_nanousd"] == DEFAULT_VISUAL_RESERVATION
    assert len(report["unresolved"]) == 1 and report["unresolved"][0]["state"] == "unknown"


def unpriced_response(scenario):
    output = response()
    if scenario == "missing":
        del output["usage"]["output_tokens"]
    elif scenario == "negative":
        output["usage"]["input_tokens"] = -1
    elif scenario == "cache_mismatch":
        output["usage"]["cache_creation"]["ephemeral_5m_input_tokens"] = 11
    elif scenario in {"priority", "batch"}:
        output["usage"]["service_tier"] = scenario
    elif scenario == "geography":
        output["usage"]["inference_geo"] = "us"
    elif scenario == "other_supported_model":
        # Even another supported model cannot be silently substituted.
        output["model"] = SONNET46
    elif scenario == "unpriced_model":
        output["model"] = "unpriced-model"
    elif scenario == "server_tool":
        output["usage"]["server_tool_use"] = {"web_search_requests": 1}
    else:
        output["usage"]["unpriced_units"] = 1
    return output


@pytest.mark.parametrize(
    "scenario",
    [
        "missing",
        "negative",
        "cache_mismatch",
        "priority",
        "batch",
        "geography",
        "other_supported_model",
        "unpriced_model",
        "server_tool",
        "new_units",
    ],
)
async def test_unpriced_response_holds_the_reservation_without_retry(
    native_analysis_spending, scenario
):
    storage, spend_policy = native_analysis_spending
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(
            200, json=unpriced_response(scenario), headers={"request-id": "unknown-fixture"}
        )

    async with quality(
        storage, spend_policy, respond, analysis_config=configuration(sdk_retries=1)
    ) as analyzer:
        with pytest.raises(APIUnavailableError, match="unpriced_provider_response"):
            await analyzer._run_visual_analysis(CONTENT, "fixture:billing")
    assert len(calls) == 1
    report = await storage.spend.report()
    assert report["held_nanousd"] == 431_920_000
    assert report["unresolved"][0]["state"] == "unknown"
    assert report["unresolved"][0]["request_id"] == "unknown-fixture"
    assert (await rows(storage))[0]["settlement_json"] is None
    assert await checkpoint_rows(storage) == []


@pytest.mark.parametrize(
    "status,error_type,message,result",
    [
        (401, "authentication_error", "Invalid key", "authentication"),
        (402, "billing_error", "Payment details must be updated", "quota"),
        (400, "invalid_request_error", "Schema too complex", "unsupported_schema"),
        (400, "invalid_request_error", "Credit balance too low", "quota"),
    ],
)
async def test_permanent_provider_errors_cannot_request_automatic_retry(
    native_analysis_spending, status, error_type, message, result
):
    storage, spend_policy = native_analysis_spending
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return provider_error(status, error_type, message, **{"x-should-retry": "true"})

    async with quality(storage, spend_policy, respond) as analyzer:
        with pytest.raises((APIUnavailableError, AnalysisOutputError)):
            await analyzer._run_visual_analysis(CONTENT, "fixture:permanent")
    assert calls == 1
    attempts = await rows(storage)
    assert attempts[0]["result"] == result and attempts[0]["accounted_nanousd"] == 0
    assert attempts[0]["cost_basis"] == "provider_error"


@pytest.mark.parametrize("status,error_type", [(409, "conflict_error"), (504, "timeout_error")])
async def test_documented_transient_error_envelopes_settle_each_rejection(
    native_analysis_spending, monkeypatch, status, error_type
):
    storage, spend_policy = native_analysis_spending
    backoffs = []

    def record_backoff(self, remaining_retries, options, response_headers=None):
        backoffs.append(remaining_retries)
        return 0.0

    # These envelopes carry no retry-after header, so the SDK would otherwise
    # sleep for its own exponential backoff before retrying.
    monkeypatch.setattr(anthropic.AsyncAnthropic, "_calculate_retry_timeout", record_backoff)
    async with quality(
        storage, spend_policy, lambda _: provider_error(status, error_type, "Try later")
    ) as analyzer:
        with pytest.raises((APIUnavailableError, AnalysisOutputError)):
            await analyzer._run_visual_analysis(CONTENT, "fixture:transient")
    assert backoffs == [1]  # The default configuration allows one SDK retry.
    attempts = await rows(storage)
    assert len(attempts) == 2
    assert all(
        attempt["state"] == "settled"
        and attempt["result"] == "transient"
        and attempt["cost_basis"] == "provider_error"
        and attempt["accounted_nanousd"] == 0
        for attempt in attempts
    )


async def test_unrecognised_provider_error_does_not_discard_liability(native_analysis_spending):
    storage, spend_policy = native_analysis_spending
    async with quality(
        storage, spend_policy, lambda _: provider_error(500, "future_error", "Unknown")
    ) as analyzer:
        with pytest.raises(APIUnavailableError, match="unpriced_provider_response"):
            await analyzer._run_visual_analysis(CONTENT, "fixture:unknown-error")
    attempts = await rows(storage)
    assert len(attempts) == 1
    assert attempts[0]["state"] == "unknown"
    assert attempts[0]["cost_basis"] is None
    assert (await storage.spend.report())["held_nanousd"] == DEFAULT_VISUAL_RESERVATION


async def test_cancellation_during_the_request_holds_the_reservation_without_circuit_failure(
    native_analysis_spending,
):
    storage, spend_policy = native_analysis_spending
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
    async with PropertyQualityAnalyzer(
        "fixture", http_client=native, spend_repository=storage.spend, spend_policy=spend_policy
    ) as analyzer:
        task = asyncio.create_task(analyzer._run_visual_analysis(CONTENT, "fixture:cancel"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert calls == 1 and analyzer._breaker.failure_count == 0
        assert analyzer.token_usage.input_tokens == analyzer.token_usage.output_tokens == 0
    assert native.is_closed
    report = await storage.spend.report()
    assert report["held_nanousd"] == DEFAULT_VISUAL_RESERVATION
    assert report["unresolved"][0]["result"] == "cancelled"


async def test_cancellation_after_response_finishes_accounting(
    native_analysis_spending, monkeypatch
):
    storage, spend_policy = native_analysis_spending
    entered, release = asyncio.Event(), asyncio.Event()
    original = storage.spend.settle_usage

    async def settle(attempt, usage, *, checkpoint=None):
        entered.set()
        await release.wait()
        await original(attempt, usage, checkpoint=checkpoint)

    monkeypatch.setattr(storage.spend, "settle_usage", settle)
    async with quality(
        storage, spend_policy, lambda _: httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
    ) as analyzer:
        task = asyncio.create_task(
            analyzer._run_visual_analysis(CONTENT, "fixture:cancel-accounting")
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    attempts = await rows(storage)
    assert len(attempts) == 1 and attempts[0]["state"] == "settled"
    assert attempts[0]["accounted_nanousd"] > 0
    assert (await storage.spend.report())["held_nanousd"] == 0


@pytest.mark.parametrize("boundary", ["admit", "settle_usage"])
async def test_ledger_failure_stops_sdk_retries(native_analysis_spending, monkeypatch, boundary):
    storage, spend_policy = native_analysis_spending
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    monkeypatch.setattr(storage.spend, boundary, AsyncMock(side_effect=OSError("fixture")))
    async with quality(storage, spend_policy, respond) as analyzer:
        with pytest.raises(APIUnavailableError, match="ledger_unavailable"):
            await analyzer._run_visual_analysis(CONTENT, "fixture:ledger-failure")
    assert len(calls) == (0 if boundary == "admit" else 1)
    report = await storage.spend.report()
    assert report["held_nanousd"] == (0 if boundary == "admit" else DEFAULT_VISUAL_RESERVATION)


async def test_uncharged_sdk_rejections_do_not_exhaust_future_paid_attempts(
    native_analysis_spending,
):
    storage, spend_policy = native_analysis_spending
    cap = spend_policy.model_copy(update={"max_attempts_per_operation": 1})
    calls = []

    def respond(request):
        calls.append(request)
        return provider_error(429, "rate_limit_error", **{"retry-after": "0.001"})

    async with quality(storage, cap, respond) as analyzer:
        with pytest.raises(APIUnavailableError):
            await analyzer._run_visual_analysis(CONTENT, "fixture:retry-cap")
    # SDK retry bounds still apply to each run. A later recovered request can
    # use the one allowed paid attempt because both rejections were uncharged.
    assert len(calls) == 2 and (await storage.spend.report())["provider_rejections"] == 2
    async with quality(
        storage, cap, lambda _: httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
    ) as analyzer:
        await analyzer._run_visual_analysis(CONTENT, "fixture:retry-cap")
        # Successful visual work is reusable even when no further paid attempt
        # is allowed. The receipt count below proves this did not dispatch again.
        await analyzer._run_visual_analysis(CONTENT, "fixture:retry-cap")
    assert len(await rows(storage)) == 3


async def test_provider_timeout_drains_other_inflight_requests_without_cancelling_them(
    native_analysis_spending, make_merged_property
):
    storage, spend_policy = native_analysis_spending
    items = [make_merged_property() for _ in range(3)]
    second_started, release_success = asyncio.Event(), asyncio.Event()
    dispatched = 0

    async def respond(request):
        nonlocal dispatched
        dispatched += 1
        if dispatched == 1:
            await second_started.wait()
            raise httpx2.ReadTimeout("fixture", request=request)
        second_started.set()
        await release_success.wait()
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with quality(storage, spend_policy, respond) as analyzer:

        async def analyze(merged):
            await analyzer._run_visual_analysis(CONTENT, merged.unique_id)
            return AnalysisOutcome(merged, AnalysisDisposition.SKIPPED, reason="fixture")

        complete = AsyncMock()
        complete.return_value = None
        count = await asyncio.wait_for(
            run_concurrent_analysis(
                items, analyze, complete, concurrency=2, on_deferred=release_success.set
            ),
            timeout=2,
        )
    assert count == 1 and dispatched == 2
    assert [row["state"] for row in await rows(storage)] == ["unknown", "settled"]
    assert (await storage.spend.report())["held_nanousd"] == DEFAULT_VISUAL_RESERVATION
    assert complete.await_args.args[0].candidate.unique_id == items[1].unique_id


@pytest.mark.parametrize("scenario", ["timeout", "invalid_usage", "cancelled"])
async def test_failed_uncertainty_write_keeps_durable_hold_and_stops_processing(
    native_analysis_spending, monkeypatch, scenario
):
    storage, spend_policy = native_analysis_spending
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if scenario == "timeout":
            raise httpx2.ReadTimeout("fixture", request=request)
        if scenario == "cancelled":
            raise asyncio.CancelledError()
        return httpx2.Response(200, json={"model": "unpriced"})

    monkeypatch.setattr(storage.spend, "mark_unknown", AsyncMock(side_effect=OSError("fixture")))
    async with quality(storage, spend_policy, respond) as analyzer:
        if scenario == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await analyzer._run_visual_analysis(CONTENT, "fixture:uncertainty-write")
        else:
            with pytest.raises(APIUnavailableError, match="ledger_unavailable"):
                await analyzer._run_visual_analysis(CONTENT, "fixture:uncertainty-write")
    assert calls == 1
    report = await storage.spend.report()
    assert report["held_nanousd"] == DEFAULT_VISUAL_RESERVATION
    assert report["unresolved"][0]["state"] == "dispatched"
