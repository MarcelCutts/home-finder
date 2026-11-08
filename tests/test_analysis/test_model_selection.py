"""Choosing a model per phase: supported settings, provenance, request contracts and rates.

Without an explicit configuration the analyzer keeps Sonnet 4.6 and its historical
provenance; explicit configurations, such as the runtime Settings, choose a model
per phase. Native requests run through the real SDK with no network or paid requests.
"""

import json
from pathlib import Path

import httpx2
import pytest
from pydantic import ValidationError

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.analysis.request_budget import input_reservation_tokens
from home_finder.analysis.spend import visual_input_key
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models.analysis import AnalysisConfig, AnalysisProvenance
from home_finder.models.spend import RateCard, rate_card_for
from tests.support.analysis_config import configuration, policy
from tests.support.analysis_responses import SONNET55, response
from tests.support.analysis_spend import CONTENT, quality, rows
from tests.support.visual_checkpoints import checkpoint_rows

SONNET46 = "claude-sonnet-4-6"


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
    assert default.configuration.visual.model == default.configuration.evaluation.model == SONNET46
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
        {"input_token_limit": True},
    ],
)
def test_unpriced_or_unsupported_configuration_fails_at_startup(overrides):
    with pytest.raises(ValidationError):
        configuration(**overrides)


@pytest.mark.parametrize(
    "visual,evaluation,reasoning",
    [
        (SONNET55, SONNET55, "disabled"),
        (SONNET55, SONNET55, "adaptive"),
        (SONNET55, SONNET46, "disabled"),
        (SONNET46, SONNET55, "disabled"),
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

        async with quality(storage, policy(), respond, analysis_config=config) as analyzer:
            merged, images = cached_analysis_property
            _, analysis = await analyzer.analyze_single_merged(merged, data_dir=images)
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
            assert analyzer.token_usage.estimated_cost_usd == pytest.approx(expected_cost / 1e9)
            assert analyzer.token_usage.output_tokens == 400  # thinking is already included
        assert len(requests) == 2
        assert (await storage.spend.report())["held_nanousd"] == 0


async def test_model_switch_invalidates_visual_checkpoint_but_evaluation_switch_reuses_it(tmp_path):
    database = str(tmp_path / "checkpoint.db")
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx2.Response(200, json=response(model=body["model"]))

    keys = []
    for visual, evaluation in ((SONNET46, SONNET46), (SONNET55, SONNET55), (SONNET55, SONNET46)):
        async with PropertyStorage(database) as storage:
            async with quality(
                storage, policy(), respond, analysis_config=configuration(visual, evaluation)
            ) as analyzer:
                keys.append(
                    visual_input_key(
                        "fixture:key",
                        analyzer._visual_request(CONTENT),
                        analyzer._analysis_provenance(),
                    )
                )
                assert await analyzer._run_visual_analysis(CONTENT, "fixture:key")
            assert len(await checkpoint_rows(storage)) == 1
    assert keys[0] != keys[1] == keys[2]
    assert len(calls) == 2 and [body["model"] for body in calls] == [SONNET46, SONNET55]
