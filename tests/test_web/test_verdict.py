"""Tests for the fits / watch out / find out lists that lead the listing page."""

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
    assert build_verdict(analysis, None)["watch"] == expected


def test_epc_highlight_is_left_to_the_costs_when_the_listing_states_a_band(make_quality_analysis):
    highlights = ["EPC B or above", "Gas hob"]
    stated = make_quality_analysis(
        highlights=highlights, listing_extraction=ListingExtraction(epc_rating="B")
    )
    unstated = make_quality_analysis(highlights=highlights)
    assert build_verdict(stated, None)["fits"] == ["Gas hob"]
    assert build_verdict(unstated, None)["fits"] == highlights


def test_find_out_collects_unknowns_once_and_service_charge_leaves_watch_out(
    make_quality_analysis,
):
    analysis = make_quality_analysis(lowlights=["Service charge unstated", "No dishwasher"])
    breakdown = [
        dimension(("Dishwasher", "unknown"), ("Gas/induction hob", "earned")),
        dimension(("Broadband", "unknown"), ("Dishwasher", "unknown")),
    ]
    verdict = build_verdict(analysis, breakdown)
    assert verdict["watch"] == ["No dishwasher"]
    assert verdict["find_out"] == ["Service charge", "Dishwasher", "Broadband"]
