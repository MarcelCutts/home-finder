"""The fit score with its working shown."""

import pytest

from home_finder.filters.fit_score import compute_fit_score_and_breakdown
from home_finder.web.working import fit_working


def dim(key: str, score: int, weight: int, confidence: float = 1.0) -> dict:
    return {
        "key": key,
        "label": key.title(),
        "score": score,
        "weight": weight,
        "confidence": confidence,
        "factors": [],
    }


def test_points_add_up_to_the_score_and_shares_follow_confidence() -> None:
    breakdown = [dim("workspace", 100, 25), dim("hosting", 95, 20), dim("sound", 88, 15, 0.5)]
    working = fit_working(breakdown)
    rows = {row["label"]: row for row in working["rows"]}
    # 25 + 20 + 7.5 = 52.5 of effective weight.
    assert rows["Workspace"]["share"] == 47.6
    assert rows["Sound"]["share"] == 14.3
    assert working["adjusted"] is True
    assert working["total"] == round((100 * 25 + 95 * 20 + 88 * 7.5) / 52.5, 1)
    assert abs(sum(row["points"] for row in working["rows"]) - working["total"]) < 0.2
    assert working["lowest"]["label"] == "Sound"


def test_full_confidence_shares_equal_the_weights() -> None:
    working = fit_working([dim("a", 60, 50), dim("b", 80, 50)])
    assert [row["share"] for row in working["rows"]] == [50.0, 50.0]
    assert working["adjusted"] is False
    assert working["total"] == 70.0


@pytest.mark.parametrize("bedrooms", [0, 1, 2])
@pytest.mark.parametrize("living_room_sqm", [11.75, 14.5, 18.0, 23.25, 31.0])
def test_total_rounds_to_the_real_score(make_quality_analysis, bedrooms, living_room_sqm) -> None:
    """The scorer rounds the dimensions it reports; the working uses its unrounded values."""
    from home_finder.models import SpaceAnalysis

    analysis = make_quality_analysis(
        space=SpaceAnalysis(living_room_sqm=living_room_sqm, is_spacious_enough=None)
    )
    score, breakdown = compute_fit_score_and_breakdown(analysis, bedrooms, postcode="E8 3RH")
    assert score is not None and breakdown is not None
    working = fit_working(breakdown)
    assert round(working["total"]) == score
