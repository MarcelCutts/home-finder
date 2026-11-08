"""What the spend middleware lets through to the provider, using the real SDK.

Nothing is sent without an explicit ledger and policy; only the configured
request shape is priced; and each physical request must fit the caps before it
is dispatched.
"""

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from pydantic import SecretStr

from home_finder.analysis.analyzer import (
    APIUnavailableError,
    PropertyQualityAnalyzer,
    _visual_output,
)
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.spend import SpendRepository
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import SpendDeferred, SpendPolicy, usd_text
from home_finder.pipeline.assessment import process_initial_assessments
from home_finder.pipeline.contracts import CandidateBatch
from home_finder.pipeline.event_recorder import EventRecorder
from home_finder.pipeline.reanalysis import process_reanalysis_queue, run_reanalysis
from tests.support.analysis_config import configuration, policy
from tests.support.analysis_responses import PHASE1_VISUAL_RESPONSE, SONNET55, response
from tests.support.analysis_spend import CONTENT, quality, rows

SONNET46 = "claude-sonnet-4-6"


@pytest.mark.parametrize("missing", ["ledger", "policy", "both"])
async def test_no_implicit_financial_allowance(native_analysis_spending, missing):
    storage, spend_policy = native_analysis_spending
    async with quality(
        None if missing in {"ledger", "both"} else storage,
        None if missing in {"policy", "both"} else spend_policy,
        lambda _: pytest.fail("No request can bypass admission"),
    ) as analyzer:
        with pytest.raises(SpendDeferred, match="spending_not_configured"):
            await analyzer._run_visual_analysis(CONTENT, "fixture:disabled")
    assert await rows(storage) == []


@pytest.mark.parametrize("cap", ["0", "0.431919999", "0.431920000"])
async def test_per_call_cap_admits_a_request_only_when_it_covers_the_reservation(cap):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=response())

    async with (
        PropertyStorage(":memory:") as storage,
        quality(
            storage, policy(per_call_usd=cap), respond, analysis_config=configuration()
        ) as analyzer,
    ):
        if cap == "0.431920000":
            assert await analyzer._run_visual_analysis(CONTENT, "fixture:cap")
            assert len(calls) == 1
        else:
            with pytest.raises(SpendDeferred, match="per_call_limit"):
                await analyzer._run_visual_analysis(CONTENT, "fixture:cap")
            assert not calls and not await rows(storage)


async def test_phase_two_needs_its_own_admission(
    native_analysis_spending, cached_analysis_property
):
    storage, _ = native_analysis_spending
    # This permits the 1,024-output-token visual phase but not evaluation's
    # 4,096-token bound under the five-minute cache reservation policy.
    cap = SpendPolicy(per_call_usd="3.78", daily_usd="3.78", monthly_usd="3.78")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    merged, data_dir = cached_analysis_property
    config = AnalysisConfig.model_validate({"visual": {"max_output_tokens": 1024}})
    async with quality(storage, cap, respond, analysis_config=config) as analyzer:
        with pytest.raises(SpendDeferred, match="per_call_limit"):
            await analyzer.analyze_single_merged(merged, data_dir=data_dir)
    assert len(calls) == 1
    attempts = await rows(storage)
    assert len(attempts) == 1 and attempts[0]["state"] == "settled"
    assert attempts[0]["phase"] == "visual" and attempts[0]["accounted_nanousd"] > 0


async def test_too_large_request_stops_before_admission():
    async with (
        PropertyStorage(":memory:") as storage,
        quality(
            storage,
            policy(),
            lambda _: pytest.fail("No send"),
            analysis_config=configuration(input_token_limit=20_000),
        ) as analyzer,
    ):
        with pytest.raises(SpendDeferred, match="input_limit"):
            await analyzer._run_visual_analysis(
                [{"type": "text", "text": "x" * 30_000}], "fixture:large"
            )
        assert (await storage.spend.report())["attempts"] == 0


@pytest.mark.parametrize("model, reserve", [(SONNET46, 647_880_000), (SONNET55, 431_920_000)])
@pytest.mark.parametrize("location", ["system", "tools", "message"])
@pytest.mark.parametrize(
    "control, allowed",
    [
        ({"type": "ephemeral"}, True),
        ({"type": "ephemeral", "ttl": "5m"}, True),
        ({"type": "ephemeral", "ttl": "1h"}, False),
        ({"type": "ephemeral", "billing_override": "unknown"}, False),
        ({"type": "unknown"}, False),
    ],
)
async def test_five_minute_reservation_is_coupled_to_the_native_request(
    model, reserve, location, control, allowed
):
    calls = []

    async def respond(request):
        calls.append(json.loads(request.content))
        attempt = (await rows(storage))[0]
        assert attempt["state"] == "dispatched"
        assert attempt["reserved_nanousd"] == reserve
        assert attempt["rate_version"].endswith("2026-10-13-v2")
        return httpx2.Response(200, json=response(model=model))

    config = configuration(visual=model, reasoning="disabled")
    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage, policy(per_call_usd=usd_text(reserve)), respond, analysis_config=config
        ) as analyzer:
            request = copy.deepcopy(analyzer._visual_request(CONTENT))
            block = (
                request["messages"][0]["content"][0]
                if location == "message"
                else request[location][0]
            )
            block["cache_control"] = control
            with analyzer._spend_meter.bind(
                "fixture:cache-policy", "visual", analyzer._analysis_provenance(), _visual_output
            ):
                if allowed:
                    await analyzer._get_client().messages.create(**request)
                else:
                    with pytest.raises(SpendDeferred, match="unpriced_request"):
                        await analyzer._get_client().messages.create(**request)
        assert len(calls) == int(allowed)
        assert len(await rows(storage)) == int(allowed)


@pytest.mark.parametrize(
    "override",
    [
        {"thinking": {"type": "disabled"}},
        {"thinking": {"type": "enabled", "budget_tokens": 1000}},
        {"thinking": {"type": "adaptive"}},
        {"thinking": {"type": "between_tools", "display": "summarized"}},
        {"tool_choice": {"type": "tool", "name": "property_visual_analysis"}},
        {"tool_choice": {"type": "any"}},
        {"output_config": {"effort": "max"}},
        {"max_tokens": 8193},
        {"model": SONNET46},
        {"inference_geo": "us"},
        {"service_tier": "auto"},
        {"speed": "fast"},
        {"fallbacks": "default"},
        {"temperature": 0.5},
        {"tools": [{"type": "web_search_20250305", "name": "web_search"}]},
    ],
)
async def test_native_extra_body_cannot_bypass_admission(override):
    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage,
            policy(),
            lambda _: pytest.fail("Unsupported request dispatched"),
            analysis_config=configuration(reasoning="disabled"),
        ) as analyzer:
            with (
                analyzer._spend_meter.bind(
                    "fixture:override", "visual", analyzer._analysis_provenance(), _visual_output
                ),
                pytest.raises(SpendDeferred, match="unpriced_request"),
            ):
                await analyzer._get_client().messages.create(
                    **analyzer._visual_request(CONTENT), extra_body=override
                )
        assert await rows(storage) == []


async def test_native_beta_header_is_not_an_unpriced_escape_hatch():
    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage,
            policy(),
            lambda _: pytest.fail("Unsupported beta dispatched"),
            analysis_config=configuration(),
        ) as analyzer:
            with (
                analyzer._spend_meter.bind(
                    "fixture:beta", "visual", analyzer._analysis_provenance(), _visual_output
                ),
                pytest.raises(SpendDeferred, match="unpriced_request"),
            ):
                await analyzer._get_client().messages.create(
                    **analyzer._visual_request(CONTENT),
                    extra_headers={"Anthropic-Beta": "fixture-beta"},
                )
        assert await rows(storage) == []


async def test_native_sdk_cannot_inherit_beta_modes_from_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "Anthropic-Beta: fixture-unpriced-mode")
    calls = []

    def respond(request):
        assert "anthropic-beta" not in request.headers
        calls.append(request)
        return httpx2.Response(200, json=response())

    async with PropertyStorage(":memory:") as storage:
        async with quality(storage, policy(), respond, analysis_config=configuration()) as analyzer:
            assert await analyzer._run_visual_analysis(CONTENT, "fixture:inherited-beta")
        assert len(calls) == 1 and (await rows(storage))[0]["state"] == "settled"


async def test_provider_redirect_cannot_dispatch_a_second_unmetered_post(
    native_analysis_spending,
):
    storage, spend_policy = native_analysis_spending
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(307, headers={"location": "https://example.com/redirect"}, json={})

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond), follow_redirects=True)
    async with PropertyQualityAnalyzer(
        "fixture", http_client=native, spend_repository=storage.spend, spend_policy=spend_policy
    ) as analyzer:
        with pytest.raises(APIUnavailableError):
            await analyzer._run_visual_analysis(CONTENT, "fixture:redirect")
    assert len(calls) == 1 and str(calls[0].url).startswith("https://api.anthropic.com/")
    assert (await storage.spend.report())["held_nanousd"] == 4_065_000_000


async def test_native_concurrency_cannot_bypass_admission(native_analysis_spending):
    storage, spend_policy = native_analysis_spending
    cap = spend_policy.model_copy(update={"max_in_flight": 1})
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def respond(request):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with quality(storage, cap, respond) as analyzer:
        first = asyncio.create_task(analyzer._run_visual_analysis(CONTENT, "fixture:first"))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            others = await asyncio.gather(
                *(analyzer._run_visual_analysis(CONTENT, f"fixture:other-{i}") for i in range(5)),
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
        transport=httpx2.MockTransport(lambda _: pytest.fail("Spend caps are zero"))
    )

    class NativeAnalyzer(PropertyQualityAnalyzer):
        def __init__(self, **kwargs):
            super().__init__(http_client=native, **kwargs)

    monkeypatch.setattr("home_finder.pipeline.assessment.PropertyQualityAnalyzer", NativeAnalyzer)
    monkeypatch.setattr("home_finder.pipeline.assessment._lookup_wards", AsyncMock())
    monkeypatch.setattr(
        "home_finder.pipeline.image_recovery.restore_missing_analysis_images",
        AsyncMock(side_effect=lambda queue, *args: queue),
    )
    admission_calls = []
    admit = SpendRepository.admit

    async def observe_admission(self, spec, spend_policy):
        admission_calls.append(spec.phase)
        return await admit(self, spec, spend_policy)

    monkeypatch.setattr(SpendRepository, "admit", observe_admission)
    async with PropertyStorage(settings.database_path) as storage:
        old = make_quality_analysis()
        if entry != "initial":
            await storage.save_merged_property(merged)
            await storage.save_property_images(merged.unique_id, list(merged.images))
            await storage.save_quality_analysis(merged.unique_id, old)
            old = await storage.get_quality_analysis(merged.unique_id)
            await storage.processing.request_reanalysis([merged.unique_id])
            assert (await storage.processing.get_reanalysis_queue())[0].images
        if entry == "initial":
            callback = AsyncMock()
            run = await storage.runs.create_pipeline_run()
            assessment = await process_initial_assessments(
                CandidateBatch(candidates=[merged]),
                settings,
                storage,
                callback,
                recorder=EventRecorder(storage, run),
            )
            assert assessment.processed_count == 0
            callback.assert_not_awaited()
            assert await storage.get_quality_analysis(merged.unique_id) is None
            assert len(await storage.processing.get_pending_analysis_properties()) == 1
            async with storage._read_connection() as conn:
                async with conn.execute("SELECT analysis_attempts FROM properties") as cur:
                    assert (await cur.fetchone())[0] == 0
                async with conn.execute("SELECT metadata_json FROM property_events") as cur:
                    assert json.loads((await cur.fetchone())[0])["reason"] == "spend_per_call_limit"
        elif entry == "queued":
            assert (await process_reanalysis_queue(settings, storage)).completed == 0
        else:
            await run_reanalysis(settings)
        if entry != "initial":
            assert await storage.get_quality_analysis(merged.unique_id) == old
            assert len(await storage.processing.get_reanalysis_queue()) == 1
        assert await rows(storage) == []
        assert admission_calls == ["visual"]
    assert native.is_closed
