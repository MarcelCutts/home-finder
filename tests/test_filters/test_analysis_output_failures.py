"""Both native SDK phases must complete before an assessment is usable."""

import copy
import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.analysis_contracts import (
    MAX_OUTPUT_CHARACTERS,
    AnalysisOutputError,
    validate_output,
)
from home_finder.filters.quality import (
    PropertyQualityFilter,
    _EvaluationResponse,
    _VisualAnalysisResponse,
)
from home_finder.pipeline.analysis import _run_quality_and_save
from home_finder.pipeline.event_recorder import EventRecorder
from home_finder.pipeline.stages import PreAnalysisResult
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)

pytestmark = pytest.mark.usefixtures("native_analysis_spending")


@pytest.mark.parametrize("prose", ["[Not visible in photos]", '{"source": "listing"}', " [] "])
def test_validated_prose_survives_assembly_without_mutating_phase_inputs(prose):
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    evaluation = json.loads(PHASE2_EVALUATION_RESPONSE["content"][0]["text"])
    visual["kitchen"]["notes"] = prose
    evaluation["value_for_quality"]["reasoning"] = prose
    visual = validate_output(visual, _VisualAnalysisResponse, "visual", None)
    evaluation = validate_output(evaluation, _EvaluationResponse, "evaluation", None)
    before_visual, before_evaluation = copy.deepcopy(visual), copy.deepcopy(evaluation)

    for _ in range(2):
        analysis = PropertyQualityFilter._merge_analysis_results(visual, evaluation, 2, "fixture")
        assert analysis is not None
        assert analysis.kitchen.notes == prose
        assert analysis.value.quality_adjusted_note == prose
        assert visual == before_visual
        assert evaluation == before_evaluation


def failed_message(phase, scenario):
    data = copy.deepcopy(
        PHASE1_VISUAL_RESPONSE if phase == "visual" else PHASE2_EVALUATION_RESPONSE
    )
    data["usage"]["cache_creation_input_tokens"] = 17
    if scenario in {"refusal", "max_tokens", "model_context_window_exceeded", "pause_turn"}:
        # The payload is otherwise complete: the stop reason still takes precedence.
        data["stop_reason"] = scenario
    elif scenario == "wrong_tool":
        data["content"][0]["name"] = "unrequested_tool"
    elif scenario == "two_tools":
        data["content"] *= 2
    elif scenario == "missing_tool":
        data["content"] = [{"type": "text", "text": "{}"}]
    elif scenario == "missing_visual_field":
        del data["content"][0]["input"]["kitchen"]["has_dishwasher"]
    elif scenario == "string_boolean":
        data["content"][0]["input"]["space"]["is_spacious_enough"] = "false"
    elif scenario == "invalid_rating":
        data["content"][0]["input"]["overall_rating"] = 6
    elif scenario in {"fractional_rating", "boolean_rating", "string_rating"}:
        data["content"][0]["input"]["overall_rating"] = {
            "fractional_rating": 4.5,
            "boolean_rating": True,
            "string_rating": "4",
        }[scenario]
    elif scenario == "null_notes":
        data["content"][0]["input"]["kitchen"]["notes"] = None
    elif scenario == "missing_notes":
        del data["content"][0]["input"]["kitchen"]["notes"]
    elif scenario == "oversized_visual":
        data["content"][0]["input"]["unused_extension"] = "x" * MAX_OUTPUT_CHARACTERS
    elif scenario == "missing_eval_field":
        payload = json.loads(data["content"][0]["text"])
        del payload["viewing_notes"]
        data["content"][0]["text"] = json.dumps(payload)
    elif scenario == "invalid_enum":
        payload = json.loads(data["content"][0]["text"])
        payload["lowlights"] = ["Invented category"]
        data["content"][0]["text"] = json.dumps(payload)
    elif scenario == "wrapped_one_line":
        payload = json.loads(data["content"][0]["text"])
        payload["one_line"] = {"one_line": "Legacy wrapped text"}
        data["content"][0]["text"] = json.dumps(payload)
    elif scenario == "duplicate_field":
        data["content"][0]["text"] = '{"one_line":"first",' + data["content"][0]["text"][1:]
    elif scenario == "two_text_blocks":
        data["content"] *= 2
    elif scenario == "malformed_json":
        data["content"][0]["text"] = '{"broken"'
    elif scenario == "oversized_evaluation":
        data["content"][0]["text"] = " " * (MAX_OUTPUT_CHARACTERS + 1)
    else:
        raise AssertionError(scenario)
    return data


@pytest.mark.parametrize(
    "phase,scenario,kind",
    [
        ("visual", "refusal", "refusal"),
        ("evaluation", "refusal", "refusal"),
        ("visual", "max_tokens", "truncated"),
        ("evaluation", "max_tokens", "truncated"),
        ("evaluation", "model_context_window_exceeded", "truncated"),
        ("visual", "pause_turn", "unexpected_stop_reason"),
        ("evaluation", "pause_turn", "unexpected_stop_reason"),
        ("visual", "wrong_tool", "malformed_output"),
        ("visual", "two_tools", "malformed_output"),
        ("visual", "missing_tool", "malformed_output"),
        ("visual", "missing_visual_field", "malformed_output"),
        ("visual", "string_boolean", "malformed_output"),
        ("visual", "invalid_rating", "malformed_output"),
        ("visual", "fractional_rating", "malformed_output"),
        ("visual", "boolean_rating", "malformed_output"),
        ("visual", "string_rating", "malformed_output"),
        ("visual", "null_notes", "malformed_output"),
        ("visual", "missing_notes", "malformed_output"),
        ("visual", "oversized_visual", "oversized_output"),
        ("evaluation", "missing_eval_field", "malformed_output"),
        ("evaluation", "invalid_enum", "malformed_output"),
        ("evaluation", "wrapped_one_line", "malformed_output"),
        ("evaluation", "duplicate_field", "malformed_output"),
        ("evaluation", "two_text_blocks", "malformed_output"),
        ("evaluation", "malformed_json", "malformed_output"),
        ("evaluation", "oversized_evaluation", "oversized_output"),
    ],
)
async def test_failed_required_phase_retains_usage_but_never_completed_facts(
    cached_analysis_property, phase, scenario, kind
):
    bodies = ([] if phase == "visual" else [copy.deepcopy(PHASE1_VISUAL_RESPONSE)]) + [
        failed_message(phase, scenario)
    ]
    pending = iter(bodies)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=next(pending), headers={"request-id": "fixture-request"})

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    merged, data_dir = cached_analysis_property
    async with PropertyQualityFilter("fixture", http_client=native) as q:
        _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
    assert result is not None and result.overall_rating is None
    assert result.analysis_failure.phase == phase and result.analysis_failure.kind == kind
    assert result.analysis_failure.request_id == "fixture-request"
    assert result.analysis_provenance.version == 3
    assert result.kitchen.overall_quality == "unknown" and result.listing_extraction is None
    assert result.value.quality_adjusted_rating is None
    assert "No images" not in result.summary
    assert len(calls) == len(bodies) and native.is_closed
    assert q.token_usage.input_tokens == sum(b["usage"]["input_tokens"] for b in bodies)
    assert q.token_usage.output_tokens == sum(b["usage"]["output_tokens"] for b in bodies)
    assert q.token_usage.cache_read_tokens == sum(
        b["usage"]["cache_read_input_tokens"] for b in bodies
    )
    assert q.token_usage.cache_creation_tokens == 17


@pytest.mark.parametrize("phase", ["visual", "evaluation"])
@pytest.mark.parametrize("schema_error", [True, False])
async def test_bad_requests_are_typed_without_inventing_usage(
    cached_analysis_property, phase, schema_error
):
    calls = []

    def respond(request):
        calls.append(request)
        if phase == "evaluation" and len(calls) == 1:
            return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
        return httpx2.Response(
            400,
            headers={"request-id": "rejected-request"},
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "output_config.format.schema: schema too complex"
                    if schema_error
                    else "Invalid request image",
                },
            },
        )

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    merged, data_dir = cached_analysis_property
    async with PropertyQualityFilter("fixture", http_client=native) as q:
        _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
    assert result.overall_rating is None and result.analysis_failure.phase == phase
    assert result.analysis_failure.kind == (
        "unsupported_schema" if schema_error else "request_rejected"
    )
    assert result.analysis_failure.request_id == "rejected-request"
    assert len(calls) == (1 if phase == "visual" else 2)
    assert q.token_usage.input_tokens == (
        0 if phase == "visual" else PHASE1_VISUAL_RESPONSE["usage"]["input_tokens"]
    )
    assert q._breaker.failure_count == 0


async def test_lossless_enum_casing_and_supported_nullable_fields(cached_analysis_property):
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    visual["content"][0]["input"]["kitchen"]["overall_quality"] = "MODERN"
    visual["content"][0]["input"]["space"]["living_room_sqm"] = None
    visual["content"][0]["input"]["space"]["area_estimation_method"] = "PARTIAL_DIMENSIONS"
    visual["content"][0]["input"]["analysis_failure"] = {"phase": "visual", "kind": "refusal"}
    evaluation = copy.deepcopy(PHASE2_EVALUATION_RESPONSE)
    payload = json.loads(evaluation["content"][0]["text"])
    payload["highlights"] = ["gas hob"]
    evaluation["content"][0]["text"] = json.dumps(payload)
    bodies = iter([visual, evaluation])
    native = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=next(bodies)))
    )
    merged, data_dir = cached_analysis_property
    async with PropertyQualityFilter("fixture", http_client=native) as q:
        _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
    assert result.overall_rating == 4 and result.analysis_failure is None
    assert result.kitchen.overall_quality == "modern" and result.space.living_room_sqm is None
    assert result.space.area_estimation_method == "partial_dimensions"
    assert result.highlights == ["Gas hob"]


async def test_integral_json_numbers_complete_both_native_phases(cached_analysis_property):
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    visual["content"][0]["input"]["overall_rating"] = 4.0
    visual["content"][0]["input"]["listing_red_flags"]["red_flag_count"] = 0.0
    visual["content"][0]["input"]["floorplan_detected_in_gallery"] = [1.0]
    evaluation = copy.deepcopy(PHASE2_EVALUATION_RESPONSE)
    payload = json.loads(evaluation["content"][0]["text"])
    payload["listing_extraction"]["deposit_weeks"] = 5.0
    # The other nullable integer, service_charge_pcm, deliberately stays null.
    evaluation["content"][0]["text"] = json.dumps(payload)
    bodies = iter([visual, evaluation])
    native = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=next(bodies)))
    )
    merged, data_dir = cached_analysis_property
    async with PropertyQualityFilter("fixture", http_client=native) as q:
        _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
    assert result.overall_rating == 4 and type(result.overall_rating) is int
    assert result.analysis_failure is None
    assert result.listing_red_flags.red_flag_count == 0
    assert result.listing_extraction.deposit_weeks == 5
    assert result.listing_extraction.service_charge_pcm is None


@pytest.mark.parametrize("phase", ["visual", "evaluation"])
async def test_permission_error_is_a_request_rejection(cached_analysis_property, phase):
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if phase == "evaluation" and calls == 1:
            return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
        return httpx2.Response(
            403,
            json={"type": "error", "error": {"type": "permission_error", "message": "denied"}},
        )

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    merged, data_dir = cached_analysis_property
    async with PropertyQualityFilter("fixture", http_client=native) as q:
        _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
    assert result.overall_rating is None
    assert result.analysis_failure.phase == phase
    assert result.analysis_failure.kind == "request_rejected"
    assert calls == (1 if phase == "visual" else 2)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_output_cannot_enter_domain_models(number):
    raw = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    raw["space"]["living_room_sqm"] = number
    with pytest.raises(AnalysisOutputError) as caught:
        validate_output(raw, _VisualAnalysisResponse, "visual", None)
    assert caught.value.failure.kind == "malformed_output"


@pytest.mark.parametrize("scenario", ["refusal", "max_tokens", "missing_eval_field"])
async def test_rejected_evaluation_stays_pending_without_notifying(
    cached_analysis_property, tmp_path, monkeypatch, scenario, native_analysis_spending
):
    merged, _ = cached_analysis_property
    bodies = [copy.deepcopy(PHASE1_VISUAL_RESPONSE), failed_message("evaluation", scenario)]
    for body in bodies:
        body["model"] = "claude-sonnet-5-5"  # Current Settings default used by the pipeline.
    pending = iter(bodies)
    native = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(
                200, json=next(pending), headers={"request-id": "pipeline-fixture"}
            )
        )
    )

    class NativeFixtureFilter(PropertyQualityFilter):
        def __init__(self, **kwargs):
            super().__init__(http_client=native, **kwargs)

    monkeypatch.setattr("home_finder.pipeline.analysis.PropertyQualityFilter", NativeFixtureFilter)
    monkeypatch.setattr("home_finder.pipeline.analysis._lookup_wards", AsyncMock())
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "properties.db"),
        anthropic_api_key=SecretStr("fixture"),
        enable_quality_filter=True,
        analysis_spend_policy=native_analysis_spending[1],
    )
    on_result = AsyncMock()
    async with PropertyStorage(":memory:") as storage:
        run_id = await storage.pipeline.create_pipeline_run()
        recorder = EventRecorder(storage, run_id)
        count, usage = await _run_quality_and_save(
            PreAnalysisResult(merged_to_process=[merged]),
            settings,
            storage,
            on_result,
            recorder=recorder,
        )
        assert count == 0
        assert usage.input_tokens == sum(b["usage"]["input_tokens"] for b in bodies)
        assert usage.output_tokens == sum(b["usage"]["output_tokens"] for b in bodies)
        assert usage.cache_creation_tokens == 17
        on_result.assert_not_awaited()
        assert await storage.get_quality_analysis(merged.unique_id) is None
        assert [p.unique_id for p in await storage.pipeline.get_pending_analysis_properties()] == [
            merged.unique_id
        ]
        async with storage._read_connection() as conn:
            async with conn.execute(
                "SELECT analysis_attempts FROM properties WHERE unique_id = ?", (merged.unique_id,)
            ) as cursor:
                assert (await cursor.fetchone())[0] == 1
            async with conn.execute(
                "SELECT event_type, metadata_json FROM property_events WHERE property_id = ?",
                (merged.unique_id,),
            ) as cursor:
                events = await cursor.fetchall()
        assert len(events) == 1 and events[0]["event_type"] == "analysis_deferred"
        failure = json.loads(events[0]["metadata_json"])["failure"]
        assert failure["phase"] == "evaluation"
        assert (
            failure["kind"]
            == {
                "refusal": "refusal",
                "max_tokens": "truncated",
                "missing_eval_field": "malformed_output",
            }[scenario]
        )
        assert failure["request_id"] == "pipeline-fixture"
    assert native.is_closed
