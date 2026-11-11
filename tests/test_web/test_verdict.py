"""The fits / watch out / find out lists that lead the detail page (build_verdict)."""

import pytest

from home_finder.filters.fit_score import FitDimension
from home_finder.models import ConditionAnalysis, ListingExtraction, ListingRedFlags
from home_finder.web.verdict import build_verdict


def dimension(*factors: tuple[str, str]) -> FitDimension:
    return FitDimension(
        key="kitchen",
        label="Kitchen",
        score=50,
        weight=15,
        confidence=1.0,
        factors=[{"label": label, "state": state} for label, state in factors],
    )


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("serious", ["Damp patch on ceiling", "No dishwasher"]),
        ("minor", ["No dishwasher", "Damp patch on ceiling"]),
    ],
)
def test_serious_condition_concerns_lead_watch_out(make_quality_analysis, severity, expected):
    analysis = make_quality_analysis(
        lowlights=["No dishwasher"],
        condition=ConditionAnalysis(
            overall_condition="fair", maintenance_concerns=["Damp patch on ceiling"]
        ),
        condition_concerns=True,
        concern_severity=severity,
    )
    assert build_verdict(analysis, None)["watch"] == expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (None, ["No bathroom photos"]),
        (
            ListingRedFlags(missing_room_photos=["Bathroom"]),
            ["Not seen in supplied photos: Bathroom"],
        ),
    ],
)
def test_photo_tag_gives_way_to_a_red_flag_naming_the_room(make_quality_analysis, flags, expected):
    analysis = make_quality_analysis(lowlights=["No bathroom photos"], listing_red_flags=flags)
    verdict = build_verdict(analysis, None)
    assert verdict["find_out"] == expected
    assert verdict["watch"] == []


def test_a_concern_written_as_a_missing_photo_is_unknown_not_a_fault(make_quality_analysis):
    """The analysis sometimes writes a gap as a concern; it belongs with the unknowns."""
    analysis = make_quality_analysis(
        condition=ConditionAnalysis(
            overall_condition="good",
            maintenance_concerns=[
                "Bathroom not photographed - condition unknown",
                "Damp patch on ceiling",
            ],
        ),
        condition_concerns=True,
        concern_severity="moderate",
        listing_red_flags=ListingRedFlags(
            description_concerns=["Only 3 photos for entire property", "Price above average"]
        ),
    )
    verdict = build_verdict(analysis, None)
    assert verdict["watch"] == ["Damp patch on ceiling", "Price above average"]
    assert verdict["find_out"] == [
        "Bathroom not photographed - condition unknown",
        "Only 3 photos for entire property",
    ]


def test_what_the_photos_leave_out_is_unknown_not_a_fault(make_quality_analysis):
    """Photo gaps are something to find out; the concerns the listing text raised stay faults."""
    flags = ListingRedFlags(
        missing_room_photos=["Bathroom", "Second bedroom"],
        too_few_photos=True,
        selective_angles=True,
        description_concerns=["Railway beside the building"],
    )
    analysis = make_quality_analysis(
        lowlights=["Missing key photos", "No dishwasher"], listing_red_flags=flags
    )
    verdict = build_verdict(analysis, [dimension(("Dishwasher", "unknown"))])
    assert verdict["watch"] == ["No dishwasher", "Railway beside the building"]
    assert verdict["find_out"] == [
        "Few supplied photos",
        "Limited views in supplied photos",
        "Not seen in supplied photos: Bathroom, Second bedroom",
        "Dishwasher",
    ]


def test_epc_highlight_is_left_to_the_costs_when_the_listing_states_a_band(make_quality_analysis):
    highlights = ["EPC B or above", "Gas hob"]
    stated = make_quality_analysis(
        highlights=highlights, listing_extraction=ListingExtraction(epc_rating="B")
    )
    unstated = make_quality_analysis(highlights=highlights)
    assert build_verdict(stated, None)["fits"] == ["Gas hob"]
    assert build_verdict(unstated, None)["fits"] == highlights


def test_find_out_collects_unknowns_once_and_service_charge_is_dropped(
    make_quality_analysis,
):
    # A service charge is the landlord's cost, so an unstated one belongs in no list.
    analysis = make_quality_analysis(lowlights=["Service charge unstated", "No dishwasher"])
    breakdown = [
        dimension(("Dishwasher", "unknown"), ("Gas/induction hob", "earned")),
        dimension(("Broadband", "unknown"), ("Dishwasher", "unknown")),
    ]
    verdict = build_verdict(analysis, breakdown)
    assert verdict["watch"] == ["No dishwasher"]
    assert verdict["find_out"] == ["Dishwasher", "Broadband"]
