"""The brief check's claims and the lead line it gives a listing.

Every case runs a realistic assessment through the real fit scorer, so a claim is
tested against what the scorer would also see, not against a hand-made breakdown.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from home_finder.config import Settings
from home_finder.filters.fit_score import compute_fit_score_and_breakdown
from home_finder.web.brief import BriefCheck, brief_lead, check_brief, must_have_dimensions
from tests.support.fit_score import make_fit_analysis

LIMITS = {
    "min_bedrooms": 0,
    "max_bedrooms": 2,
    "max_price": 2500,
    "max_commute_minutes": 30,
}
ALL_DIMENSIONS = "workspace,hosting,sound,kitchen,vibe,condition"
PROP = {"bedrooms": 2, "price_pcm": 2300, "commute_minutes": 16, "postcode": "E8 3RH"}


def check(
    analysis: dict[str, Any] | None,
    *,
    prop: dict[str, Any] = PROP,
    must_haves: str = ALL_DIMENSIONS,
) -> BriefCheck:
    _, breakdown = compute_fit_score_and_breakdown(analysis, prop["bedrooms"] or 0)
    return check_brief(prop, analysis, breakdown, **LIMITS, must_haves=must_haves)


def claims(result: BriefCheck) -> dict[str, tuple[str, str]]:
    return {item["label"]: (item["claim"], item["found"]) for item in result["items"]}


def test_must_have_dimensions_keeps_weight_order() -> None:
    assert must_have_dimensions("sound, workspace") == ["workspace", "sound"]
    assert must_have_dimensions("") == []


def test_settings_reject_an_unknown_must_have_name() -> None:
    with pytest.raises(ValidationError, match="brief_must_haves has unknown dimensions") as exc:
        Settings(brief_must_haves="workspce,hosting")
    assert "workspce" not in str(exc.value)
    assert Settings(brief_must_haves=" Sound , hosting").brief_must_haves == " Sound , hosting"
    assert Settings(brief_must_haves="").brief_must_haves == ""


def test_hard_limits_rest_on_the_listing_numbers() -> None:
    prop = {"bedrooms": 2, "price_pcm": 2500, "commute_minutes": None, "postcode": ""}
    by_label = claims(check(None, prop=prop))
    assert by_label["Up to 2 bedrooms"] == ("supported", "2")
    assert by_label["Up to £2,500 pcm"] == ("supported", "£2,500, your limit")
    assert by_label["Within 30 min"] == ("needs_checking", "no postcode in the listing")

    over = claims(check(None, prop={**prop, "price_pcm": 2650, "commute_minutes": 41}))
    assert over["Up to £2,500 pcm"] == ("conflict", "£2,650 pcm, £150 over your limit")
    assert over["Within 30 min"] == ("conflict", "41 min estimated, 11 over")


def test_room_to_host_is_not_supported_by_a_big_flat_whose_living_room_was_never_measured():
    """Total area, a spacious feel, outdoor space and a good flow move the score, not the claim."""
    analysis = make_fit_analysis(
        space={
            "living_room_sqm": None,
            "total_area_sqm": 70,
            "is_spacious_enough": True,
            "hosting_layout": "excellent",
        },
        highlights=["Spacious living room"],
    )
    _, breakdown = compute_fit_score_and_breakdown(analysis, 2)
    hosting = next(dim for dim in breakdown or [] if dim["key"] == "hosting")
    assert hosting["score"] >= 80, "the scorer still rewards the space"
    assert claims(check(analysis))["Room to host"] == (
        "needs_checking",
        "living-room size not shown",
    )


@pytest.mark.parametrize(
    ("living_room_sqm", "expected"),
    [
        (24, ("supported", "Living room ~24 m²")),
        (20, ("supported", "Living room ~20 m²")),
        (15, ("needs_checking", "living room ~15 m², not clearly big enough")),
        (10, ("conflict", "Living room only ~10 m²")),
    ],
)
def test_a_measured_living_room_decides_room_to_host(living_room_sqm, expected) -> None:
    analysis = make_fit_analysis(space={"living_room_sqm": living_room_sqm})
    assert claims(check(analysis))["Room to host"] == expected


@pytest.mark.parametrize(
    ("label", "overrides", "expected"),
    [
        pytest.param(
            "Somewhere to work",
            {"bedroom": {"can_fit_desk": "unknown", "office_separation": "unknown"}},
            ("needs_checking", "work separation and desk space not shown"),
            id="a-second-bedroom-alone-is-not-an-office",
        ),
        pytest.param(
            "Somewhere to work",
            {"bedroom": {"can_fit_desk": "yes", "office_separation": "shared_space"}},
            ("supported", "Room for a desk"),
            id="shared-space-does-not-count-against-room-for-a-desk",
        ),
        pytest.param(
            "Somewhere to work",
            {"bedroom": {"can_fit_desk": "unknown", "office_separation": "shared_space"}},
            ("needs_checking", "work spot in shared living space; desk space not shown"),
            id="shared-space-alone-cannot-settle-it",
        ),
        pytest.param(
            "Room to host",
            {"space": {"living_room_sqm": 24, "hosting_layout": "poor"}},
            ("conflict", "Poor hosting layout"),
            id="a-poor-layout-outweighs-a-big-living-room",
        ),
        pytest.param(
            "Somewhere to work",
            {"bedroom": {"can_fit_desk": "no", "office_separation": "dedicated_room"}},
            ("conflict", "No room for a desk"),
            id="evidence-against-outweighs-evidence-for",
        ),
        pytest.param(
            "Quiet enough for calls",
            {
                "flooring_noise": {"building_construction": None, "has_double_glazing": "unknown"},
                "light_space": {"floor_level": "lower"},
            },
            ("needs_checking", "construction and glazing not shown"),
            id="floor-level-says-nothing-about-quiet",
        ),
        pytest.param(
            "Quiet enough for calls",
            {
                "lowlights": ["Potential traffic noise"],
                "flooring_noise": {"noise_indicators": ["Hard laminate flooring throughout"]},
            },
            ("conflict", "Potential road or rail noise"),
            id="a-noise-tag-is-named-for-itself-not-for-the-first-note",
        ),
        pytest.param(
            "Quiet enough for calls",
            {"flooring_noise": {"building_construction": "timber_frame"}},
            ("conflict", "Timber-frame construction"),
            id="timber-frame-outweighs-double-glazing",
        ),
        pytest.param(
            "Quiet enough for calls",
            {"flooring_noise": {"building_construction": "mixed", "has_double_glazing": "unknown"}},
            ("needs_checking", "mixed construction; glazing not shown"),
            id="mixed-construction-cannot-settle-it",
        ),
        pytest.param(
            "A kitchen that works",
            {"kitchen": {"overall_quality": "modern", "hob_type": "electric"}},
            ("conflict", "Electric hob"),
            id="a-modern-finish-does-not-outweigh-an-electric-hob",
        ),
        pytest.param(
            "A kitchen that works",
            {"kitchen": {"overall_quality": "modern", "hob_type": "unknown"}},
            ("needs_checking", "hob type not shown"),
            id="a-modern-kitchen-without-a-known-hob",
        ),
        pytest.param(
            "A kitchen that works",
            {"kitchen": {"hob_type": "unknown"}, "lowlights": ["Electric hob"]},
            ("conflict", "Electric hob"),
            id="an-electric-hob-tag-counts-when-the-hob-field-is-unread",
        ),
        pytest.param(
            "A kitchen that works",
            {"kitchen": {"hob_type": "gas", "has_dishwasher": "no"}},
            ("supported", "Gas hob"),
            id="no-dishwasher-is-not-a-conflict",
        ),
        pytest.param(
            "Light and feel",
            {"light_space": {"natural_light": "good"}, "space": {"hosting_layout": "awkward"}},
            ("supported", "Good natural light"),
            id="an-awkward-layout-says-nothing-about-light",
        ),
        pytest.param(
            "Light and feel",
            {"light_space": {"natural_light": "fair"}},
            ("needs_checking", "natural light only fair"),
            id="fair-light-cannot-settle-it",
        ),
        pytest.param(
            "Good condition",
            {"condition": {"overall_condition": "unknown"}, "overall_rating": 3},
            ("needs_checking", "condition not shown"),
            id="the-desirability-rating-is-not-a-condition-reading",
        ),
        pytest.param(
            "Good condition",
            {"condition": {"overall_condition": "poor"}},
            ("conflict", "Looks poor"),
            id="a-poor-condition-reading",
        ),
        pytest.param(
            "Good condition",
            {"condition": {"overall_condition": "fair"}},
            ("needs_checking", "condition only fair"),
            id="fair-condition-cannot-settle-it",
        ),
        pytest.param(
            "Good condition",
            {"condition_concerns": True, "concern_severity": "moderate"},
            ("conflict", "Moderate maintenance concerns"),
            id="rated-concerns-count-against-a-seen-condition",
        ),
        pytest.param(
            "Good condition",
            {
                "condition": {
                    "overall_condition": "good",
                    "maintenance_concerns": [
                        "Only 1 photo provided - cannot assess overall property condition"
                    ],
                },
                "condition_concerns": True,
                "concern_severity": "serious",
            },
            ("needs_checking", "looks good, but not every room was shown"),
            id="a-missing-photo-concern-is-a-gap-not-a-defect",
        ),
        pytest.param(
            "Good condition",
            {
                "condition": {
                    "maintenance_concerns": [
                        "Bathroom not photographed - condition unknown",
                        "Damp patch on the bedroom ceiling",
                    ],
                },
                "condition_concerns": True,
                "concern_severity": "moderate",
            },
            ("conflict", "Moderate maintenance concerns"),
            id="a-seen-defect-still-counts-beside-a-photo-gap",
        ),
        pytest.param(
            "Good condition",
            {
                "condition": {
                    "overall_condition": "unknown",
                    "maintenance_concerns": [
                        "No interior photos provided - condition unverifiable"
                    ],
                },
                "condition_concerns": True,
                "concern_severity": "serious",
            },
            ("needs_checking", "condition not shown"),
            id="concerns-only-about-missing-photos-are-gaps",
        ),
        pytest.param(
            "Good condition",
            {
                "condition": {
                    "overall_condition": "unknown",
                    "maintenance_concerns": [
                        "No interior photos provided - cannot assess condition",
                        "Period property: risk of deferred maintenance",
                    ],
                },
                "condition_concerns": True,
                "concern_severity": "serious",
            },
            ("needs_checking", "condition not shown"),
            id="without-a-condition-reading-concerns-are-guesses",
        ),
    ],
)
def test_each_need_rests_on_its_own_direct_evidence(label, overrides, expected) -> None:
    assert claims(check(make_fit_analysis(**overrides)))[label] == expected


def test_a_studio_has_nowhere_separate_to_work() -> None:
    """Even when the analysis reads the studio as shared space with room for a desk."""
    analysis = make_fit_analysis(
        bedroom={"office_separation": "shared_space", "can_fit_desk": "yes"}
    )
    studio = {**PROP, "bedrooms": 0}
    assert claims(check(analysis, prop=studio))["Somewhere to work"] == (
        "conflict",
        "Studio (no separation)",
    )


def test_a_tagged_lowlight_leads_the_evidence_against() -> None:
    analysis = make_fit_analysis(
        bedroom={"office_separation": "dedicated_room", "can_fit_desk": "yes"},
        lowlights=["No work-life separation"],
    )
    assert claims(check(analysis))["Somewhere to work"] == ("conflict", "No work-life separation")


def test_must_haves_come_first_and_the_counts_match_the_claims() -> None:
    analysis = make_fit_analysis(
        bedroom={"office_separation": "dedicated_room"},
        lowlights=["Potential traffic noise"],
        kitchen={"hob_type": "unknown"},
    )
    result = check(analysis, must_haves="workspace,sound,kitchen")
    musts = [item for item in result["items"] if item["must"]]
    assert [item["label"] for item in musts] == [
        "Up to 2 bedrooms",
        "Up to £2,500 pcm",
        "Within 30 min",
        "Somewhere to work",
        "Quiet enough for calls",
        "A kitchen that works",
    ]
    assert result["items"][: len(musts)] == musts
    assert [item["label"] for item in result["items"][len(musts) :]] == [
        "Room to host",
        "Light and feel",
        "Good condition",
    ]
    assert (
        result["must_total"],
        result["must_supported"],
        result["must_conflict"],
        result["must_needs_checking"],
    ) == (6, 4, 1, 1)


def test_the_templates_still_read_each_claim_under_its_earlier_name() -> None:
    analysis = make_fit_analysis(lowlights=["Potential traffic noise"], kitchen={"hob_type": None})
    result = check(analysis, must_haves="workspace,sound,kitchen")
    earlier = {"supported": "met", "conflict": "broken", "needs_checking": "unknown"}
    assert {item["claim"]: item["state"] for item in result["items"]} == earlier
    assert (result["must_met"], result["must_broken"], result["must_unknown"]) == (
        result["must_supported"],
        result["must_conflict"],
        result["must_needs_checking"],
    )


@pytest.mark.parametrize("analysis", [None, {}])
def test_without_an_accepted_assessment_each_must_have_dimension_needs_checking(analysis):
    result = check(analysis, must_haves="workspace,sound")
    dimensions = [item for item in result["items"] if item["kind"] == "dimension"]
    assert [(item["claim"], item["found"]) for item in dimensions] == [
        ("needs_checking", "not assessed yet"),
        ("needs_checking", "not assessed yet"),
    ]
    assert result["must_needs_checking"] == 2


def test_lead_order_limit_then_risk_then_needs_checking_then_clear() -> None:
    supported = make_fit_analysis(
        bedroom={"office_separation": "dedicated_room"},
        highlights=["Dishwasher included", "Gas hob"],
    )
    clear = brief_lead(check(supported, must_haves="workspace,sound"), supported)
    assert clear == {
        "kind": "clear",
        "kicker": "All 5 must-haves supported",
        "title": "Nothing breaks your brief.",
        "detail": "Best thing: dishwasher included.",
    }

    # A conflict the analysis found is a risk to check, not a broken limit.
    noisy = make_fit_analysis(lowlights=["New-build acoustics"])
    risk = brief_lead(check(noisy, must_haves="workspace,sound"), noisy)
    assert risk["kind"] == "risk"
    assert risk["kicker"] == "Puts a must-have at risk"
    assert risk["title"] == "New-build acoustics."
    assert risk["detail"].startswith("Your must-have: quiet enough for calls.")

    over = brief_lead(
        check(noisy, prop={**PROP, "price_pcm": 2600}, must_haves="workspace,sound"), noisy
    )
    assert over["kind"] == "broken" and over["title"] == "£2,600 pcm, £100 over your limit."


def test_the_lead_never_clears_a_brief_whose_must_haves_need_checking() -> None:
    """A big flat whose living room was never measured: the score is high, the lead is honest."""
    analysis = make_fit_analysis(
        space={"living_room_sqm": None, "total_area_sqm": 70, "is_spacious_enough": True}
    )
    lead = brief_lead(check(analysis, must_haves="workspace,hosting"), analysis)
    assert lead == {
        "kind": "unknown",
        "kicker": "A must-have needs checking",
        "title": "Room to host: living-room size not shown.",
        "detail": "The other 4 must-haves are supported.",
    }

    unrouted = {**PROP, "commute_minutes": None}
    lead = brief_lead(check(analysis, prop=unrouted, must_haves="workspace,hosting"), analysis)
    assert lead["kicker"] == "2 must-haves need checking"
    assert lead["title"] == "Within 30 min: not worked out yet."
    assert lead["detail"] == "3 of 5 must-haves supported; room to host also needs checking."


def test_a_clear_lead_still_reports_weak_nice_to_haves() -> None:
    analysis = make_fit_analysis(
        kitchen={"hob_type": "electric"},
        light_space={"natural_light": "unknown"},
        highlights=[],
    )
    lead = brief_lead(check(analysis, must_haves="workspace,sound"), analysis)
    assert lead["kind"] == "clear"
    assert lead["detail"] == "Nice-to-haves: 1 in conflict, 1 to check."
