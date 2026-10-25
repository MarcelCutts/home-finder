"""Optional Sonnet 5.5 through the real SDK, with no network or paid requests."""

import base64
import copy
import json
from io import BytesIO
from pathlib import Path

import httpx2
import pytest
from PIL import Image
from pydantic import ValidationError

from home_finder.analysis.analyzer import (
    APIUnavailableError,
    PropertyQualityAnalyzer,
    _visual_output,
)
from home_finder.analysis.contracts import AnalysisOutputError
from home_finder.analysis.request_budget import input_reservation_tokens
from home_finder.analysis.spend import reported_usage, visual_input_key
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models.analysis import AnalysisConfig, AnalysisProvenance
from home_finder.models.spend import (
    SONNET_46_FIVE_MINUTE_RATES,
    SONNET_55_FIVE_MINUTE_RATES,
    SONNET_55_STANDARD_RATES,
    SONNET_STANDARD_RATES,
    AttemptSpec,
    RateCard,
    SpendDeferred,
    UsageSettlement,
    rate_card_for,
    usd_text,
)
from tests.support.analysis_config import configuration, policy
from tests.support.analysis_responses import (
    SONNET55,
    USAGE,
    failed_message,
    recorded_malformed_visual,
    response,
)
from tests.support.analysis_spend import CONTENT, quality, rows
from tests.support.visual_checkpoints import checkpoint_rows

LEGACY = "claude-sonnet-4-6"


@pytest.mark.parametrize("case", ["c218d61fc0d__r2", "c6a9db682ca__r0"])
@pytest.mark.parametrize("recovered", [False, True])
async def test_recorded_malformed_receipt_retry_is_bounded_and_checkpointed(case, recovered):
    broken = response(thinking=False)
    broken["content"][0]["input"] = recorded_malformed_visual(case)
    outputs = iter([broken, response(thinking=False) if recovered else broken])
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=next(outputs))

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage, policy(), respond, analysis_config=configuration(reasoning="disabled")
        ) as q:
            if recovered:
                result = await q._run_visual_analysis(CONTENT, "fixture:recorded-failure")
                assert result["kitchen"]["overall_quality"] == "modern"
            else:
                with pytest.raises(AnalysisOutputError):
                    await q._run_visual_analysis(CONTENT, "fixture:recorded-failure")
        assert len(calls) == 2
        assert calls[0].content == calls[1].content
        assert q.token_usage.output_tokens == 2 * USAGE["output_tokens"]
        attempts = await rows(storage)
        assert [attempt["result"] for attempt in attempts] == [
            "malformed_output",
            "success" if recovered else "malformed_output",
        ]
        assert all(attempt["state"] == "settled" for attempt in attempts)
        assert len(await checkpoint_rows(storage)) == int(recovered)


def test_opt_in_settings_and_legacy_provenance_are_stable(monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "__version__", "fixture-sdk")
    default = PropertyQualityAnalyzer("fixture")._analysis_provenance()
    # Captured before the evidence repair. Old records retain their identity;
    # only the deliberately changed prompts/visual schema differ for new work.
    legacy = AnalysisProvenance.model_validate_json(
        (Path(__file__).parents[1] / "fixtures" / "legacy_sonnet46_provenance.json").read_text()
    )
    assert legacy.fingerprint == "adb93a7be9a07fb8afa52249a351aa18c1562c7384b257f0cf7aac72dd91c50b"
    assert {
        key for key, value in default.model_dump().items() if value != legacy.model_dump()[key]
    } == {"visual_system_sha256", "evaluation_system_sha256", "visual_output_sha256"}
    assert default.fingerprint != legacy.fingerprint
    assert default.configuration == AnalysisConfig()
    assert default.configuration.visual.model == default.configuration.evaluation.model == LEGACY
    assert "thinking_type" not in default.model_dump_json()
    assert "effective_effort" not in default.model_dump_json()
    monkeypatch.setenv("HOME_FINDER_ANALYSIS_CONFIG", configuration().model_dump_json())
    assert Settings(_env_file=None).analysis_config == configuration()
    assert configuration().visual.effort == configuration().evaluation.effort == "high"
    assert configuration() == configuration(effort="high")
    for version in (1, 2, 3):
        historical = default.model_dump(mode="json")
        historical["version"] = version
        assert AnalysisProvenance.model_validate_json(json.dumps(historical)).version == version
    assert (
        PropertyQualityAnalyzer("fixture", analysis_config=configuration())._analysis_provenance()
        != default
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "claude-sonnet-5-5-latest"},
        {"model": "claude-sonnet-5"},
        {"reasoning": "between_tools"},
        {"reasoning": "enabled"},
        {"effort": "xhigh"},
        {"effort": "max"},
        {"provider": "bedrock"},
        {"service_tier": "priority"},
        {"inference_geo": "us"},
        {"speed": "fast"},
        {"fallbacks": "default"},
    ],
)
def test_unpriced_or_unsupported_configuration_fails_at_startup(overrides):
    with pytest.raises(ValidationError):
        configuration(**overrides)


def test_both_rate_cards_roundtrip_without_repricing():
    billed = reported_usage(response(), expected_model=SONNET55)
    assert SONNET_55_STANDARD_RATES.cost(billed) == 2_335_000
    assert SONNET_STANDARD_RATES.cost(billed) == 3_547_500
    assert SONNET_55_STANDARD_RATES.reservation(configuration().visual) == 641_920_000
    full = AnalysisConfig.model_validate(
        {"visual": {"model": SONNET55}, "evaluation": {"model": SONNET55}}
    )
    assert SONNET_55_STANDARD_RATES.reservation(full.visual) == 4_210_000_000
    assert SONNET_55_STANDARD_RATES.reservation(full.evaluation) == 4_040_960_000
    for card in (
        SONNET_STANDARD_RATES,
        SONNET_55_STANDARD_RATES,
        SONNET_46_FIVE_MINUTE_RATES,
        SONNET_55_FIVE_MINUTE_RATES,
    ):
        assert RateCard.model_validate_json(card.model_dump_json()) == card
        active = rate_card_for(card.model)
        assert active.cost(billed) == card.cost(billed)
        assert active.reservation_cache_ttl == "5m"
    assert SONNET_55_FIVE_MINUTE_RATES.reservation(configuration().visual) == 431_920_000
    with pytest.raises(ValueError, match="Unpriced model"):
        rate_card_for("claude-sonnet-5")
    with pytest.raises(ValueError, match="Unpriced model"):
        reported_usage({**response(), "model": "unknown"}, expected_model="unknown")
    with pytest.raises(ValueError, match="Unpriced provider/model"):
        SONNET_STANDARD_RATES.reservation(configuration().visual)


@pytest.mark.parametrize(
    "card",
    [
        SONNET_STANDARD_RATES,
        SONNET_55_STANDARD_RATES,
        SONNET_46_FIVE_MINUTE_RATES,
        SONNET_55_FIVE_MINUTE_RATES,
    ],
)
@pytest.mark.parametrize(
    "field",
    [
        "version",
        "model",
        "input_nanousd",
        "output_nanousd",
        "cache_write_5m_nanousd",
        "cache_write_1h_nanousd",
        "cache_read_nanousd",
    ],
)
def test_a_frozen_card_cannot_mix_models_or_rates(card, field):
    other = SONNET_55_STANDARD_RATES if card.model == LEGACY else SONNET_STANDARD_RATES
    hybrid = {**card.model_dump(), field: getattr(other, field)}
    with pytest.raises(ValidationError):
        RateCard.model_validate_json(json.dumps(hybrid))


@pytest.mark.parametrize(
    "visual,evaluation,reasoning",
    [
        (SONNET55, SONNET55, "disabled"),
        (SONNET55, SONNET55, "adaptive"),
        (SONNET55, LEGACY, "disabled"),
        (LEGACY, SONNET55, "disabled"),
        (SONNET55, SONNET55, None),
    ],
)
async def test_native_two_phase_calls_use_model_specific_contracts_and_rates(
    cached_analysis_property, visual, evaluation, reasoning
):
    config = configuration(visual, evaluation, **({"reasoning": reasoning} if reasoning else {}))
    requests = []
    async with PropertyStorage(":memory:") as storage:

        async def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            phase = "visual" if "tools" in body else "evaluation"
            settings = getattr(config, phase)
            assert body["model"] == settings.model
            assert body["service_tier"] == "standard_only"
            assert body["inference_geo"] == "global"
            assert input_reservation_tokens(body) <= settings.input_token_limit
            if settings.model == SONNET55:
                assert body["thinking"] == {
                    "type": "adaptive" if settings.reasoning == "adaptive" else "between_tools"
                }
                assert body["output_config"]["effort"] == "high"
                if phase == "visual":
                    assert body["tool_choice"] == {"type": "auto"}
            else:
                assert "thinking" not in body
                if phase == "visual":
                    assert body["tool_choice"]["type"] == "tool"
            if phase == "evaluation":
                assert body["output_config"]["format"]["type"] == "json_schema"
                assert "tool_choice" not in body
            attempts = await rows(storage)
            assert len(attempts) == len(requests) and attempts[-1]["state"] == "dispatched"
            card = rate_card_for(settings.model)
            assert attempts[-1]["reserved_nanousd"] == card.reservation(settings)
            return httpx2.Response(
                200,
                json=response(
                    phase,
                    settings.model,
                    thinking=phase == "visual" or settings.reasoning == "adaptive",
                ),
            )

        async with quality(storage, policy(), respond, analysis_config=config) as q:
            merged, images = cached_analysis_property
            _, analysis = await q.analyze_single_merged(merged, data_dir=images)
            assert analysis.overall_rating == 4
            assert analysis.analysis_provenance.configuration == config
            attempts = await rows(storage)
            expected_cost = 0
            for attempt, model in zip(attempts, (visual, evaluation), strict=True):
                card = rate_card_for(model)
                assert RateCard.model_validate_json(attempt["rates_json"]) == card
                assert attempt["rate_version"] == card.version
                assert attempt["model"] == model
                assert attempt["state"] == "settled"
                cost = 2_335_000 if model == SONNET55 else 3_547_500
                assert attempt["accounted_nanousd"] == cost
                expected_cost += cost
            assert q.token_usage.estimated_cost_usd == pytest.approx(expected_cost / 1e9)
            assert q.token_usage.output_tokens == 400  # thinking is already included
        assert len(requests) == 2
        assert (await storage.spend.report())["held_nanousd"] == 0


@pytest.mark.parametrize("cap", ["0", "0.431919999", "0.431920000"])
async def test_native_admission_respects_exact_sonnet55_cap(cap):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=response())

    async with (
        PropertyStorage(":memory:") as storage,
        quality(storage, policy(per_call_usd=cap), respond, analysis_config=configuration()) as q,
    ):
        if cap == "0.431920000":
            assert await q._run_visual_analysis(CONTENT, "fixture:cap")
            assert len(calls) == 1
        else:
            with pytest.raises(SpendDeferred, match="per_call_limit"):
                await q._run_visual_analysis(CONTENT, "fixture:cap")
            assert not calls and not await rows(storage)


@pytest.mark.parametrize("model, reserve", [(LEGACY, 647_880_000), (SONNET55, 431_920_000)])
@pytest.mark.parametrize("location", ["system", "tools", "message"])
@pytest.mark.parametrize(
    "control, allowed",
    [
        ({"type": "ephemeral"}, True),
        ({"type": "ephemeral", "ttl": "5m"}, True),
        ({"type": "ephemeral", "ttl": "1h"}, False),
        ({"type": "ephemeral", "billing_override": "unknown"}, False),
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
        ) as q:
            request = copy.deepcopy(q._visual_request(CONTENT))
            block = (
                request["messages"][0]["content"][0]
                if location == "message"
                else request[location][0]
            )
            block["cache_control"] = control
            with q._spend_meter.bind(
                "fixture:cache-policy", "visual", q._analysis_provenance(), _visual_output
            ):
                if allowed:
                    await q._get_client().messages.create(**request)
                else:
                    with pytest.raises(SpendDeferred, match="unpriced_request"):
                        await q._get_client().messages.create(**request)
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
        {"model": LEGACY},
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
        ) as q:
            with (
                q._spend_meter.bind(
                    "fixture:override", "visual", q._analysis_provenance(), _visual_output
                ),
                pytest.raises(SpendDeferred, match="unpriced_request"),
            ):
                await q._get_client().messages.create(
                    **q._visual_request(CONTENT), extra_body=override
                )
        assert await rows(storage) == []


async def test_native_beta_header_is_not_an_unpriced_escape_hatch():
    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage,
            policy(),
            lambda _: pytest.fail("Unsupported beta dispatched"),
            analysis_config=configuration(),
        ) as q:
            with (
                q._spend_meter.bind(
                    "fixture:beta", "visual", q._analysis_provenance(), _visual_output
                ),
                pytest.raises(SpendDeferred, match="unpriced_request"),
            ):
                await q._get_client().messages.create(
                    **q._visual_request(CONTENT), extra_headers={"Anthropic-Beta": "fixture-beta"}
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
        async with quality(storage, policy(), respond, analysis_config=configuration()) as q:
            assert await q._run_visual_analysis(CONTENT, "fixture:inherited-beta")
        assert len(calls) == 1 and (await rows(storage))[0]["state"] == "settled"


def failed_visual(failure):
    output = response(thinking=False)
    if failure == "no_tool":
        output["content"] = [{"type": "text", "text": "No assessment"}]
        output["stop_reason"] = "end_turn"
    elif failure == "wrong_tool":
        output["content"][0]["name"] = "unknown"
    elif failure == "duplicate_tool":
        output["content"].append(copy.deepcopy(output["content"][0]))
    elif failure == "schema":
        del output["content"][0]["input"]["kitchen"]
    else:
        output["stop_reason"] = "refusal" if failure == "refusal" else "max_tokens"
    return output


@pytest.mark.parametrize(
    "failure", ["no_tool", "wrong_tool", "duplicate_tool", "schema", "refusal", "truncated"]
)
async def test_sonnet55_auto_tool_failures_are_billed_and_only_malformed_output_retries(failure):
    output = failed_visual(failure)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=output)

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage,
            policy(),
            respond,
            analysis_config=configuration(reasoning="disabled", sdk_retries=1),
        ) as q:
            with pytest.raises(AnalysisOutputError):
                await q._run_visual_analysis(CONTENT, "fixture:invalid")
        # The SDK never resends a completed response; only the filter's own
        # malformed-output retry does, as a separately admitted identical request.
        retried = failure not in {"refusal", "truncated"}
        assert len(calls) == (2 if retried else 1)
        assert len({call.content for call in calls}) == 1
        attempts = await rows(storage)
        assert len(attempts) == len(calls)
        assert len({attempt["operation_key"] for attempt in attempts}) == 1
        for attempt in attempts:
            assert attempt["state"] == "settled" and attempt["accounted_nanousd"] == 2_335_000
            assert attempt["result"] == ("malformed_output" if retried else failure)
        assert await checkpoint_rows(storage) == []


async def test_malformed_visual_output_is_recovered_by_one_retry():
    outputs = iter([failed_visual("schema"), response(thinking=False)])

    def respond(request):
        return httpx2.Response(200, json=next(outputs))

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage, policy(), respond, analysis_config=configuration(reasoning="disabled")
        ) as q:
            visual = await q._run_visual_analysis(CONTENT, "fixture:recovered")
        assert visual["kitchen"]["overall_quality"] == "modern"
        first, second = await rows(storage)
        assert first["result"] == "malformed_output" and second["result"] == "success"
        assert q.token_usage.output_tokens == 2 * USAGE["output_tokens"]
        assert len(await checkpoint_rows(storage)) == 1


async def test_malformed_visual_retry_respects_the_paid_attempt_cap():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=failed_visual("schema"))

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage,
            policy(max_attempts_per_operation=1),
            respond,
            analysis_config=configuration(reasoning="disabled"),
        ) as q:
            with pytest.raises(SpendDeferred) as caught:
                await q._run_visual_analysis(CONTENT, "fixture:capped")
        assert caught.value.reason == "attempt_limit"
        assert len(calls) == 1 and len(await rows(storage)) == 1


@pytest.mark.parametrize(
    "failure", ["model", "priority", "batch", "geography", "tool", "missing", "new_units", "cache"]
)
async def test_unpriced_sonnet55_response_holds_reservation_without_retry(failure):
    output = response()
    if failure == "model":
        output["model"] = LEGACY  # Even another supported model cannot be silently substituted.
    elif failure in {"priority", "batch"}:
        output["usage"]["service_tier"] = failure
    elif failure == "geography":
        output["usage"]["inference_geo"] = "us"
    elif failure == "tool":
        output["usage"]["server_tool_use"] = {"web_search_requests": 1}
    elif failure == "missing":
        del output["usage"]["output_tokens"]
    elif failure == "new_units":
        output["usage"]["unpriced_units"] = 1
    else:
        output["usage"]["cache_creation"]["ephemeral_5m_input_tokens"] = 11
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=output)

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage, policy(), respond, analysis_config=configuration(sdk_retries=1)
        ) as q:
            with pytest.raises(APIUnavailableError, match="unpriced_provider_response"):
                await q._run_visual_analysis(CONTENT, "fixture:billing")
        assert len(calls) == 1
        assert (await storage.spend.report())["held_nanousd"] == 431_920_000
        assert (await rows(storage))[0]["state"] == "unknown"
        assert await checkpoint_rows(storage) == []


async def test_rate_cards_settle_after_restart_without_using_current_rates(tmp_path, monkeypatch):
    database = str(tmp_path / "rates.db")
    attempts = []
    async with PropertyStorage(database) as storage:
        for model, key in ((LEGACY, "a"), (SONNET55, "b")):
            provenance = PropertyQualityAnalyzer(
                "fixture", analysis_config=configuration(model, model)
            )._analysis_provenance()
            spec = AttemptSpec(
                operation_key=key * 64,
                request_sha256=key * 64,
                property_id=f"fixture:{key}",
                phase="visual",
                provenance=provenance,
            )
            attempt = await storage.spend.admit(spec, policy())
            await storage.spend.mark_dispatched(attempt)
            attempts.append(attempt)
    monkeypatch.setattr(
        "home_finder.db.spend.rate_card_for", lambda _: pytest.fail("Frozen rates required")
    )
    async with PropertyStorage(database) as storage:
        for attempt in attempts:
            await storage.spend.settle_usage(
                attempt,
                UsageSettlement(
                    usage=reported_usage(response(), expected_model=SONNET55), result="success"
                ),
            )
        assert [row["accounted_nanousd"] for row in await rows(storage)] == [3_547_500, 2_335_000]


async def test_model_switch_invalidates_visual_checkpoint_but_evaluation_switch_reuses_it(tmp_path):
    database = str(tmp_path / "checkpoint.db")
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx2.Response(200, json=response(model=body["model"]))

    keys = []
    for visual, evaluation in ((LEGACY, LEGACY), (SONNET55, SONNET55), (SONNET55, LEGACY)):
        async with PropertyStorage(database) as storage:
            async with quality(
                storage, policy(), respond, analysis_config=configuration(visual, evaluation)
            ) as q:
                keys.append(
                    visual_input_key(
                        "fixture:key", q._visual_request(CONTENT), q._analysis_provenance()
                    )
                )
                assert await q._run_visual_analysis(CONTENT, "fixture:key")
            assert len(await checkpoint_rows(storage)) == 1
    assert keys[0] != keys[1] == keys[2]
    assert len(calls) == 2 and [body["model"] for body in calls] == [LEGACY, SONNET55]


def test_input_budget_covers_high_resolution_image_tier_and_new_text_tokenizer():
    q = PropertyQualityAnalyzer("fixture", analysis_config=configuration())
    text = [{"type": "text", "text": "🏠" * 1000}]
    buffer = BytesIO()
    Image.new("RGB", (3840, 2160), "red").save(buffer, "PNG")
    image = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(buffer.getvalue()).decode(),
        },
    }
    # Image size does not discount admission: every static image gets 5,000 tokens.
    plain = input_reservation_tokens(q._visual_request(text))
    with_images = input_reservation_tokens(q._visual_request([*text, *([image] * 21)]))
    assert with_images >= plain + 21 * 5000 > plain + 21 * 4784
    assert plain >= len(json.dumps(q._visual_request(text)).encode()) * 2 + 16_384


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
@pytest.mark.parametrize("status", [429, 503])
async def test_native_retry_is_separately_admitted_and_keeps_sonnet55_effort(effort, status):
    calls = []
    async with PropertyStorage(":memory:") as storage:

        async def respond(request):
            body = json.loads(request.content)
            assert body["thinking"] == {"type": "between_tools"}
            assert body["output_config"]["effort"] == effort
            calls.append(request)
            attempts = await rows(storage)
            assert len(attempts) == len(calls)
            assert attempts[-1]["state"] == "dispatched"
            if len(calls) == 1:
                return httpx2.Response(
                    status,
                    headers={"retry-after": "0.001"},
                    json={"type": "error", "error": {"type": "api_error", "message": "fixture"}},
                )
            return httpx2.Response(200, json=response())

        async with quality(
            storage,
            policy(),
            respond,
            analysis_config=configuration(reasoning="disabled", effort=effort, sdk_retries=1),
        ) as q:
            assert await q._run_visual_analysis(CONTENT, "fixture:retry")
        first, second = await rows(storage)
        assert calls[0].content == calls[1].content
        assert first["operation_key"] == second["operation_key"]
        assert first["accounted_nanousd"] == 0 and first["cost_basis"] == "provider_error"
        assert second["accounted_nanousd"] == 2_335_000
        assert first["reserved_nanousd"] == second["reserved_nanousd"] == 431_920_000
        assert (await storage.spend.report())["held_nanousd"] == 0


@pytest.mark.parametrize(
    "failure,kind",
    [
        ("missing_eval_field", "malformed_output"),
        ("malformed_json", "malformed_output"),
        ("refusal", "refusal"),
        ("model_context_window_exceeded", "truncated"),
    ],
)
async def test_sonnet55_evaluation_failure_preserves_charge_and_visual_checkpoint(
    cached_analysis_property, failure, kind
):
    evaluation = failed_message("evaluation", failure)
    evaluation["model"] = SONNET55
    evaluation["usage"] = copy.deepcopy(USAGE)
    outputs = iter([response(), evaluation])
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=next(outputs))

    async with PropertyStorage(":memory:") as storage:
        async with quality(storage, policy(), respond, analysis_config=configuration()) as q:
            merged, images = cached_analysis_property
            _, analysis = await q.analyze_single_merged(merged, data_dir=images)
            assert analysis.overall_rating is None
            assert analysis.analysis_failure.phase == "evaluation"
            assert analysis.analysis_failure.kind == kind
            assert q.token_usage.estimated_cost_usd == pytest.approx(0.00467)
        attempts = await rows(storage)
        assert len(calls) == len(attempts) == 2
        assert [row["accounted_nanousd"] for row in attempts] == [2_335_000, 2_335_000]
        assert [row["result"] for row in attempts] == ["success", kind]
        assert len(await checkpoint_rows(storage)) == 1


@pytest.mark.parametrize("field", ["model", "rate_version", "rates_json"])
async def test_frozen_card_must_match_admitted_model_and_version(field):
    async with PropertyStorage(":memory:") as storage:
        spec = AttemptSpec(
            operation_key="a" * 64,
            request_sha256="b" * 64,
            property_id="fixture:rate-identity",
            phase="visual",
            provenance=PropertyQualityAnalyzer(
                "fixture", analysis_config=configuration()
            )._analysis_provenance(),
        )
        attempt = await storage.spend.admit(spec, policy())
        await storage.spend.mark_dispatched(attempt)
        replacement = {
            "model": LEGACY,
            "rate_version": SONNET_STANDARD_RATES.version,
            "rates_json": SONNET_STANDARD_RATES.model_dump_json(),
        }[field]
        async with storage._transaction() as conn:
            await conn.execute(
                f"UPDATE paid_attempts SET {field}=? WHERE attempt_id=?", (replacement, attempt)
            )
        with pytest.raises(ValueError, match="Frozen rate card does not match"):
            await storage.spend.settle_usage(
                attempt,
                UsageSettlement(
                    usage=reported_usage(response(), expected_model=SONNET55), result="success"
                ),
            )
        assert (await rows(storage))[0]["state"] == "dispatched"
        assert (await storage.spend.report())["held_nanousd"] == 431_920_000
