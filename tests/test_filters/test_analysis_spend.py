"""Money admission must cover the real SDK, retries and every analysis entry point."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.spend import SpendRepository
from home_finder.filters.analysis_contracts import AnalysisOutputError
from home_finder.filters.quality import APIUnavailableError, PropertyQualityFilter
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import SpendDeferred, SpendPolicy
from home_finder.pipeline.analysis import (
    _drain_reanalysis_queue,
    _run_concurrent_analysis,
    _run_quality_and_save,
    run_reanalysis,
)
from home_finder.pipeline.event_recorder import EventRecorder
from home_finder.pipeline.stages import PreAnalysisResult
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)

CONTENT = [{"type": "text", "text": "Spending fixture"}]


@pytest.fixture
async def budget():
    policy = SpendPolicy(per_call_usd="7", daily_usd="20", monthly_usd="20")
    async with PropertyStorage(":memory:") as storage:
        yield storage, policy


def quality(storage, policy, handler, **kwargs):
    return PropertyQualityFilter(
        "fixture",
        spend_repository=storage.spend if storage else None,
        spend_policy=policy,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        **kwargs,
    )


async def rows(storage):
    async with (
        storage._read_connection() as conn,
        conn.execute("SELECT * FROM paid_attempts ORDER BY admitted_at, attempt_id") as cur,
    ):
        return [dict(row) for row in await cur.fetchall()]


@pytest.mark.parametrize("missing", ["ledger", "policy", "both"])
async def test_no_implicit_financial_allowance(budget, missing):
    storage, policy = budget
    async with quality(
        None if missing in {"ledger", "both"} else storage,
        None if missing in {"policy", "both"} else policy,
        lambda _: pytest.fail("No request can bypass admission"),
    ) as q:
        with pytest.raises(SpendDeferred, match="spending_not_configured"):
            await q._run_visual_analysis(CONTENT, "fixture:disabled")
    assert await rows(storage) == []


@pytest.mark.parametrize("kind", ["refusal", "max_tokens", "malformed"])
async def test_both_phases_record_billed_output_failures_and_cache_ttls(
    budget, cached_analysis_property, kind
):
    storage, policy = budget
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    evaluation = copy.deepcopy(PHASE2_EVALUATION_RESPONSE)
    evaluation["usage"]["cache_creation_input_tokens"] = 17
    evaluation["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 7,
        "ephemeral_1h_input_tokens": 10,
    }
    if kind == "malformed":
        evaluation["content"][0]["text"] = "{broken"
    else:
        evaluation["stop_reason"] = kind
    responses = iter([visual, evaluation])

    async def respond(request):
        report = await storage.spend.report()
        # Every HTTP dispatch already has its own committed reservation.
        assert report["in_flight"] == 1 and report["unresolved"][0]["state"] == "dispatched"
        return httpx2.Response(200, json=next(responses), headers={"request-id": "fixture-request"})

    merged, data_dir = cached_analysis_property
    async with quality(storage, policy, respond) as q:
        _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
    assert result.overall_rating is None
    attempts = await rows(storage)
    assert [r["phase"] for r in attempts] == ["visual", "evaluation"]
    assert all(r["state"] == "settled" and r["cost_basis"] == "provider_usage" for r in attempts)
    assert [r["result"] for r in attempts] == [
        "success",
        {"refusal": "refusal", "max_tokens": "truncated", "malformed": "malformed_output"}[kind],
    ]
    usage = json.loads(attempts[1]["settlement_json"])["usage"]
    assert usage["cache_write_5m_tokens"] == 7 and usage["cache_write_1h_tokens"] == 10
    expected = (
        sum(
            b["usage"]["input_tokens"] * 3000
            + b["usage"]["output_tokens"] * 15000
            + b["usage"]["cache_read_input_tokens"] * 300
            for b in (visual, evaluation)
        )
        + 7 * 3750
        + 10 * 6000
    )
    assert (await storage.spend.report())["day_accounted_nanousd"] == expected
    assert all(r["request_id"] == "fixture-request" for r in attempts)


@pytest.mark.parametrize("status", [429, 500])
async def test_each_sdk_retry_has_its_own_reservation_and_outcome(budget, status):
    storage, policy = budget
    calls = []

    async def respond(request):
        calls.append(request)
        attempts = await rows(storage)
        assert len(attempts) == len(calls) and attempts[-1]["state"] == "dispatched"
        if len(calls) == 1:
            return httpx2.Response(
                status,
                headers={"retry-after": "0.001", "request-id": "rejected-fixture"},
                json={"type": "error", "error": {"type": "api_error", "message": "Fixture"}},
            )
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with quality(storage, policy, respond) as q:
        await q._run_visual_analysis(CONTENT, "fixture:retry")
    attempts = await rows(storage)
    assert len(calls) == 2 and len(attempts) == 2
    assert attempts[0]["operation_key"] == attempts[1]["operation_key"]
    assert attempts[0]["settlement_json"] is None
    assert attempts[0]["accounted_nanousd"] == 0 and attempts[0]["cost_basis"] == "provider_error"
    assert attempts[1]["cost_basis"] == "provider_usage"
    assert (await storage.spend.report())["held_nanousd"] == 0


@pytest.mark.parametrize("scenario", ["timeout", "disconnect", "gateway_html"])
async def test_ambiguous_outcome_does_not_repeat_even_with_a_new_client(budget, scenario):
    storage, policy = budget
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if scenario == "timeout":
            raise httpx2.ReadTimeout("fixture", request=request)
        if scenario == "disconnect":
            raise httpx2.ReadError("fixture", request=request)
        return httpx2.Response(503, text="<html>Gateway error</html>")

    async with quality(storage, policy, respond) as q:
        with pytest.raises(APIUnavailableError):
            await q._run_visual_analysis(CONTENT, "fixture:uncertain")
    async with quality(storage, policy, respond) as restarted:
        with pytest.raises(SpendDeferred, match="operation_unresolved"):
            await restarted._run_visual_analysis(CONTENT, "fixture:uncertain")
    assert calls == 1
    report = await storage.spend.report()
    assert report["held_nanousd"] == 6_315_000_000
    assert len(report["unresolved"]) == 1 and report["unresolved"][0]["state"] == "unknown"


@pytest.mark.parametrize(
    "scenario", ["missing", "negative", "cache_mismatch", "priority", "model", "tools"]
)
async def test_unknown_billing_preserves_reservation_and_request_id(budget, scenario):
    storage, policy = budget
    response = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    if scenario == "missing":
        del response["usage"]["output_tokens"]
    elif scenario == "negative":
        response["usage"]["input_tokens"] = -1
    elif scenario == "cache_mismatch":
        response["usage"]["cache_creation"] = {
            "ephemeral_5m_input_tokens": 3,
            "ephemeral_1h_input_tokens": 7,
        }
    elif scenario == "priority":
        response["usage"]["service_tier"] = "priority"
    elif scenario == "model":
        response["model"] = "unpriced-model"
    else:
        response["usage"]["server_tool_use"] = {"web_search_requests": 1}
    async with quality(
        storage,
        policy,
        lambda _: httpx2.Response(200, json=response, headers={"request-id": "unknown-fixture"}),
    ) as q:
        with pytest.raises(APIUnavailableError, match="unpriced_provider_response"):
            await q._run_visual_analysis(CONTENT, "fixture:billing")
    report = await storage.spend.report()
    assert report["held_nanousd"] == 6_315_000_000
    assert report["unresolved"][0]["request_id"] == "unknown-fixture"
    assert (await rows(storage))[0]["settlement_json"] is None


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
    budget, status, error_type, message, result
):
    storage, policy = budget
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(
            status,
            headers={"x-should-retry": "true"},
            json={"type": "error", "error": {"type": error_type, "message": message}},
        )

    async with quality(storage, policy, respond) as q:
        with pytest.raises((APIUnavailableError, AnalysisOutputError)):
            await q._run_visual_analysis(CONTENT, "fixture:permanent")
    assert calls == 1
    attempts = await rows(storage)
    assert attempts[0]["result"] == result and attempts[0]["accounted_nanousd"] == 0
    assert attempts[0]["cost_basis"] == "provider_error"


@pytest.mark.parametrize("status,error_type", [(409, "conflict_error"), (504, "timeout_error")])
async def test_documented_transient_error_envelopes_settle_each_rejection(
    budget, status, error_type
):
    storage, policy = budget
    async with quality(
        storage,
        policy,
        lambda _: httpx2.Response(
            status,
            json={"type": "error", "error": {"type": error_type, "message": "Try later"}},
        ),
    ) as q:
        with pytest.raises((APIUnavailableError, AnalysisOutputError)):
            await q._run_visual_analysis(CONTENT, "fixture:transient")
    attempts = await rows(storage)
    assert len(attempts) == 2  # The explicit legacy SDK policy permits one retry.
    assert all(
        attempt["state"] == "settled"
        and attempt["result"] == "transient"
        and attempt["cost_basis"] == "provider_error"
        and attempt["accounted_nanousd"] == 0
        for attempt in attempts
    )


async def test_unrecognised_provider_error_does_not_discard_liability(budget):
    storage, policy = budget
    async with quality(
        storage,
        policy,
        lambda _: httpx2.Response(
            500,
            json={"type": "error", "error": {"type": "future_error", "message": "Unknown"}},
        ),
    ) as q:
        with pytest.raises(APIUnavailableError, match="unpriced_provider_response"):
            await q._run_visual_analysis(CONTENT, "fixture:unknown-error")
    attempts = await rows(storage)
    assert len(attempts) == 1
    assert attempts[0]["state"] == "unknown"
    assert attempts[0]["cost_basis"] is None
    assert (await storage.spend.report())["held_nanousd"] > 0


async def test_cancellation_after_response_finishes_accounting(budget, monkeypatch):
    storage, policy = budget
    entered, release = asyncio.Event(), asyncio.Event()
    original = storage.spend.settle_usage

    async def settle(attempt, usage, *, checkpoint=None):
        entered.set()
        await release.wait()
        await original(attempt, usage, checkpoint=checkpoint)

    monkeypatch.setattr(storage.spend, "settle_usage", settle)
    async with quality(
        storage, policy, lambda _: httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
    ) as q:
        task = asyncio.create_task(q._run_visual_analysis(CONTENT, "fixture:cancel-accounting"))
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


@pytest.mark.parametrize("entry", ["initial", "queued", "command"])
async def test_all_processing_entry_points_defer_without_spending_or_losing_results(
    entry, cached_analysis_property, tmp_path, monkeypatch, make_quality_analysis
):
    merged, _ = cached_analysis_property
    cap = SpendPolicy(per_call_usd="0", daily_usd="0", monthly_usd="0")
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "properties.db"),
        anthropic_api_key=SecretStr("fixture"),
        enable_quality_filter=True,
        analysis_spend_policy=cap,
        require_floorplan=False,
    )
    native = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: pytest.fail("Budget is zero"))
    )

    class NativeFilter(PropertyQualityFilter):
        def __init__(self, **kwargs):
            super().__init__(http_client=native, **kwargs)

    monkeypatch.setattr("home_finder.pipeline.analysis.PropertyQualityFilter", NativeFilter)
    monkeypatch.setattr("home_finder.pipeline.analysis._lookup_wards", AsyncMock())
    monkeypatch.setattr(
        "home_finder.pipeline.analysis._re_enrich_incomplete",
        AsyncMock(side_effect=lambda queue, *args: queue),
    )
    admission_calls = []
    admit = SpendRepository.admit

    async def observe_admission(self, spec, policy):
        admission_calls.append(spec.phase)
        return await admit(self, spec, policy)

    monkeypatch.setattr(SpendRepository, "admit", observe_admission)
    async with PropertyStorage(settings.database_path) as storage:
        old = make_quality_analysis()
        if entry != "initial":
            await storage.save_merged_property(merged)
            await storage.save_property_images(merged.unique_id, list(merged.images))
            await storage.save_quality_analysis(merged.unique_id, old)
            old = await storage.get_quality_analysis(merged.unique_id)
            await storage.pipeline.request_reanalysis([merged.unique_id])
            assert (await storage.pipeline.get_reanalysis_queue())[0].images
        if entry == "initial":
            callback = AsyncMock()
            run = await storage.pipeline.create_pipeline_run()
            count, _ = await _run_quality_and_save(
                PreAnalysisResult(merged_to_process=[merged]),
                settings,
                storage,
                callback,
                recorder=EventRecorder(storage, run),
            )
            assert count == 0
            callback.assert_not_awaited()
            assert await storage.get_quality_analysis(merged.unique_id) is None
            assert len(await storage.pipeline.get_pending_analysis_properties()) == 1
            async with storage._read_connection() as conn:
                async with conn.execute("SELECT analysis_attempts FROM properties") as cur:
                    assert (await cur.fetchone())[0] == 0
                async with conn.execute("SELECT metadata_json FROM property_events") as cur:
                    assert json.loads((await cur.fetchone())[0])["reason"] == "spend_per_call_limit"
        elif entry == "queued":
            assert await _drain_reanalysis_queue(settings, storage) == 0
        else:
            await run_reanalysis(settings)
        if entry != "initial":
            assert await storage.get_quality_analysis(merged.unique_id) == old
            assert len(await storage.pipeline.get_reanalysis_queue()) == 1
        assert await rows(storage) == []
        assert admission_calls == ["visual"]
    assert native.is_closed


async def test_phase_two_needs_its_own_admission(budget, cached_analysis_property):
    storage, _ = budget
    policy = SpendPolicy(per_call_usd="6.03", daily_usd="6.03", monthly_usd="6.03")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    merged, data_dir = cached_analysis_property
    config = AnalysisConfig.model_validate({"visual": {"max_output_tokens": 1024}})
    async with quality(storage, policy, respond, analysis_config=config) as q:
        with pytest.raises(SpendDeferred, match="per_call_limit"):
            await q.analyze_single_merged(merged, data_dir=data_dir)
    assert len(calls) == 1
    attempts = await rows(storage)
    assert len(attempts) == 1 and attempts[0]["state"] == "settled"
    assert attempts[0]["phase"] == "visual" and attempts[0]["accounted_nanousd"] > 0


async def test_provider_redirect_cannot_dispatch_a_second_unmetered_post(budget):
    storage, policy = budget
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(307, headers={"location": "https://example.com/redirect"}, json={})

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond), follow_redirects=True)
    async with PropertyQualityFilter(
        "fixture", http_client=native, spend_repository=storage.spend, spend_policy=policy
    ) as q:
        with pytest.raises(APIUnavailableError):
            await q._run_visual_analysis(CONTENT, "fixture:redirect")
    assert len(calls) == 1 and str(calls[0].url).startswith("https://api.anthropic.com/")
    assert (await storage.spend.report())["held_nanousd"] == 6_315_000_000


@pytest.mark.parametrize("boundary", ["admit", "settle_usage"])
async def test_ledger_failure_stops_sdk_retries(budget, monkeypatch, boundary):
    storage, policy = budget
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    monkeypatch.setattr(storage.spend, boundary, AsyncMock(side_effect=OSError("fixture")))
    async with quality(storage, policy, respond) as q:
        with pytest.raises(APIUnavailableError, match="ledger_unavailable"):
            await q._run_visual_analysis(CONTENT, "fixture:ledger-failure")
    assert len(calls) == (0 if boundary == "admit" else 1)
    report = await storage.spend.report()
    assert report["held_nanousd"] == (0 if boundary == "admit" else 6_315_000_000)


async def test_uncharged_sdk_rejections_do_not_exhaust_future_paid_attempts(budget):
    storage, cap = budget
    cap = cap.model_copy(update={"max_attempts_per_operation": 1})
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(
            429,
            headers={"retry-after": "0.001"},
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "fixture"}},
        )

    async with quality(storage, cap, respond) as q:
        with pytest.raises(APIUnavailableError):
            await q._run_visual_analysis(CONTENT, "fixture:retry-cap")
    # SDK retry bounds still apply to each run. A later recovered request can
    # use the one allowed paid attempt because both rejections were uncharged.
    assert len(calls) == 2 and (await storage.spend.report())["provider_rejections"] == 2
    async with quality(
        storage, cap, lambda _: httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
    ) as q:
        await q._run_visual_analysis(CONTENT, "fixture:retry-cap")
        # Successful visual work is reusable even when no further paid attempt
        # is allowed. The receipt count below proves this did not dispatch again.
        await q._run_visual_analysis(CONTENT, "fixture:retry-cap")
    assert len(await rows(storage)) == 3


async def test_provider_timeout_drains_other_inflight_requests_without_cancelling_them(
    budget, make_merged_property
):
    storage, policy = budget
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

    async with quality(storage, policy, respond) as q:

        async def analyze(merged):
            await q._run_visual_analysis(CONTENT, merged.unique_id)
            return merged, None

        complete = AsyncMock()
        complete.return_value = None
        count = await asyncio.wait_for(
            _run_concurrent_analysis(
                items, analyze, complete, concurrency=2, on_deferred=release_success.set
            ),
            timeout=2,
        )
    assert count == 1 and dispatched == 2
    assert [row["state"] for row in await rows(storage)] == ["unknown", "settled"]
    assert (await storage.spend.report())["held_nanousd"] == 6_315_000_000
    assert complete.await_args.args[0].unique_id == items[1].unique_id


async def test_native_concurrency_cannot_bypass_admission(budget):
    storage, cap = budget
    cap = cap.model_copy(update={"max_in_flight": 1})
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def respond(request):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with quality(storage, cap, respond) as q:
        first = asyncio.create_task(q._run_visual_analysis(CONTENT, "fixture:first"))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            others = await asyncio.gather(
                *(q._run_visual_analysis(CONTENT, f"fixture:other-{i}") for i in range(5)),
                return_exceptions=True,
            )
            assert all(
                isinstance(exc, SpendDeferred) and exc.reason == "concurrency_limit"
                for exc in others
            )
        finally:
            release.set()
            await first
    assert calls == 1 and (await rows(storage))[0]["property_id"] == "fixture:first"


@pytest.mark.parametrize("scenario", ["timeout", "invalid_usage", "cancelled"])
async def test_failed_uncertainty_write_keeps_durable_hold_and_stops_processing(
    budget, monkeypatch, scenario
):
    storage, policy = budget
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
    async with quality(storage, policy, respond) as q:
        if scenario == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await q._run_visual_analysis(CONTENT, "fixture:uncertainty-write")
        else:
            with pytest.raises(APIUnavailableError, match="ledger_unavailable"):
                await q._run_visual_analysis(CONTENT, "fixture:uncertainty-write")
    assert calls == 1
    report = await storage.spend.report()
    assert report["held_nanousd"] == 6_315_000_000
    assert report["unresolved"][0]["state"] == "dispatched"


@pytest.mark.parametrize("control", [{"type": "ephemeral", "ttl": "1h"}, {"type": "unknown"}])
async def test_request_cannot_silently_change_cache_billing(budget, control):
    storage, policy = budget
    content = [{"type": "text", "text": "Fixture", "cache_control": control}]
    async with quality(storage, policy, lambda _: pytest.fail("Unpriced request")) as q:
        with pytest.raises(SpendDeferred, match="unpriced_request"):
            await q._run_visual_analysis(content, "fixture:cache-ttl")
    assert await rows(storage) == []
