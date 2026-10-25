"""Both native SDK phases must complete before an assessment is usable."""

import copy
import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from pydantic import SecretStr

from home_finder.analysis.analyzer import (
    PropertyQualityAnalyzer,
    _EvaluationResponse,
    _VisualAnalysisResponse,
)
from home_finder.analysis.contracts import (
    AnalysisOutputError,
    validate_output,
)
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.pipeline.assessment import process_initial_assessments
from home_finder.pipeline.contracts import CandidateBatch
from home_finder.pipeline.event_recorder import EventRecorder
from tests.support.analysis_responses import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
    failed_message,
    recorded_malformed_visual,
)

pytestmark = pytest.mark.usefixtures("native_analysis_spending")


@pytest.mark.parametrize("case", ["c218d61fc0d__r2", "c6a9db682ca__r0"])
def test_recorded_malformed_sections_are_not_reconstructed(case):
    raw = recorded_malformed_visual(case)
    before = copy.deepcopy(raw)
    with pytest.raises(AnalysisOutputError) as caught:
        validate_output(raw, _VisualAnalysisResponse, "visual", None)
    assert caught.value.failure.kind == "malformed_output"
    assert raw == before


@pytest.mark.parametrize("conflicting_field", ["overall_quality", "has_dishwasher"])
def test_json_text_with_duplicate_facts_is_rejected(conflicting_field):
    raw = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    section = json.dumps(raw["kitchen"])
    # Do not silently pick the last fact, even if that one passes validation.
    first = "dated" if conflicting_field == "overall_quality" else "no"
    raw["kitchen"] = (
        "{" + json.dumps(conflicting_field) + ":" + json.dumps(first) + "," + section[1:]
    )
    with pytest.raises(AnalysisOutputError) as caught:
        validate_output(raw, _VisualAnalysisResponse, "visual", None)
    assert caught.value.failure.kind == "malformed_output"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_json_text_nonfinite_number_is_rejected(number):
    raw = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    raw["space"]["living_room_sqm"] = 20
    raw["space"] = json.dumps(raw["space"]).replace(
        '"living_room_sqm": 20', f'"living_room_sqm": {number}'
    )
    with pytest.raises(AnalysisOutputError) as caught:
        validate_output(raw, _VisualAnalysisResponse, "visual", None)
    assert caught.value.failure.kind == "malformed_output"


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
        analysis = PropertyQualityAnalyzer._merge_analysis_results(visual, evaluation, 2, "fixture")
        assert analysis is not None
        assert analysis.kitchen.notes == prose
        assert analysis.value.quality_adjusted_note == prose
        assert visual == before_visual
        assert evaluation == before_evaluation


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
    repeats = 2 if phase == "visual" and kind == "malformed_output" else 1
    bodies = ([] if phase == "visual" else [copy.deepcopy(PHASE1_VISUAL_RESPONSE)]) + [
        failed_message(phase, scenario) for _ in range(repeats)
    ]
    pending = iter(bodies)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=next(pending), headers={"request-id": "fixture-request"})

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    merged, data_dir = cached_analysis_property
    async with PropertyQualityAnalyzer("fixture", http_client=native) as q:
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
    assert q.token_usage.cache_creation_tokens == 17 * repeats


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
    async with PropertyQualityAnalyzer("fixture", http_client=native) as q:
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
    async with PropertyQualityAnalyzer("fixture", http_client=native) as q:
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
    async with PropertyQualityAnalyzer("fixture", http_client=native) as q:
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
    async with PropertyQualityAnalyzer("fixture", http_client=native) as q:
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

    class NativeFixtureFilter(PropertyQualityAnalyzer):
        def __init__(self, **kwargs):
            super().__init__(http_client=native, **kwargs)

    monkeypatch.setattr(
        "home_finder.pipeline.assessment.PropertyQualityAnalyzer", NativeFixtureFilter
    )
    monkeypatch.setattr("home_finder.pipeline.assessment._lookup_wards", AsyncMock())
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "properties.db"),
        anthropic_api_key=SecretStr("fixture"),
        enable_quality_filter=True,
        analysis_spend_policy=native_analysis_spending[1],
    )
    on_result = AsyncMock()
    async with PropertyStorage(":memory:") as storage:
        run_id = await storage.runs.create_pipeline_run()
        recorder = EventRecorder(storage, run_id)
        assessment = await process_initial_assessments(
            CandidateBatch(candidates=[merged]),
            settings,
            storage,
            on_result,
            recorder=recorder,
        )
        count, usage = assessment.processed_count, assessment.token_usage
        assert count == 0
        assert usage.input_tokens == sum(b["usage"]["input_tokens"] for b in bodies)
        assert usage.output_tokens == sum(b["usage"]["output_tokens"] for b in bodies)
        assert usage.cache_creation_tokens == 17
        on_result.assert_not_awaited()
        assert await storage.get_quality_analysis(merged.unique_id) is None
        assert [
            p.unique_id for p in await storage.processing.get_pending_analysis_properties()
        ] == [merged.unique_id]
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


def test_visual_sections_sent_as_json_text_are_decoded():
    expected = validate_output(
        copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"]),
        _VisualAnalysisResponse,
        "visual",
        None,
    )
    raw = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    raw["kitchen"] = json.dumps(raw["kitchen"])
    raw["listing_red_flags"] = json.dumps(raw["listing_red_flags"])
    raw["condition"]["maintenance_concerns"] = json.dumps(["Scuffed skirting"])
    expected["condition"]["maintenance_concerns"] = ["Scuffed skirting"]
    assert validate_output(raw, _VisualAnalysisResponse, "visual", None) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Shapes seen from Claude on 2026-10-21: an object cut short inside the
        # string with its remaining keys spilled into the parent, and a trailing comma.
        '{"overall_quality": "modern", "hob_type": "gas"',
        '{"overall_quality": "modern", "hob_type": "gas",}',
        '["not", "an", "object"]',
        "modern",
    ],
)
def test_visual_json_text_that_is_not_the_expected_object_still_fails(text):
    raw = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    raw["kitchen"] = text
    with pytest.raises(AnalysisOutputError) as caught:
        validate_output(raw, _VisualAnalysisResponse, "visual", None)
    assert caught.value.failure.kind == "malformed_output"
    assert caught.value.diagnostics["validation_errors"][0]["path"] == ["kitchen"]


def test_evaluation_json_text_is_not_decoded():
    # The evaluation phase is grammar-constrained; text in an object field is a defect.
    raw = json.loads(PHASE2_EVALUATION_RESPONSE["content"][0]["text"])
    raw["viewing_notes"] = json.dumps(raw["viewing_notes"])
    with pytest.raises(AnalysisOutputError) as caught:
        validate_output(raw, _EvaluationResponse, "evaluation", None)
    assert caught.value.failure.kind == "malformed_output"
