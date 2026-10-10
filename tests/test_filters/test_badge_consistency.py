"""Badge consistency through native mocked requests and the final merged result."""

import copy
import json

import httpx2
import pytest
import structlog.testing

from home_finder.db import PropertyStorage
from home_finder.filters.badge_consistency import reconcile_analysis_badges
from home_finder.models import PropertyQualityAnalysis
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)
from tests.test_filters.test_sonnet55 import configuration
from tests.test_filters.test_visual_checkpoints import checkpoint_rows, quality


@pytest.fixture(params=["claude-sonnet-4-6", "claude-sonnet-5-5"])
def assemble(request, tmp_path, cached_analysis_property):
    async def run(
        *, visual=None, highlights=None, lowlights=None, bills="unknown", description=None
    ):
        merged, images = cached_analysis_property
        merged = merged.model_copy(
            update={
                "canonical": merged.canonical.model_copy(update={"description": description}),
                "descriptions": {},
                "source_text": {},
            }
        )
        visual_body = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
        visual_data = visual_body["content"][0]["input"]
        for section, fields in (visual or {}).items():
            visual_data[section].update(fields)
        eval_body = copy.deepcopy(PHASE2_EVALUATION_RESPONSE)
        eval_data = json.loads(eval_body["content"][0]["text"])
        eval_data.update(highlights=highlights or [], lowlights=lowlights or [])
        eval_data["listing_extraction"]["bills_included"] = bills
        eval_body["content"][0]["text"] = json.dumps(eval_data)
        calls = []

        def respond(outgoing):
            body = json.loads(outgoing.content)
            calls.append(body)
            response = copy.deepcopy(visual_body if "tools" in body else eval_body)
            response["model"] = request.param
            return httpx2.Response(200, json=response)

        with structlog.testing.capture_logs() as logs:
            async with (
                PropertyStorage(str(tmp_path / "badges.db")) as storage,
                quality(
                    storage, respond, settings=configuration(request.param, request.param)
                ) as analyst,
            ):
                _, result = await analyst.analyze_single_merged(merged, data_dir=images)
                checkpoints = await checkpoint_rows(storage)
        assert result is not None and result.analysis_failure is None
        assert len(calls) == 2 and all(body["model"] == request.param for body in calls)
        # Raw visual observations are neither rewritten nor invalidated by badge checks.
        assert json.loads(checkpoints[0]["data_json"]) == visual_data
        assert result.analysis_provenance.visual_input_sha256 == checkpoints[0]["input_sha256"]
        return result, [log for log in logs if log["event"] == "analysis_badge_dropped"]

    return run


@pytest.mark.parametrize(
    ("hosting", "positive", "negative"),
    [
        ("excellent", True, False),
        ("good", True, False),
        ("poor", False, True),
        ("awkward", False, False),
        ("unknown", False, False),
    ],
)
async def test_hosting_badges_follow_final_layout_without_changing_prose_or_ratings(
    assemble, hosting, positive, negative
):
    result, logs = await assemble(
        visual={"space": {"hosting_layout": hosting, "living_room_sqm": None}},
        highlights=["Modern kitchen", "Great hosting layout", "Spacious living room"],
        lowlights=["Potential traffic noise", "Poor hosting layout"],
    )
    assert result.highlights == [
        "Modern kitchen",
        *(["Great hosting layout"] if positive else []),
        "Spacious living room",
    ]
    assert result.lowlights == [
        "Potential traffic noise",
        *(["Poor hosting layout"] if negative else []),
    ]
    assert result.space.hosting_layout == hosting
    assert result.space.living_room_sqm is None  # No measured-area requirement for spacious badges.
    assert result.summary == PHASE1_VISUAL_RESPONSE["content"][0]["input"]["summary"]
    assert result.overall_rating == 4
    assert len(logs) == 2 - positive - negative


@pytest.mark.parametrize(
    ("bills", "description", "corrected", "keep"),
    [
        ("yes", "All bills included.", "yes", True),
        ("yes", "Bright flat.", "unknown", False),
        ("yes", "Some bills included.", "unknown", False),
        ("yes", "Bills included. Electricity extra.", "unknown", False),
        ("yes", "Bills included. Bills excluded.", "unknown", False),
        ("no", "Bills are not included.", "no", False),
        ("no", "Bills included.", "unknown", False),
        ("unknown", "All bills included.", "unknown", False),
    ],
)
async def test_bills_badge_uses_canonical_correction_not_the_raw_evaluation(
    assemble, bills, description, corrected, keep
):
    result, logs = await assemble(
        bills=bills,
        description=description,
        highlights=["Modern kitchen", "Bills included", "Good natural light"],
    )
    assert result.listing_extraction.bills_included == corrected
    assert result.highlights == [
        "Modern kitchen",
        *(["Bills included"] if keep else []),
        "Good natural light",
    ]
    assert bool(logs) is not keep
    if logs:
        assert logs == [
            {
                "event": "analysis_badge_dropped",
                "log_level": "info",
                "badge": "Bills included",
                "field": "listing_extraction.bills_included",
                "reason": (
                    "missing_structured_support"
                    if corrected == "unknown"
                    else "unsupported_structured_value"
                ),
            }
        ]  # No description, property identifier, URL, model response or other payload.


@pytest.mark.parametrize("state", ["yes", "no", "unknown"])
async def test_appliance_and_glazing_badges_require_matching_tristate_evidence(assemble, state):
    result, logs = await assemble(
        visual={
            "kitchen": {"has_dishwasher": state, "has_washing_machine": state},
            "flooring_noise": {"has_double_glazing": state},
        },
        highlights=["Dishwasher included", "Washing machine", "Double glazing"],
        lowlights=["No dishwasher", "No washing machine"],
    )
    assert result.highlights == (
        ["Dishwasher included", "Washing machine", "Double glazing"] if state == "yes" else []
    )
    assert result.lowlights == (["No dishwasher", "No washing machine"] if state == "no" else [])
    assert len(logs) == 5 - len(result.highlights) - len(result.lowlights)


@pytest.mark.parametrize(
    "present", ["has_balcony", "has_garden", "has_terrace", "has_shared_garden", None]
)
async def test_positive_outdoor_evidence_contradicts_no_outdoor_space(assemble, present):
    outdoor = dict.fromkeys(
        ["has_balcony", "has_garden", "has_terrace", "has_shared_garden"], False
    )
    if present:
        outdoor[present] = True
    result, logs = await assemble(
        visual={"outdoor_space": outdoor},
        highlights=["Private balcony"],
        lowlights=["No outdoor space", "Potential traffic noise"],
    )
    assert result.highlights == ["Private balcony"]  # False defaults do not prove absence.
    assert result.lowlights == (
        ["Potential traffic noise"] if present else ["No outdoor space", "Potential traffic noise"]
    )
    assert len(logs) == bool(present)


async def test_supported_facts_do_not_generate_new_badges(assemble):
    result, logs = await assemble(bills="yes", description="All bills included.")
    assert result.highlights == result.lowlights == []
    assert logs == []


def test_historical_load_is_unchanged_and_explicit_guard_is_idempotent(make_quality_analysis):
    original = make_quality_analysis(
        highlights=["Bills included", "Double glazing", "Modern kitchen"],
        lowlights=["Poor hosting layout"],
        listing_extraction=None,
        flooring_noise=None,
    )
    saved = original.model_dump_json()
    loaded = PropertyQualityAnalysis.model_validate_json(saved)
    assert loaded.highlights == original.highlights and loaded.lowlights == original.lowlights
    loaded_before = loaded.model_dump_json()  # Existing legacy coercions are outside this guard.
    with structlog.testing.capture_logs() as first_logs:
        checked = reconcile_analysis_badges(loaded)
    assert checked.highlights == ["Modern kitchen"] and checked.lowlights == []
    assert len(first_logs) == 3
    with structlog.testing.capture_logs() as repeat_logs:
        assert reconcile_analysis_badges(checked) is checked
    assert repeat_logs == [] and loaded.model_dump_json() == loaded_before
    assert original.model_dump_json() == saved
    no_badges = original.model_copy(update={"highlights": None, "lowlights": None})
    assert reconcile_analysis_badges(no_badges) is no_badges
