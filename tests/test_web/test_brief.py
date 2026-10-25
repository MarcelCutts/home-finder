"""The brief check and the lead line it gives a listing."""

import pytest
from pydantic import ValidationError

from home_finder.config import Settings
from home_finder.filters.fit_score import compute_fit_score_and_breakdown
from home_finder.web.brief import brief_lead, check_brief, must_have_dimensions

LIMITS = {
    "min_bedrooms": 0,
    "max_bedrooms": 2,
    "max_price": 2500,
    "max_commute_minutes": 30,
    "must_haves": "workspace,sound",
}


def dimension(key: str, score: int, confidence: float = 1.0, *factors: tuple[str, str]) -> dict:
    return {
        "key": key,
        "label": key.title(),
        "score": score,
        "weight": 20,
        "confidence": confidence,
        "factors": [{"label": label, "state": state} for label, state in factors],
    }


def states(check) -> dict[str, tuple[str, str]]:
    return {item["label"]: (item["state"], item["found"]) for item in check["items"]}


def test_must_have_dimensions_keeps_weight_order() -> None:
    assert must_have_dimensions("sound, workspace") == ["workspace", "sound"]
    assert must_have_dimensions("") == []


def test_settings_reject_an_unknown_must_have_name() -> None:
    with pytest.raises(ValidationError, match="workspce"):
        Settings(brief_must_haves="workspce,hosting")
    assert Settings(brief_must_haves=" Sound , hosting").brief_must_haves == " Sound , hosting"
    assert Settings(brief_must_haves="").brief_must_haves == ""


def test_hard_limits_are_met_broken_or_unknown_with_evidence() -> None:
    prop = {"bedrooms": 2, "price_pcm": 2500, "commute_minutes": None, "postcode": ""}
    by_label = states(check_brief(prop, None, None, **LIMITS))
    assert by_label["Up to 2 bedrooms"] == ("met", "2")
    assert by_label["Up to £2,500 pcm"] == ("met", "£2,500, your limit")
    assert by_label["Within 30 min"] == ("unknown", "no postcode in the listing")

    over = states(
        check_brief({**prop, "price_pcm": 2650, "commute_minutes": 41}, None, None, **LIMITS)
    )
    assert over["Up to £2,500 pcm"] == ("broken", "£2,650 pcm, £150 over your limit")
    assert over["Within 30 min"] == ("broken", "41 min estimated, 11 over")


def test_a_low_score_with_thin_evidence_is_unknown_not_broken(make_quality_analysis) -> None:
    """A real scorer result: two bedrooms earned, desk and broadband unknown, workspace 35."""
    analysis = make_quality_analysis()
    _, breakdown = compute_fit_score_and_breakdown(analysis, 2, postcode="E8 3RH")
    workspace = next(dim for dim in breakdown if dim["key"] == "workspace")
    assert workspace["score"] < 40 and workspace["confidence"] <= 0.5
    prop = {"bedrooms": 2, "price_pcm": 2300, "commute_minutes": 16, "postcode": "E8 3RH"}
    by_label = states(check_brief(prop, analysis, breakdown, **LIMITS))
    assert by_label["Somewhere to work"] == (
        "unknown",
        "not seen in the photos: desk space, broadband",
    )


def test_a_missed_factor_with_a_low_score_breaks_a_dimension() -> None:
    breakdown = [dimension("workspace", 0, 0.5, ("Studio (no separation)", "missed"))]
    prop = {"bedrooms": 0, "price_pcm": 1800, "commute_minutes": 10, "postcode": "N1 1AA"}
    by_label = states(check_brief(prop, {}, breakdown, **LIMITS))
    assert by_label["Somewhere to work"] == ("broken", "Studio (no separation)")


def test_dimensions_split_into_must_and_nice_and_a_breaker_lowlight_breaks_one() -> None:
    analysis = {
        "lowlights": ["Potential traffic noise"],
        "flooring_noise": {"noise_indicators": ["Railway line directly beside the building."]},
    }
    breakdown = [
        dimension("workspace", 100, 1.0, ("Dedicated office", "earned")),
        dimension("hosting", 95, 1.0, ("Spacious", "earned")),
        dimension("sound", 88, 1.0, ("Double glazing", "earned")),
        dimension("kitchen", 30, 1.0, ("Dishwasher", "missed")),
        dimension("vibe", 68, 0.0),
        dimension("condition", 75, 1.0, ("Good overall", "earned")),
    ]
    prop = {"bedrooms": 2, "price_pcm": 2300, "commute_minutes": 16, "postcode": "E3 4AB"}
    check = check_brief(prop, analysis, breakdown, **LIMITS)

    musts = [item for item in check["items"] if item["must"]]
    assert [item["label"] for item in musts] == [
        "Up to 2 bedrooms",
        "Up to £2,500 pcm",
        "Within 30 min",
        "Somewhere to work",
        "Quiet enough for calls",
    ]
    assert check["items"][: len(musts)] == musts, "must-haves come first"
    by_label = states(check)
    assert by_label["Somewhere to work"] == ("met", "Dedicated office")
    # The typed noise lowlight breaks "quiet" whatever the sound score, with the
    # photos' own evidence as the reason.
    assert by_label["Quiet enough for calls"] == (
        "broken",
        "Railway line directly beside the building",
    )
    assert by_label["A kitchen that works"] == ("broken", "Dishwasher")
    assert by_label["Light and feel"] == ("unknown", "not seen in the photos")
    assert (
        check["must_total"],
        check["must_met"],
        check["must_broken"],
        check["must_unknown"],
    ) == (
        5,
        4,
        1,
        0,
    )


def test_lead_order_limit_then_risk_then_gap_then_clear() -> None:
    prop = {"bedrooms": 2, "price_pcm": 2200, "commute_minutes": 28, "postcode": "E17 1AA"}
    quiet = [
        dimension("workspace", 90, 1.0, ("Desk space", "earned")),
        dimension("sound", 80, 1.0, ("Double glazing", "earned")),
    ]
    analysis = {"highlights": ["Dishwasher", "Gas hob"]}

    clear = brief_lead(check_brief(prop, analysis, quiet, **LIMITS), analysis)
    assert clear == {
        "kind": "clear",
        "kicker": "All 5 must-haves met",
        "title": "Nothing breaks your brief.",
        "detail": "Best thing: dishwasher.",
    }

    # A photo-based breaker is a risk to check, not a broken limit.
    noisy = {"lowlights": ["New-build acoustics"]}
    risk = brief_lead(check_brief(prop, noisy, quiet, **LIMITS), noisy)
    assert risk["kind"] == "risk"
    assert risk["kicker"] == "Puts a must-have at risk"
    assert risk["title"] == "New-build acoustics."
    assert risk["detail"].startswith("Your must-have: quiet enough for calls.")

    # A broken limit outranks a risk.
    over = brief_lead(check_brief({**prop, "price_pcm": 2600}, noisy, quiet, **LIMITS), noisy)
    assert over["kind"] == "broken" and over["title"] == "£2,600 pcm, £100 over your limit."


def test_an_unknown_lead_counts_what_is_met_and_names_the_other_gaps() -> None:
    prop = {"bedrooms": 2, "price_pcm": 2300, "commute_minutes": None, "postcode": "E3 1AA"}
    one_gap = [
        dimension("workspace", 90, 1.0, ("Desk space", "earned")),
        dimension("sound", 80, 1.0, ("Double glazing", "earned")),
    ]
    lead = brief_lead(check_brief(prop, {}, one_gap, **LIMITS), {})
    assert lead["title"] == "Can't check 'within 30 min': not worked out yet."
    assert lead["detail"] == "The other 4 must-haves are met."

    two_gaps = [
        dimension("workspace", 90, 1.0, ("Desk space", "earned")),
        dimension("sound", 0, 0.0),
    ]
    lead = brief_lead(check_brief(prop, {}, two_gaps, **LIMITS), {})
    assert lead["detail"] == "3 of 5 must-haves met; quiet enough for calls also still unknown."


def test_a_clear_lead_still_reports_weak_nice_to_haves() -> None:
    prop = {"bedrooms": 2, "price_pcm": 2200, "commute_minutes": 20, "postcode": "E17 1AA"}
    breakdown = [
        dimension("workspace", 90, 1.0, ("Desk space", "earned")),
        dimension("sound", 80, 1.0, ("Double glazing", "earned")),
        dimension("kitchen", 20, 1.0, ("Dishwasher", "missed")),
        dimension("vibe", 50, 0.0),
    ]
    lead = brief_lead(check_brief(prop, {}, breakdown, **LIMITS), {})
    assert lead["kind"] == "clear"
    assert lead["detail"] == "Nice-to-haves: 1 not met, 1 unknown."


@pytest.mark.parametrize("analysis", [None, {}])
def test_missing_analysis_leaves_must_have_dimensions_unknown(analysis) -> None:
    prop = {"bedrooms": 1, "price_pcm": 1900, "commute_minutes": 22, "postcode": "E8 3RH"}
    check = check_brief(prop, analysis, None, **LIMITS)
    unknown = [item for item in check["items"] if item["state"] == "unknown"]
    assert [item["found"] for item in unknown] == ["not assessed yet", "not assessed yet"]
    assert check["must_unknown"] == 2
