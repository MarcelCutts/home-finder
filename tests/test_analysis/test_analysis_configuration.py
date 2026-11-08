"""How Settings resolve the analysis configuration, and what that configuration controls.

Settings give new runs a bounded runtime profile; saved configurations and provenance
keep their historical defaults, and no configuration authorizes spending by itself.
Native requests run through the real SDK with a mock transport.
"""

import copy
import json
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.analysis.prompts import build_user_prompt
from home_finder.analysis.request_budget import input_reservation_tokens
from home_finder.config import Settings, new_runtime_analysis_config
from home_finder.db import PropertyStorage
from home_finder.models import PropertyImage, PropertyQualityAnalysis
from home_finder.models.analysis import (
    AnalysisConfig,
    AnalysisProvenance,
    ImageReviewCoverage,
    PhaseConfig,
)
from home_finder.models.spend import (
    SONNET_55_FIVE_MINUTE_RATES,
    SONNET_55_STANDARD_RATES,
    SpendDeferred,
    SpendPolicy,
)
from tests.support.analysis_responses import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
    response,
)
from tests.support.analysis_spend import CONTENT, rows
from tests.support.images import image_block, populate_image_cache


@pytest.mark.parametrize("images,bound", [(1, 122_000), (8, 160_500), (20, 226_500)])
def test_runtime_default_is_bounded_and_does_not_authorize_spending(images, bound):
    settings = Settings(_env_file=None, quality_filter_max_images=images)
    config = settings.analysis_config
    assert config == new_runtime_analysis_config(max_images=images)
    assert config.visual.input_token_limit == bound
    assert config.evaluation.input_token_limit == 140_000
    assert config.visual.reasoning == "adaptive"
    assert config.evaluation.reasoning == "disabled"
    for phase in (config.visual, config.evaluation):
        assert phase.model == "claude-sonnet-5-5"
        assert phase.effort == "low"
        assert phase.max_output_tokens == 8192
        assert phase.sdk_retries == 0
    assert config.visual.request_timeout_seconds == 240
    assert config.evaluation.request_timeout_seconds == 60
    assert settings.quality_filter_max_images == images
    assert Settings(_env_file=None).quality_filter_max_images == 20
    assert settings.analysis_spend_policy is None
    assert AnalysisConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError):
        config.visual.effort = "medium"
    assert SONNET_55_STANDARD_RATES.reservation(config.visual) == bound * 4000 + 81_920_000
    assert SONNET_55_STANDARD_RATES.reservation(config.evaluation) == 641_920_000
    assert SONNET_55_FIVE_MINUTE_RATES.reservation(config.visual) == bound * 2500 + 81_920_000
    assert SONNET_55_FIVE_MINUTE_RATES.reservation(config.evaluation) == 431_920_000


def test_environment_fields_are_validated_before_runtime_defaults(monkeypatch):
    monkeypatch.setenv("HOME_FINDER_QUALITY_FILTER_MAX_IMAGES", "8")
    monkeypatch.setenv("HOME_FINDER_ENABLE_EXTENDED_THINKING", "false")
    settings = Settings(_env_file=None)
    assert settings.analysis_config.visual.input_token_limit == 160_500
    assert settings.analysis_config.visual.thinking_type == "between_tools"
    assert settings.analysis_config.visual.effort == "low"
    monkeypatch.setenv("HOME_FINDER_QUALITY_FILTER_MAX_IMAGES", "21")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("bad", [0, 21, True])
def test_runtime_factory_rejects_unsupported_image_counts(bad):
    with pytest.raises(ValueError):
        new_runtime_analysis_config(max_images=bad)


def test_settings_field_order_and_explicit_none_reject_ambiguous_fallback():
    fields = list(Settings.model_fields)
    assert fields.index("quality_filter_max_images") < fields.index("analysis_config")
    assert fields.index("enable_extended_thinking") < fields.index("analysis_config")
    with pytest.raises(ValidationError, match="analysis_config cannot be null"):
        Settings(_env_file=None, analysis_config=None)
    for bad in (0, 21, "invalid"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, quality_filter_max_images=bad)


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        '{"visual":{"effort":"medium"}}',
        '{"visual":{"effort":"medium"},"evaluation":{"max_output_tokens":2048}}',
    ],
)
def test_partial_settings_json_overrides_only_supplied_fields(monkeypatch, value):
    monkeypatch.setenv("HOME_FINDER_ANALYSIS_CONFIG", value)
    settings = Settings(_env_file=None, enable_extended_thinking=False)
    expected = new_runtime_analysis_config(enable_extended_thinking=False).model_dump()
    for phase, fields in json.loads(value).items():
        expected[phase].update(fields)
    assert settings.analysis_config == AnalysisConfig.model_validate(expected)
    assert settings.analysis_config.visual.model == "claude-sonnet-5-5"
    assert settings.analysis_config.visual.reasoning == "disabled"


def test_dotenv_null_is_rejected_and_partial_json_inherits_runtime_profile(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("HOME_FINDER_ANALYSIS_CONFIG=null\n")
    with pytest.raises(ValidationError, match="analysis_config cannot be null"):
        Settings(_env_file=dotenv)
    dotenv.write_text('HOME_FINDER_ANALYSIS_CONFIG={"visual":{"effort":"medium"}}\n')
    config = Settings(_env_file=dotenv).analysis_config
    assert config.visual.model == config.evaluation.model == "claude-sonnet-5-5"
    assert config.visual.effort == "medium"
    assert config.visual.input_token_limit == 226_500
    assert config.evaluation == new_runtime_analysis_config().evaluation


def test_explicit_phase_overrides_can_select_model_and_reasoning_without_losing_bounds():
    config = Settings(
        _env_file=None,
        quality_filter_max_images=8,
        enable_extended_thinking=False,
        analysis_config={"visual": {"model": "claude-sonnet-4-6", "reasoning": "adaptive"}},
    ).analysis_config
    assert config.visual.model == "claude-sonnet-4-6"
    assert config.visual.reasoning == "adaptive"
    assert config.visual.input_token_limit == 160_500
    assert config.visual.sdk_retries == 0
    assert config.evaluation == new_runtime_analysis_config().evaluation


def test_explicit_resolved_profile_retains_all_of_its_fields():
    legacy = AnalysisConfig()
    assert Settings(_env_file=None, analysis_config=legacy).analysis_config == legacy
    explicit = legacy.model_dump()
    # Historical serialization omits None limits. A new-run override must
    # explicitly request an unbounded phase rather than inherit it by omission.
    for phase in ("visual", "evaluation"):
        explicit[phase]["input_token_limit"] = None
    assert Settings(_env_file=None, analysis_config=json.dumps(explicit)).analysis_config == legacy


_INVALID_VISUAL_SETTINGS = [
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
    {"effotr": "low"},
]


@pytest.mark.parametrize(
    "override",
    [
        *({"visual": changes} for changes in _INVALID_VISUAL_SETTINGS),
        "null",
        {"visual": None},
        {"unknown_phase": {}},
    ],
)
def test_invalid_analysis_config_fails_at_the_settings_boundary(override):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_config=override)


def test_historical_models_and_provenance_keep_their_identity():
    assert PhaseConfig.model_validate_json("{}").model == "claude-sonnet-4-6"
    legacy = AnalysisConfig.model_validate_json("{}")
    assert legacy.visual.model == legacy.evaluation.model == "claude-sonnet-4-6"
    assert legacy.visual.max_output_tokens == 21_000
    assert legacy.evaluation.max_output_tokens == 4096
    assert legacy.visual.input_token_limit is None
    assert legacy.visual.sdk_retries == 1
    assert PropertyQualityAnalyzer("fixture").analysis_config == legacy
    # A saved partial configuration expands with the historical defaults, not the runtime ones.
    partial = AnalysisConfig.model_validate({"visual": {"effort": "medium"}})
    assert partial.visual.model == partial.evaluation.model == "claude-sonnet-4-6"
    assert partial.visual.max_output_tokens == 21_000
    payload = (
        Path(__file__).parents[1] / "fixtures" / "legacy_sonnet46_provenance.json"
    ).read_text()
    provenance = AnalysisProvenance.model_validate_json(payload)
    assert (
        provenance.fingerprint == "adb93a7be9a07fb8afa52249a351aa18c1562c7384b257f0cf7aac72dd91c50b"
    )
    assert AnalysisProvenance.model_validate_json(provenance.model_dump_json()) == provenance
    assert provenance.configuration == legacy


def test_legacy_thinking_flag_applies_only_without_explicit_configuration():
    legacy = PropertyQualityAnalyzer("fixture", enable_extended_thinking=False)
    assert legacy.analysis_config.visual.reasoning == "disabled"
    explicit = PropertyQualityAnalyzer(
        "fixture", enable_extended_thinking=False, analysis_config=AnalysisConfig()
    )
    assert explicit.analysis_config.visual.reasoning == "adaptive"
    assert explicit.analysis_config.evaluation.reasoning == "disabled"
    with pytest.raises(AttributeError):
        explicit.analysis_config = legacy.analysis_config


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


@pytest.mark.usefixtures("native_analysis_spending")
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

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityAnalyzer(
        "fixture", http_client=http_client, analysis_config=config
    ) as analyzer:
        visual = await analyzer._run_visual_analysis(
            [{"type": "text", "text": "Fixture"}], "fixture:1"
        )
        result = await analyzer._run_evaluation(
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
    assert analyzer._client is None and http_client.is_closed


@pytest.mark.parametrize("images", range(1, 21))
async def test_visual_allowance_covers_configured_images_plus_floorplan_and_listing_context(images):
    config = Settings(_env_file=None, quality_filter_max_images=images).analysis_config
    prompt = build_user_prompt(
        price_pcm=2500,
        bedrooms=2,
        area_average=2450,
        description="x" * 3000,
        features=["x" * 200] * 15,
        listing_address="x" * 500,
        listing_postcode="x" * 32,
        area_context="x" * 6000,
        outcode="E8",
        crime_summary="x" * 1000,
        rent_trend="x" * 1000,
        hosting_tolerance="x" * 1000,
        has_labeled_floorplan=False,
        image_review_coverage=ImageReviewCoverage(
            reviewed_gallery_count=images, available_gallery_count=images + 1
        ),
    )
    image = image_block("red")
    content = []
    for index in range(images):
        content.extend([{"type": "text", "text": f"Gallery image {index + 1}:"}, image])
    content.extend(
        [
            {"type": "text", "text": "Source-declared floorplan."},
            image,
            {"type": "text", "text": prompt},
        ]
    )
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=response())

    async with (
        PropertyStorage(":memory:") as storage,
        PropertyQualityAnalyzer(
            "fixture",
            max_images=images,
            analysis_config=config,
            spend_repository=storage.spend,
            spend_policy=SpendPolicy(per_call_usd="1", daily_usd="1", monthly_usd="1"),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        ) as analyzer,
    ):
        assert await analyzer._run_visual_analysis(content, "fixture:full-context") is not None
    assert len(requests) == 1
    assert input_reservation_tokens(requests[0]) <= config.visual.input_token_limit


@pytest.mark.parametrize("images,thinking", [(8, False), (20, True)])
async def test_runtime_settings_reach_both_native_phases_without_reducing_gallery(
    make_merged_property, tmp_path, images, thinking
):
    settings = Settings(
        _env_file=None, quality_filter_max_images=images, enable_extended_thinking=thinking
    )
    merged = make_merged_property()
    merged = merged.model_copy(
        update={
            "images": tuple(
                PropertyImage(
                    url=f"https://example.com/gallery-{index}.jpg",
                    source=merged.canonical.source,
                    image_type="gallery",
                )
                for index in range(images)
            ),
            "floorplan": None,
        }
    )
    populate_image_cache(str(tmp_path), merged)
    calls = []
    async with PropertyStorage(":memory:") as storage:

        def respond(request):
            body = json.loads(request.content)
            phase = "visual" if "tools" in body else "evaluation"
            assert body["model"] == "claude-sonnet-5-5"
            assert body["max_tokens"] == 8192
            assert body["output_config"]["effort"] == "low"
            assert body["thinking"] == {
                "type": "adaptive" if phase == "visual" and thinking else "between_tools"
            }
            if phase == "visual":
                assert body["tool_choice"] == {"type": "auto"}
                assert (
                    sum(block["type"] == "image" for block in body["messages"][0]["content"])
                    == images
                )
            assert (
                input_reservation_tokens(body)
                <= getattr(settings.analysis_config, phase).input_token_limit
            )
            calls.append(body)
            return httpx2.Response(200, json=response(phase))

        async with PropertyQualityAnalyzer(
            "fixture",
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
            analysis_config=settings.analysis_config,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
            spend_repository=storage.spend,
            spend_policy=SpendPolicy(per_call_usd="1", daily_usd="3", monthly_usd="3"),
        ) as analyzer:
            assert analyzer._get_client().max_retries == 0
            _, analysis = await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))
            assert analysis.overall_rating == 4
            assert analysis.analysis_provenance.configuration == settings.analysis_config
            assert analysis.analysis_provenance.max_images == images
        assert len(calls) == 2
        attempts = await rows(storage)
        assert [attempt["state"] for attempt in attempts] == ["settled", "settled"]
        assert [attempt["model"] for attempt in attempts] == ["claude-sonnet-5-5"] * 2
        assert attempts[0]["reserved_nanousd"] == (483_170_000 if images == 8 else 648_170_000)
        assert attempts[1]["reserved_nanousd"] == 431_920_000


@pytest.mark.parametrize("mode", ["missing_policy", "zero_policy", "oversized_input"])
async def test_runtime_settings_never_bypass_spend_or_input_admission(mode):
    settings = Settings(_env_file=None, anthropic_api_key=SecretStr("fixture"))
    policy = settings.analysis_spend_policy
    if mode != "missing_policy":
        cap = "0" if mode == "zero_policy" else "1"
        policy = SpendPolicy(per_call_usd=cap, daily_usd=cap, monthly_usd=cap)
    content = CONTENT if mode != "oversized_input" else [{"type": "text", "text": "🏠" * 10_000}]
    reason = {
        "missing_policy": "spending_not_configured",
        "zero_policy": "per_call_limit",
        "oversized_input": "input_limit",
    }[mode]
    async with PropertyStorage(":memory:") as storage:
        async with PropertyQualityAnalyzer(
            settings.anthropic_api_key.get_secret_value(),
            analysis_config=settings.analysis_config,
            spend_repository=storage.spend,
            spend_policy=policy,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(lambda _: pytest.fail("Unadmitted paid request"))
            ),
        ) as analyzer:
            with pytest.raises(SpendDeferred, match=reason):
                await analyzer._run_visual_analysis(content, "fixture:runtime-admission")
        assert await rows(storage) == []


@pytest.mark.usefixtures("native_analysis_spending")
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
    populate_image_cache(str(tmp_path), merged)
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    visual["content"][0]["input"]["analysis_provenance"] = {"sdk_version": "injected"}
    visual["content"][0]["input"]["listing_text_inputs"] = {"description": "injected"}
    responses = iter([visual, PHASE2_EVALUATION_RESPONSE])
    http_client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=next(responses)))
    )
    config = AnalysisConfig.model_validate({"visual": {"effort": "medium"}})
    async with PropertyQualityAnalyzer(
        "fixture", http_client=http_client, analysis_config=config
    ) as analyzer:
        _, analysis = await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))
        assert analysis is not None and analysis.overall_rating == 4
        assert analysis.analysis_provenance is not None
        assert len(analysis.analysis_provenance.visual_input_sha256) == 64
        assert (
            analysis.analysis_provenance.model_copy(update={"visual_input_sha256": None})
            == analyzer._analysis_provenance()
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
