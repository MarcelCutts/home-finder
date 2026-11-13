"""What the tool says in short (web/verdict.py): the listing page's fits / watch out /
find out lists, and the card icons and highlight tags held to the brief."""

import pytest

from home_finder.filters.fit_score import (
    FitDimension,
    compute_fit_score_and_breakdown,
    compute_lifestyle_icons,
)
from home_finder.models import (
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    ListingExtraction,
    ListingRedFlags,
    PropertyQualityAnalysis,
    SpaceAnalysis,
)
from home_finder.web.brief import BriefCheck, check_brief
from home_finder.web.verdict import build_verdict, highlights_within_brief, icons_from_brief


def brief_for(
    analysis: PropertyQualityAnalysis,
    bedrooms: int = 1,
    *,
    unassessed: frozenset[str] = frozenset(),
) -> BriefCheck:
    """The brief check for a one-bed inside every hard limit, with the default must-haves.

    ``unassessed`` names fit dimensions to leave out of the breakdown, as when the
    scorer has no reading for them.
    """
    _, breakdown = compute_fit_score_and_breakdown(analysis, bedrooms)
    breakdown = [dim for dim in breakdown or [] if dim["key"] not in unassessed]
    listing = {"bedrooms": bedrooms, "price_pcm": 1900, "commute_minutes": 20, "postcode": "E8 3RH"}
    return check_brief(
        listing,
        analysis,
        breakdown,
        min_bedrooms=0,
        max_bedrooms=2,
        max_price=2500,
        max_commute_minutes=30,
        must_haves="workspace,hosting",
    )


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


@pytest.mark.parametrize(
    ("living_room_sqm", "expected"),
    [
        (None, ["Gas hob"]),  # never measured: the brief says room to host needs checking
        (22.0, ["Spacious living room", "Gas hob"]),  # measured and roomy: supported
    ],
)
def test_spacious_living_room_fits_only_where_the_brief_supports_room_to_host(
    make_quality_analysis, living_room_sqm, expected
):
    analysis = make_quality_analysis(
        highlights=["Spacious living room", "Gas hob"],
        space=SpaceAnalysis(living_room_sqm=living_room_sqm, is_spacious_enough=True),
    )
    check = brief_for(analysis)
    assert build_verdict(analysis, None, check)["fits"] == expected
    assert highlights_within_brief(analysis.highlights, check) == expected
    # Without a brief there is nothing to hold the highlight to.
    assert build_verdict(analysis, None)["fits"] == ["Spacious living room", "Gas hob"]


def test_condition_highlights_wait_for_every_room_to_be_shown(make_quality_analysis):
    """A good-looking home with a room left out of the photos needs checking, not praise."""
    highlights = ["Excellent condition", "Recently refurbished", "Period features"]
    shown = make_quality_analysis(highlights=highlights)
    gaps = make_quality_analysis(
        highlights=highlights, listing_red_flags=ListingRedFlags(missing_room_photos=["Bathroom"])
    )
    assert highlights_within_brief(highlights, brief_for(shown)) == highlights
    assert highlights_within_brief(highlights, brief_for(gaps)) == ["Period features"]


def test_condition_highlight_goes_when_the_brief_has_no_condition_line(make_quality_analysis):
    """A nice-to-have the scorer didn't assess has no brief line; no line is no support."""
    highlights = ["Excellent condition", "Period features"]
    analysis = make_quality_analysis(highlights=highlights)
    check = brief_for(analysis, unassessed=frozenset({"condition"}))
    assert "Good condition" not in [item["label"] for item in check["items"]]
    assert highlights_within_brief(highlights, check) == ["Period features"]


@pytest.mark.parametrize(
    ("space", "lowlights", "expected"),
    [
        (
            SpaceAnalysis(is_spacious_enough=True),
            [],
            {"state": "neutral", "tooltip": "Room to host: living-room size not shown"},
        ),
        (
            SpaceAnalysis(living_room_sqm=22.0, is_spacious_enough=True),
            [],
            {"state": "good", "tooltip": "Room to host: Living room ~22 m²"},
        ),
        (
            SpaceAnalysis(living_room_sqm=22.0, is_spacious_enough=True),
            ["Compact living room"],
            {"state": "concern", "tooltip": "Room to host: Compact living room"},
        ),
    ],
    ids=["needs checking", "supported", "conflict"],
)
def test_card_icon_shows_the_briefs_claim_in_the_briefs_words(
    make_quality_analysis, space, lowlights, expected
):
    """The scorer calls all three of these homes good for hosting: spacious, double glazed."""
    analysis = make_quality_analysis(
        space=space,
        lowlights=lowlights,
        flooring_noise=FlooringNoiseAnalysis(has_double_glazing="yes"),
    )
    scored = compute_lifestyle_icons(analysis, 1)
    assert scored["hosting"]["state"] == "good"

    held = icons_from_brief(scored, brief_for(analysis))
    assert held["hosting"] == expected
    # Overall space has no line in the brief, so its icon stays the scorer's.
    assert held["space"] == scored["space"]
    assert icons_from_brief(None, brief_for(analysis)) is None
