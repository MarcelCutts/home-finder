"""Effective phase settings, native request overrides and persisted provenance."""

import copy
import json

import httpx2
import pytest
from pydantic import ValidationError

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import PropertyImage, PropertyQualityAnalysis
from home_finder.models.analysis import AnalysisConfig
from tests.test_analysis.test_analyzer import _populate_image_cache
from tests.test_analysis.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)

pytestmark = pytest.mark.usefixtures("native_analysis_spending")


def test_legacy_flag_applies_only_without_explicit_configuration():
    legacy = PropertyQualityAnalyzer("fixture", enable_extended_thinking=False)
    assert legacy.analysis_config.visual.reasoning == "disabled"
    explicit = PropertyQualityAnalyzer(
        "fixture", enable_extended_thinking=False, analysis_config=AnalysisConfig()
    )
    assert explicit.analysis_config.visual.reasoning == "adaptive"
    assert explicit.analysis_config.evaluation.reasoning == "disabled"
    with pytest.raises(AttributeError):
        explicit.analysis_config = legacy.analysis_config


def test_json_environment_is_validated_and_defaults_are_expanded(monkeypatch):
    monkeypatch.setenv(
        "HOME_FINDER_ANALYSIS_CONFIG",
        json.dumps({"visual": {"effort": "medium"}, "evaluation": {"max_output_tokens": 2048}}),
    )
    settings = Settings(_env_file=None)
    config = settings.analysis_config
    assert config is not None and config.visual.effort == "medium"
    assert config.visual.model == config.evaluation.model == "claude-sonnet-5-5"
    assert config.visual.max_output_tokens == 8192
    assert config.evaluation.max_output_tokens == 2048
    assert config.visual.request_timeout_seconds == 240
    assert config.evaluation.output_contract == "evaluation_schema_v1"
    with pytest.raises(ValidationError):
        config.visual.effort = "low"
    # Stored configurations still expand using their historical defaults.
    historical = AnalysisConfig.model_validate({"visual": {"effort": "medium"}})
    assert historical.visual.model == historical.evaluation.model == "claude-sonnet-4-6"
    assert historical.visual.max_output_tokens == 21_000


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "unknown"},
        {"model": "unpriced-model"},
        {"reasoning": "unknown"},
        {"effort": "unknown"},
        {"max_output_tokens": 0},
        {"max_output_tokens": 21_001},
        {"max_output_tokens": True},
        {"sdk_retries": 2},
        {"sdk_retries": True},
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": float("inf")},
        {"request_timeout_seconds": True},
        {"connect_timeout_seconds": 10, "request_timeout_seconds": 5},
        {"output_contract": "speculative_v2"},
        {"max_tokens_typo": 100},
    ],
)
def test_invalid_phase_settings_fail_at_startup(changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_config={"visual": changes})


def test_configuration_fingerprint_tracks_effective_policy_not_api_key():
    first = PropertyQualityAnalyzer("first-secret")._analysis_provenance()
    second = PropertyQualityAnalyzer("second-secret")._analysis_provenance()
    assert first.fingerprint == second.fingerprint
    assert "secret" not in first.model_dump_json()
    changed = PropertyQualityAnalyzer(
        "fixture", analysis_config=AnalysisConfig.model_validate({"visual": {"effort": "low"}})
    )._analysis_provenance()
    assert first.fingerprint != changed.fingerprint
    assert first.visual_system_sha256 == changed.visual_system_sha256
    assert first.visual_output_sha256 == changed.visual_output_sha256
    assert (
        first.fingerprint
        != PropertyQualityAnalyzer("fixture", max_images=5)._analysis_provenance().fingerprint
    )


async def test_per_phase_settings_reach_native_requests_and_retry_options():
    config = AnalysisConfig.model_validate(
        {
            "visual": {
                "reasoning": "disabled",
                "effort": "medium",
                "max_output_tokens": 2048,
                "request_timeout_seconds": 30,
                "connect_timeout_seconds": 2,
                "sdk_retries": 0,
            },
            "evaluation": {
                "reasoning": "adaptive",
                "effort": "low",
                "max_output_tokens": 1024,
                "request_timeout_seconds": 45,
                "connect_timeout_seconds": 3,
                "sdk_retries": 1,
            },
        }
    )
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 2:
            return httpx2.Response(
                429,
                json={"type": "error", "error": {"type": "rate_limit_error", "message": "fixture"}},
                headers={"retry-after": "0.001"},
            )
        return httpx2.Response(
            200, json=PHASE1_VISUAL_RESPONSE if len(requests) == 1 else PHASE2_EVALUATION_RESPONSE
        )

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityAnalyzer("fixture", http_client=native, analysis_config=config) as q:
        visual = await q._run_visual_analysis([{"type": "text", "text": "Fixture"}], "fixture:1")
        result = await q._run_evaluation(
            visual,
            "fixture:1",
            description=None,
            price_pcm=2000,
            bedrooms=1,
            effective_average=2200,
            area_context=None,
            outcode=None,
            council_tax_band_c=None,
            crime_summary=None,
            rent_trend=None,
            energy_estimate=None,
            hosting_tolerance=None,
        )
        assert result and len(requests) == 3
    visual_request, evaluation_request, retry = [json.loads(r.content) for r in requests]
    assert "thinking" not in visual_request
    assert visual_request["tool_choice"]["type"] == "tool"
    assert visual_request["output_config"] == {"effort": "medium"}
    assert visual_request["max_tokens"] == 2048
    assert evaluation_request["thinking"] == {"type": "adaptive"}
    assert evaluation_request["output_config"]["effort"] == "low"
    assert evaluation_request["output_config"]["format"]["type"] == "json_schema"
    assert evaluation_request["max_tokens"] == 1024 and evaluation_request == retry
    assert [r.extensions["timeout"]["read"] for r in requests] == [30, 45, 45]
    assert [r.extensions["timeout"]["connect"] for r in requests] == [2, 3, 3]
    assert q._client is None and native.is_closed


async def test_real_analysis_stores_application_provenance_and_roundtrips(
    make_merged_property, tmp_path
):
    merged = make_merged_property()
    merged = merged.model_copy(
        update={
            "images": (
                PropertyImage(
                    url="https://example.com/fixture.jpg",
                    source=merged.canonical.source,
                    image_type="gallery",
                ),
            )
        }
    )
    _populate_image_cache(str(tmp_path), merged)
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    visual["content"][0]["input"]["analysis_provenance"] = {"sdk_version": "injected"}
    visual["content"][0]["input"]["listing_text_inputs"] = {"description": "injected"}
    responses = iter([visual, PHASE2_EVALUATION_RESPONSE])
    native = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=next(responses)))
    )
    config = AnalysisConfig.model_validate({"visual": {"effort": "medium"}})
    async with PropertyQualityAnalyzer("fixture", http_client=native, analysis_config=config) as q:
        _, analysis = await q.analyze_single_merged(merged, data_dir=str(tmp_path))
        assert analysis is not None and analysis.overall_rating == 4
        assert analysis.analysis_provenance is not None
        assert len(analysis.analysis_provenance.visual_input_sha256) == 64
        assert (
            analysis.analysis_provenance.model_copy(update={"visual_input_sha256": None})
            == q._analysis_provenance()
        )
        assert analysis.listing_text_inputs is not None
        assert analysis.listing_text_inputs.description != "injected"
    async with PropertyStorage(":memory:") as storage:
        await storage.save_property(merged.canonical)
        await storage.save_quality_analysis(merged.unique_id, analysis)
        saved = await storage.get_quality_analysis(merged.unique_id)
    assert saved is not None and saved.analysis_provenance == analysis.analysis_provenance
    assert saved.analysis_provenance.fingerprint == analysis.analysis_provenance.fingerprint
    # Old records have no invented model/settings provenance on read.
    old = analysis.model_dump(mode="json", exclude={"analysis_provenance"})
    assert PropertyQualityAnalysis.model_validate(old).analysis_provenance is None
