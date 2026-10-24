"""The brief check and the lead line it gives a listing."""

import pytest

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


def test_must_have_dimensions_keeps_weight_order_and_drops_unknown_names() -> None:
    assert must_have_dimensions("sound, workspace,garage") == ["workspace", "sound"]
    assert must_have_dimensions("") == []


def test_hard_limits_are_met_broken_or_unknown_with_evidence() -> None:
    prop = {"bedrooms": 2, "price_pcm": 2500, "commute_minutes": None, "postcode": ""}
    check = check_brief(prop, None, None, **LIMITS)
    by_label = {item["label"]: item for item in check["items"]}
    assert by_label["Up to 2 bedrooms"]["state"] == "met"
    assert by_label["Up to £2,500 pcm"] == {
        "label": "Up to £2,500 pcm",
        "state": "met",
        "found": "£2,500, your limit",
        "must": True,
    }
    assert by_label["Within 30 min"]["state"] == "unknown"
    assert by_label["Within 30 min"]["found"] == "no postcode in the listing"

    over = check_brief({**prop, "price_pcm": 2650, "commute_minutes": 41}, None, None, **LIMITS)
    found = {item["label"]: item["found"] for item in over["items"]}
    assert found["Up to £2,500 pcm"] == "£2,650 pcm, £150 over your limit"
    assert found["Within 30 min"] == "41 min, 11 over"


def test_dimensions_split_into_must_and_nice_and_a_breaker_lowlight_breaks_one() -> None:
    analysis = {
        "lowlights": ["Potential traffic noise"],
        "flooring_noise": {"noise_indicators": ["Railway line directly beside the building."]},
    }
    breakdown = [
        dimension("workspace", 100, 1.0, ("Dedicated office", "earned")),
        dimension("hosting", 95),
        dimension("sound", 88, 1.0, ("Double glazing", "earned")),
        dimension("kitchen", 30, 1.0, ("Dishwasher", "missed")),
        dimension("vibe", 68, 0.0),
        dimension("condition", 75),
    ]
    prop = {"bedrooms": 2, "price_pcm": 2300, "commute_minutes": 16, "postcode": "E3 4AB"}
    check = check_brief(prop, analysis, breakdown, **LIMITS)

    musts = [item for item in check["items"] if item["must"]]
    nices = [item for item in check["items"] if not item["must"]]
    assert [item["label"] for item in musts] == [
        "Up to 2 bedrooms",
        "Up to £2,500 pcm",
        "Within 30 min",
        "A room to work in",
        "Quiet enough for calls",
    ]
    assert check["items"][: len(musts)] == musts, "must-haves come first"
    assert musts[3]["state"] == "met" and musts[3]["found"] == "Dedicated office"
    # The noise lowlight breaks "quiet" even though the sound score is 88, and the
    # photos' own evidence is used as the reason.
    assert musts[4] == {
        "label": "Quiet enough for calls",
        "state": "broken",
        "found": "Railway line directly beside the building",
        "must": True,
    }
    by_label = {item["label"]: item for item in nices}
    assert by_label["A kitchen that works"]["state"] == "broken"
    assert by_label["A kitchen that works"]["found"] == "Dishwasher"
    assert by_label["Light and feel"]["state"] == "unknown"
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


def test_lead_is_the_first_broken_must_have_then_the_first_gap_then_all_clear() -> None:
    prop = {"bedrooms": 2, "price_pcm": 2200, "commute_minutes": 28, "postcode": "E17 1AA"}
    quiet = [dimension("workspace", 90, 1.0, ("Desk space", "earned")), dimension("sound", 80)]
    analysis = {"highlights": ["Dishwasher", "Gas hob"]}

    clear = brief_lead(check_brief(prop, analysis, quiet, **LIMITS), analysis)
    assert clear == {
        "kind": "clear",
        "kicker": "All 5 must-haves met",
        "title": "Nothing breaks your brief.",
        "detail": "Best thing: dishwasher.",
    }

    gap = brief_lead(
        check_brief({**prop, "commute_minutes": None}, analysis, quiet, **LIMITS), analysis
    )
    assert gap["kind"] == "unknown"
    assert gap["title"] == "Can't check 'within 30 min': not worked out yet."

    noisy = {"lowlights": ["New-build acoustics"]}
    broken = brief_lead(check_brief(prop, noisy, quiet, **LIMITS), noisy)
    assert broken == {
        "kind": "broken",
        "kicker": "Breaks a must-have",
        "title": "New-build acoustics.",
        "detail": "Your must-have: quiet enough for calls.",
    }


@pytest.mark.parametrize("analysis", [None, {}])
def test_missing_analysis_leaves_must_have_dimensions_unknown(analysis) -> None:
    prop = {"bedrooms": 1, "price_pcm": 1900, "commute_minutes": 22, "postcode": "E8 3RH"}
    check = check_brief(prop, analysis, None, **LIMITS)
    unknown = [item for item in check["items"] if item["state"] == "unknown"]
    assert [item["found"] for item in unknown] == ["not assessed yet", "not assessed yet"]
    assert check["must_unknown"] == 2
