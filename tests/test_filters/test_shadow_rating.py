"""Keep the measured candidate reproducible without endorsing sparse evidence."""

from copy import deepcopy

import pytest

from home_finder.filters.shadow_rating import (
    SHADOW_RATING_POLICY_VERSION,
    compute_shadow_rating,
)


@pytest.mark.parametrize(
    "visual,rating,coverage,weight,observed",
    [
        ({}, 3, "none", 0, ()),
        ({"kitchen": {"overall_quality": "modern"}}, 5, "partial", 20, ("kitchen",)),
        ({"light_space": {"natural_light": "good"}}, 4, "partial", 20, ("light",)),
        (
            {
                "condition": {"overall_condition": "good"},
                "light_space": {"natural_light": "good"},
            },
            4,
            "partial",
            50,
            ("condition", "light"),
        ),
    ],
)
def test_sparse_evidence_counterexamples_remain_visible(visual, rating, coverage, weight, observed):
    # These outputs are known defects of the proposal. Recording coverage lets
    # reviewers see them; no score is declared safe for production by this test.
    result = compute_shadow_rating(visual)
    assert result.policy_version == SHADOW_RATING_POLICY_VERSION
    assert result.candidate_rating == rating
    assert result.coverage == coverage
    assert result.observed_weight_percent == weight
    assert result.observed_components == observed
    assert len(result.missing_components) == 5 - len(observed)


def test_withholding_an_adverse_observation_exposes_rating_inflation():
    known = {"kitchen": {"overall_quality": "modern"}, "condition": {"overall_condition": "poor"}}
    missing = {
        "kitchen": {"overall_quality": "modern"},
        "condition": {"overall_condition": "unknown"},
    }
    before, after = compute_shadow_rating(known), compute_shadow_rating(missing)
    assert (before.candidate_rating, after.candidate_rating) == (3, 5)
    assert (before.observed_weight_percent, after.observed_weight_percent) == (50, 20)


@pytest.mark.parametrize("supplied", [None, 1, 6, 4.5, True, "4"])
@pytest.mark.parametrize("layout", ["open_plan", "separate", "unknown"])
def test_model_rating_and_kitchen_living_layout_never_drive_the_candidate(supplied, layout):
    visual = {
        "overall_rating": supplied,
        "condition": {"overall_condition": "good"},
        "space": {"kitchen_living_layout": layout},
    }
    original = deepcopy(visual)
    assert compute_shadow_rating(visual) == compute_shadow_rating(
        {"condition": {"overall_condition": "good"}}
    )
    assert visual == original


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_space_only_counts_actual_boolean_observations(value):
    result = compute_shadow_rating({"space": {"is_spacious_enough": value}})
    assert result.coverage == "none"
    assert result.observed_weight_percent == 0


@pytest.mark.parametrize("value,rating", [(True, 5), (False, 3)])
def test_false_space_is_observed_and_half_stars_round_up(value, rating):
    result = compute_shadow_rating({"space": {"is_spacious_enough": value}})
    assert result.observed_weight_percent == 15
    assert result.candidate_rating == rating


@pytest.mark.parametrize("field", ["has_visible_damp", "has_visible_mold"])
def test_explicit_damage_caps_the_candidate_without_hiding_coverage(field):
    result = compute_shadow_rating(
        {"kitchen": {"overall_quality": "modern"}, "condition": {field: "yes"}}
    )
    assert result.candidate_rating == 2
    assert result.coverage == "partial"
    assert result.observed_weight_percent == 20


_GOOD_CONDITION_DECENT_FINISHES = {
    "condition": {"overall_condition": "good"},
    "kitchen": {"overall_quality": "decent"},
    "bathroom": {"overall_condition": "decent"},
    "light_space": {"natural_light": "excellent"},
    "space": {"is_spacious_enough": None},
}
_MODERN_KITCHEN_NOT_SPACIOUS = {
    "kitchen": {"overall_quality": "modern"},
    "space": {"is_spacious_enough": False},
}


@pytest.mark.parametrize(
    "visual,severity,rating",
    [
        # Unrounded 3.65: even a minor concern drops a star.
        (_GOOD_CONDITION_DECENT_FINISHES, "none", 4),
        (_GOOD_CONDITION_DECENT_FINISHES, "minor", 3),
        (_GOOD_CONDITION_DECENT_FINISHES, "moderate", 3),
        (_GOOD_CONDITION_DECENT_FINISHES, "serious", 1),
        # Unrounded 3.93: a minor concern keeps the star and a moderate one drops it.
        (_MODERN_KITCHEN_NOT_SPACIOUS, "minor", 4),
        (_MODERN_KITCHEN_NOT_SPACIOUS, "moderate", 3),
    ],
)
def test_concern_penalties_preserve_the_measured_candidate(visual, severity, rating):
    result = compute_shadow_rating({**visual, "concern_severity": severity})
    assert result.candidate_rating == rating


def test_malformed_sections_do_not_turn_into_observations():
    result = compute_shadow_rating(
        {
            "condition": "good",
            "space": [],
            "concern_severity": {},
            "kitchen": {"overall_quality": []},
        }
    )
    assert result.coverage == "none"
    assert result.observed_components == ()
    # The no-evidence default; a malformed severity applies no penalty.
    assert result.candidate_rating == 3
