"""New Settings policy must not reinterpret saved analysis or authorize spending."""

import json
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from home_finder.config import Settings, new_runtime_analysis_config
from home_finder.db import PropertyStorage
from home_finder.filters.quality import PropertyQualityFilter
from home_finder.filters.quality_prompts import build_user_prompt
from home_finder.filters.request_budget import input_reservation_tokens
from home_finder.models import PropertyImage
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
from tests.test_filters.test_analysis_spend import CONTENT, rows
from tests.test_filters.test_quality import _populate_image_cache
from tests.test_filters.test_sonnet55 import response
from tests.test_filters.test_visual_checkpoints import image_block


@pytest.mark.parametrize("images,bound", [(1, 122_000), (8, 160_500), (20, 226_500)])
def test_settings_default_is_modern_bounded_and_not_spend_authorization(images, bound):
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


def test_historical_models_and_provenance_keep_their_identity():
    assert PhaseConfig.model_validate_json("{}").model == "claude-sonnet-4-6"
    legacy = AnalysisConfig.model_validate_json("{}")
    assert legacy.visual.model == legacy.evaluation.model == "claude-sonnet-4-6"
    assert legacy.visual.max_output_tokens == 21_000
    assert legacy.evaluation.max_output_tokens == 4096
    assert legacy.visual.input_token_limit is None
    assert legacy.visual.sdk_retries == 1
    assert PropertyQualityFilter("fixture").analysis_config == legacy
    payload = (
        Path(__file__).parents[1] / "fixtures" / "legacy_sonnet46_provenance.json"
    ).read_text()
    provenance = AnalysisProvenance.model_validate_json(payload)
    assert (
        provenance.fingerprint == "adb93a7be9a07fb8afa52249a351aa18c1562c7384b257f0cf7aac72dd91c50b"
    )
    assert AnalysisProvenance.model_validate_json(provenance.model_dump_json()) == provenance
    assert provenance.configuration == legacy


@pytest.mark.parametrize("value", ["{}", '{"visual":{"effort":"medium"}}'])
def test_partial_settings_json_overrides_only_supplied_fields(monkeypatch, value):
    monkeypatch.setenv("HOME_FINDER_ANALYSIS_CONFIG", value)
    settings = Settings(_env_file=None, enable_extended_thinking=False)
    expected = new_runtime_analysis_config(enable_extended_thinking=False).model_dump()
    expected["visual"].update(json.loads(value).get("visual", {}))
    assert settings.analysis_config == AnalysisConfig.model_validate(expected)
    assert settings.analysis_config.visual.model == "claude-sonnet-5-5"
    assert settings.analysis_config.visual.reasoning == "disabled"


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


@pytest.mark.parametrize(
    "override",
    ["null", {"visual": None}, {"visual": {"effotr": "low"}}, {"unknown_phase": {}}],
)
def test_invalid_runtime_overrides_fail_at_settings_boundary(override):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_config=override)


def test_explicit_resolved_profile_retains_all_of_its_fields():
    legacy = AnalysisConfig()
    assert Settings(_env_file=None, analysis_config=legacy).analysis_config == legacy
    explicit = legacy.model_dump()
    # Historical serialization omits None limits. A new-run override must
    # explicitly request an unbounded phase rather than inherit it by omission.
    for phase in ("visual", "evaluation"):
        explicit[phase]["input_token_limit"] = None
    assert Settings(_env_file=None, analysis_config=json.dumps(explicit)).analysis_config == legacy


@pytest.mark.parametrize("images", range(1, 21))
async def test_visual_allowance_covers_configured_images_plus_floorplan_and_listing_context(images):
    config = Settings(_env_file=None, quality_filter_max_images=images).analysis_config
    q = PropertyQualityFilter("fixture", max_images=images, analysis_config=config)
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
    body = {
        **q._visual_request(content),
        "service_tier": "standard_only",
        "inference_geo": "global",
    }
    assert input_reservation_tokens(body) <= config.visual.input_token_limit

    requests = []

    def respond(request):
        native = json.loads(request.content)
        assert input_reservation_tokens(native) <= config.visual.input_token_limit
        requests.append(native)
        return httpx2.Response(200, json=response())

    async with (
        PropertyStorage(":memory:") as storage,
        PropertyQualityFilter(
            "fixture",
            max_images=images,
            analysis_config=config,
            spend_repository=storage.spend,
            spend_policy=SpendPolicy(per_call_usd="1", daily_usd="1", monthly_usd="1"),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        ) as native,
    ):
        assert await native._run_visual_analysis(content, "fixture:full-context") is not None
    assert len(requests) == 1


@pytest.mark.parametrize("images,thinking", [(8, False), (20, True)])
async def test_new_settings_reach_both_native_phases_without_reducing_gallery(
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
    _populate_image_cache(str(tmp_path), merged)
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

        native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        async with PropertyQualityFilter(
            "fixture",
            max_images=settings.quality_filter_max_images,
            enable_extended_thinking=settings.enable_extended_thinking,
            analysis_config=settings.analysis_config,
            http_client=native,
            spend_repository=storage.spend,
            spend_policy=SpendPolicy(per_call_usd="1", daily_usd="3", monthly_usd="3"),
        ) as q:
            assert q._get_client().max_retries == 0
            _, analysis = await q.analyze_single_merged(merged, data_dir=str(tmp_path))
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
async def test_modern_runtime_never_bypasses_spend_or_payload_admission(mode):
    settings = Settings(_env_file=None, anthropic_api_key=SecretStr("fixture"))
    budget = settings.analysis_spend_policy
    if mode != "missing_policy":
        cap = "0" if mode == "zero_policy" else "1"
        budget = SpendPolicy(per_call_usd=cap, daily_usd=cap, monthly_usd=cap)
    content = CONTENT if mode != "oversized_input" else [{"type": "text", "text": "🏠" * 10_000}]
    reason = {
        "missing_policy": "spending_not_configured",
        "zero_policy": "per_call_limit",
        "oversized_input": "input_limit",
    }[mode]
    async with PropertyStorage(":memory:") as storage:
        async with PropertyQualityFilter(
            settings.anthropic_api_key.get_secret_value(),
            analysis_config=settings.analysis_config,
            spend_repository=storage.spend,
            spend_policy=budget,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(lambda _: pytest.fail("Unadmitted paid request"))
            ),
        ) as q:
            with pytest.raises(SpendDeferred, match=reason):
                await q._run_visual_analysis(content, "fixture:runtime-admission")
        assert await rows(storage) == []
