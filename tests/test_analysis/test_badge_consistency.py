"""Badge reconciliation against the structured evidence of one new assembly.

The analyzer applies this check after merging both phases and correcting listing
facts; test_badge_assembly.py covers that wiring.
"""

import copy
import json

import pytest
import structlog.testing

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.analysis.badge_consistency import reconcile_analysis_badges
from home_finder.models import PropertyHighlight, PropertyLowlight, PropertyQualityAnalysis
from home_finder.models.quality import OUTDOOR_SPACE_FLAGS
from tests.support.analysis_responses import PHASE1_VISUAL_RESPONSE, PHASE2_EVALUATION_RESPONSE

PROPERTY_ID = "fixture:badges"
MISSING = "missing_structured_support"
UNSUPPORTED = "unsupported_structured_value"
NO_TYPES = dict.fromkeys(OUTDOOR_SPACE_FLAGS, False)
ACCESS_BADGES = [
    "Private balcony",
    "Private garden",
    "Private terrace",
    "Shared garden",
    "Communal gardens",
    "Roof terrace",
]


def assembled(*, visual=None, listing=None, highlights=(), lowlights=()):
    """Merge the fixture's phase outputs with the analyzer's own merge step."""
    data = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    for section, fields in (visual or {}).items():
        data[section].update(fields)
    evaluation = json.loads(PHASE2_EVALUATION_RESPONSE["content"][0]["text"])
    evaluation["listing_extraction"].update(listing or {})
    evaluation.update(highlights=[*highlights], lowlights=[*lowlights])
    analysis = PropertyQualityAnalyzer._merge_analysis_results(
        data, evaluation, bedrooms=1, property_id=PROPERTY_ID
    )
    assert analysis is not None
    # The analyzer's response contracts reject what this lenient reader accepts:
    # it would turn a misspelt value into "unknown", and no rule checks a
    # misspelt badge.
    for section, fields in (visual or {}).items():
        assert getattr(analysis, section).model_dump(include=set(fields)) == fields
    extraction = analysis.listing_extraction.model_dump(include=set(listing or {}))
    assert extraction == (listing or {})
    assert set(highlights) <= set(PropertyHighlight) and set(lowlights) <= set(PropertyLowlight)
    return analysis


def reconcile(analysis):
    """Return the checked analysis and its dropped-badge logs."""
    with structlog.testing.capture_logs() as logs:
        checked = reconcile_analysis_badges(analysis, property_id=PROPERTY_ID)
    badges = {"highlights", "lowlights"}
    # Evidence, prose and ratings are never edited, only the badge lists.
    assert checked.model_dump(exclude=badges) == analysis.model_dump(exclude=badges)
    for log in logs:
        # Correlation ID only; no listing text, URL or model output.
        assert log.keys() == {"event", "log_level", "property_id", "badge", "field", "reason"}
        assert (log["event"], log["property_id"]) == ("analysis_badge_dropped", PROPERTY_ID)
    return checked, logs


def reasons(logs):
    return {log["badge"]: log["reason"] for log in logs}


def rejected(given, kept, reason):
    return dict.fromkeys([badge for badge in given if badge not in kept], reason)


@pytest.mark.parametrize(
    ("pets_allowed", "kept", "dropped"),
    [
        pytest.param("yes", ["Pets allowed"], {}, id="allowed"),
        pytest.param("no", [], {"Pets allowed": UNSUPPORTED}, id="refused"),
        pytest.param("unknown", [], {"Pets allowed": MISSING}, id="unstated"),
    ],
)
def test_pets_badge_requires_listing_extraction_to_allow_pets(pets_allowed, kept, dropped):
    checked, logs = reconcile(
        assembled(listing={"pets_allowed": pets_allowed}, highlights=["Pets allowed"])
    )
    assert checked.highlights == kept
    assert reasons(logs) == dropped


@pytest.mark.parametrize(
    "hosting",
    [pytest.param("good", id="good-hosting"), pytest.param("excellent", id="excellent-hosting")],
)
@pytest.mark.parametrize(
    ("layout", "dropped"),
    [
        pytest.param({"kitchen_living_layout": "open_plan"}, {}, id="open-plan"),
        pytest.param(
            {"kitchen_living_layout": "separate"},
            {"Open-plan layout": UNSUPPORTED},
            id="separate",
        ),
        pytest.param(
            {"kitchen_living_layout": "unknown"}, {"Open-plan layout": MISSING}, id="unknown"
        ),
        pytest.param({}, {"Open-plan layout": MISSING}, id="layout-omitted"),
    ],
)
def test_open_plan_badge_requires_open_plan_layout_whatever_the_hosting_layout(
    layout, dropped, hosting
):
    checked, logs = reconcile(
        assembled(
            visual={"space": {"hosting_layout": hosting, **layout}},
            highlights=["Open-plan layout", "Great hosting layout"],
        )
    )
    assert checked.highlights == [
        *([] if dropped else ["Open-plan layout"]),
        "Great hosting layout",
    ]
    assert reasons(logs) == dropped


@pytest.mark.parametrize(
    ("notes", "kitchen_quality", "highlights", "kept", "dropped"),
    [
        pytest.param(
            "No floorplan or dimensions supplied; area not estimated. "
            "Living and dining share one room.",
            "decent",
            ["Open-plan layout", "Great hosting layout"],
            ["Great hosting layout"],
            {"Open-plan layout": MISSING},
            id="living-and-dining-share-a-room-c66220bd66a",
        ),
        pytest.param(
            "No floorplan or dimensions supplied; area not estimated. "
            "Living room appears reasonably sized, with a staircase within it.",
            "decent",
            ["Modern kitchen", "Open-plan layout"],
            [],
            {"Modern kitchen": UNSUPPORTED, "Open-plan layout": MISSING},
            id="staircase-in-the-living-room-c3416f2470a",
        ),
        pytest.param(
            "Reception room is 4.85m x 3.24m. Kitchen is separate.",
            "unknown",
            ["Private terrace", "Open-plan layout", "Great hosting layout"],
            ["Private terrace", "Great hosting layout"],
            {"Open-plan layout": MISSING},
            id="kitchen-described-as-separate-c7815468aad",
        ),
    ],
)
def test_saved_separate_kitchen_receipts_do_not_gain_open_plan_badges(
    notes, kitchen_quality, highlights, kept, dropped
):
    # Replayed observations predate the explicit layout field. Neither prose nor
    # a good hosting assessment is silently promoted into open-plan evidence.
    checked, logs = reconcile(
        assembled(
            visual={
                "space": {"area_notes": notes, "hosting_layout": "good"},
                "kitchen": {"overall_quality": kitchen_quality},
            },
            highlights=highlights,
        )
    )
    assert checked.highlights == kept
    assert reasons(logs) == dropped


FINISH_HIGHLIGHTS = ["Modern kitchen", "Modern bathroom"]
FINISH_LOWLIGHTS = ["Dated kitchen", "Dated bathroom"]


@pytest.mark.parametrize(
    ("finish", "kept_highlights", "kept_lowlights", "reason"),
    [
        pytest.param("modern", FINISH_HIGHLIGHTS, [], UNSUPPORTED, id="modern"),
        pytest.param("decent", [], [], UNSUPPORTED, id="decent"),
        pytest.param("dated", [], FINISH_LOWLIGHTS, UNSUPPORTED, id="dated"),
        pytest.param("unknown", [], [], MISSING, id="unknown"),
    ],
)
def test_finish_badges_require_the_matching_kitchen_and_bathroom_finish(
    finish, kept_highlights, kept_lowlights, reason
):
    checked, logs = reconcile(
        assembled(
            visual={
                "kitchen": {"overall_quality": finish},
                "bathroom": {"overall_condition": finish},
            },
            highlights=FINISH_HIGHLIGHTS,
            lowlights=FINISH_LOWLIGHTS,
        )
    )
    assert checked.highlights == kept_highlights
    assert checked.lowlights == kept_lowlights
    assert reasons(logs) == rejected(
        FINISH_HIGHLIGHTS + FINISH_LOWLIGHTS, kept_highlights + kept_lowlights, reason
    )


@pytest.mark.parametrize(
    ("condition", "dropped"),
    [
        pytest.param("excellent", {}, id="excellent"),
        pytest.param("good", {"Excellent condition": UNSUPPORTED}, id="good"),
        pytest.param("fair", {"Excellent condition": UNSUPPORTED}, id="fair"),
        pytest.param("poor", {"Excellent condition": UNSUPPORTED}, id="poor"),
        pytest.param("unknown", {"Excellent condition": MISSING}, id="unknown"),
    ],
)
def test_excellent_condition_badge_requires_an_excellent_observation(condition, dropped):
    checked, logs = reconcile(
        assembled(
            visual={"condition": {"overall_condition": condition}},
            highlights=["Excellent condition"],
        )
    )
    assert checked.highlights == ([] if dropped else ["Excellent condition"])
    assert reasons(logs) == dropped


@pytest.mark.parametrize(
    ("hosting", "great", "poor", "reason"),
    [
        pytest.param("excellent", True, False, UNSUPPORTED, id="excellent"),
        pytest.param("good", True, False, UNSUPPORTED, id="good"),
        pytest.param("poor", False, True, UNSUPPORTED, id="poor"),
        pytest.param("awkward", False, False, UNSUPPORTED, id="awkward"),
        pytest.param("unknown", False, False, MISSING, id="unknown"),
    ],
)
def test_hosting_badges_follow_the_hosting_layout(hosting, great, poor, reason):
    checked, logs = reconcile(
        assembled(
            visual={"space": {"hosting_layout": hosting, "living_room_sqm": None}},
            highlights=["Modern kitchen", "Great hosting layout", "Spacious living room"],
            lowlights=["Potential traffic noise", "Poor hosting layout"],
        )
    )
    # Spacious badges have no measured-area requirement.
    assert checked.highlights == [
        "Modern kitchen",
        *(["Great hosting layout"] if great else []),
        "Spacious living room",
    ]
    assert checked.lowlights == [
        "Potential traffic noise",
        *(["Poor hosting layout"] if poor else []),
    ]
    assert reasons(logs) == {
        **({} if great else {"Great hosting layout": reason}),
        **({} if poor else {"Poor hosting layout": reason}),
    }


@pytest.mark.parametrize(
    ("bills_included", "kept", "reason"),
    [
        pytest.param("yes", True, None, id="included"),
        pytest.param("no", False, UNSUPPORTED, id="excluded"),
        pytest.param("unknown", False, MISSING, id="unstated"),
    ],
)
def test_bills_badge_requires_the_extraction_to_include_bills(bills_included, kept, reason):
    # The analyzer passes the listing-text correction here, never the raw
    # evaluation; test_badge_assembly.py covers that ordering.
    checked, logs = reconcile(
        assembled(
            listing={"bills_included": bills_included},
            highlights=["Modern kitchen", "Bills included", "Good natural light"],
        )
    )
    assert checked.highlights == [
        "Modern kitchen",
        *(["Bills included"] if kept else []),
        "Good natural light",
    ]
    assert logs == (
        []
        if kept
        else [
            {
                "event": "analysis_badge_dropped",
                "log_level": "info",
                "property_id": PROPERTY_ID,
                "badge": "Bills included",
                "field": "listing_extraction.bills_included",
                "reason": reason,
            }
        ]
    )


APPLIANCE_HIGHLIGHTS = ["Dishwasher included", "Washing machine", "Double glazing"]
APPLIANCE_LOWLIGHTS = ["No dishwasher", "No washing machine"]


@pytest.mark.parametrize(
    ("state", "kept_highlights", "kept_lowlights", "reason"),
    [
        pytest.param("yes", APPLIANCE_HIGHLIGHTS, [], UNSUPPORTED, id="present"),
        pytest.param("no", [], APPLIANCE_LOWLIGHTS, UNSUPPORTED, id="absent"),
        pytest.param("unknown", [], [], MISSING, id="unknown"),
    ],
)
def test_appliance_and_glazing_badges_require_matching_tristate_evidence(
    state, kept_highlights, kept_lowlights, reason
):
    checked, logs = reconcile(
        assembled(
            visual={
                "kitchen": {"has_dishwasher": state, "has_washing_machine": state},
                "flooring_noise": {"has_double_glazing": state},
            },
            highlights=APPLIANCE_HIGHLIGHTS,
            lowlights=APPLIANCE_LOWLIGHTS,
        )
    )
    assert checked.highlights == kept_highlights
    assert checked.lowlights == kept_lowlights
    assert reasons(logs) == rejected(
        APPLIANCE_HIGHLIGHTS + APPLIANCE_LOWLIGHTS, kept_highlights + kept_lowlights, reason
    )


@pytest.mark.parametrize(
    ("state", "dropped"),
    [
        pytest.param("yes", {"No built-in wardrobes": UNSUPPORTED}, id="present"),
        pytest.param("no", {"Built-in wardrobes": UNSUPPORTED}, id="absent"),
        pytest.param(
            "unknown",
            {"Built-in wardrobes": MISSING, "No built-in wardrobes": MISSING},
            id="unknown",
        ),
    ],
)
def test_wardrobe_badges_use_storage_evidence_not_the_bedroom_observation(state, dropped):
    # The bedroom's wardrobe observation always differs from the storage one.
    checked, logs = reconcile(
        assembled(
            visual={
                "storage": {"has_built_in_wardrobes": state},
                "bedroom": {"has_built_in_wardrobe": "no" if state == "yes" else "yes"},
            },
            highlights=["Built-in wardrobes", "Modern kitchen"],
            lowlights=["No built-in wardrobes", "Potential traffic noise"],
        )
    )
    assert checked.highlights == [
        *(["Built-in wardrobes"] if state == "yes" else []),
        "Modern kitchen",
    ]
    assert checked.lowlights == [
        *(["No built-in wardrobes"] if state == "no" else []),
        "Potential traffic noise",
    ]
    assert reasons(logs) == dropped
    assert {log["field"] for log in logs} == {"storage.has_built_in_wardrobes"}


@pytest.mark.parametrize(
    ("availability", "highlights", "dropped"),
    [
        pytest.param("no", ["Modern kitchen"], {}, id="stated-absent"),
        pytest.param(
            "yes", ["Private balcony"], {"No outdoor space": UNSUPPORTED}, id="stated-present"
        ),
        pytest.param("unknown", ["Private balcony"], {"No outdoor space": MISSING}, id="unstated"),
    ],
)
def test_no_outdoor_space_badge_requires_stated_absence(availability, highlights, dropped):
    checked, logs = reconcile(
        assembled(
            visual={"outdoor_space": {**NO_TYPES, "availability": availability}},
            highlights=highlights,
            lowlights=["No outdoor space", "Potential traffic noise"],
        )
    )
    # False type flags mean unobserved, so they do not disprove a positive badge.
    assert checked.highlights == highlights
    assert checked.lowlights == [
        *([] if dropped else ["No outdoor space"]),
        "Potential traffic noise",
    ]
    assert reasons(logs) == dropped
    assert {log["field"] for log in logs} <= {"outdoor_space.availability"}


@pytest.mark.parametrize("flag", OUTDOOR_SPACE_FLAGS)
@pytest.mark.parametrize("availability", ["yes", "no", "unknown"])
def test_any_observed_outdoor_type_rules_out_no_outdoor_space(availability, flag):
    checked, logs = reconcile(
        assembled(
            visual={"outdoor_space": {**NO_TYPES, "availability": availability, flag: True}},
            highlights=["Private balcony"],
            lowlights=["No outdoor space", "Potential traffic noise"],
        )
    )
    assert checked.highlights == ["Private balcony"]
    assert checked.lowlights == ["Potential traffic noise"]
    assert reasons(logs) == {"No outdoor space": UNSUPPORTED}
    assert logs[0]["field"] == "outdoor_space.availability"


@pytest.mark.parametrize(
    ("outdoor", "dropped"),
    [
        pytest.param(
            {"availability": "no"}, dict.fromkeys(ACCESS_BADGES, UNSUPPORTED), id="stated-absent"
        ),
        pytest.param(
            {"availability": "yes"}, {"No outdoor space": UNSUPPORTED}, id="stated-present"
        ),
        pytest.param({"availability": "unknown"}, {"No outdoor space": MISSING}, id="unstated"),
        pytest.param(
            {"availability": "no", "has_garden": True},
            {"No outdoor space": UNSUPPORTED},
            id="stated-absent-but-garden-observed",
        ),
    ],
)
def test_explicit_outdoor_absence_rejects_contradictory_access_badges(outdoor, dropped):
    checked, logs = reconcile(
        assembled(
            visual={"outdoor_space": {**NO_TYPES, **outdoor}},
            highlights=[*ACCESS_BADGES, "Modern kitchen"],
            lowlights=["No outdoor space", "Potential traffic noise"],
        )
    )
    access_kept = "No outdoor space" in dropped
    assert checked.highlights == [*(ACCESS_BADGES if access_kept else []), "Modern kitchen"]
    assert checked.lowlights == [
        *([] if access_kept else ["No outdoor space"]),
        "Potential traffic noise",
    ]
    assert reasons(logs) == dropped
    assert {log["field"] for log in logs} == {"outdoor_space.availability"}


def test_supported_evidence_does_not_add_badges():
    analysis = assembled(listing={"bills_included": "yes", "pets_allowed": "yes"})
    checked, logs = reconcile(analysis)
    assert checked is analysis
    assert checked.highlights == checked.lowlights == []
    assert logs == []


LEGACY_HIGHLIGHTS = [
    "Bills included",
    "Double glazing",
    "Built-in wardrobes",
    "Modern kitchen",
    "Open-plan layout",
    "Pets allowed",
    "Modern bathroom",
    "Excellent condition",
]
LEGACY_LOWLIGHTS = ["Poor hosting layout", "No built-in wardrobes", "No outdoor space"]


def legacy_assessment(make_quality_analysis):
    """An assessment saved before listing extraction, flooring and storage sections."""
    return make_quality_analysis(
        highlights=LEGACY_HIGHLIGHTS,
        lowlights=LEGACY_LOWLIGHTS,
        listing_extraction=None,
        flooring_noise=None,
        storage=None,
    )


def test_loading_a_saved_assessment_does_not_reconcile_its_badges(make_quality_analysis):
    saved = legacy_assessment(make_quality_analysis).model_dump_json()
    loaded = PropertyQualityAnalysis.model_validate_json(saved)
    assert loaded.highlights == LEGACY_HIGHLIGHTS
    assert loaded.lowlights == LEGACY_LOWLIGHTS


def test_reconciliation_is_idempotent_and_leaves_its_input_unchanged(make_quality_analysis):
    legacy = legacy_assessment(make_quality_analysis)
    before = legacy.model_dump_json()
    checked, logs = reconcile(legacy)
    assert checked.highlights == ["Modern kitchen"] and checked.lowlights == []
    # Absent sections and unknown fields are missing support, not contradictions.
    assert reasons(logs) == {
        **rejected(LEGACY_HIGHLIGHTS + LEGACY_LOWLIGHTS, ["Modern kitchen"], MISSING),
        "Excellent condition": UNSUPPORTED,
    }
    assert legacy.model_dump_json() == before

    repeated, repeat_logs = reconcile(checked)
    assert repeated is checked and repeat_logs == []
    no_badges = legacy.model_copy(update={"highlights": None, "lowlights": None})
    assert reconcile_analysis_badges(no_badges) is no_badges
