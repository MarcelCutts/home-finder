"""Total floor area adds up to 15 hosting points, graduated from 30 to 55 m²."""

import pytest

from home_finder.filters.fit_score import _score_hosting


def _space(**space: object) -> dict[str, object]:
    """The parts of an assessment the hosting score reads, with only ``space`` set."""
    return {"space": space, "light_space": {}, "outdoor_space": {}, "flooring_noise": {}}


@pytest.mark.parametrize(
    ("total_area_sqm", "points"),
    [(None, 0), (30, 0), (42.5, 7.5), (55, 15), (100, 15), ("unknown", 0)],
    ids=["absent", "at-30", "midpoint", "at-55", "capped", "non-numeric"],
)
def test_total_area_points(total_area_sqm, points):
    space = {} if total_area_sqm is None else {"total_area_sqm": total_area_sqm}
    assert _score_hosting(_space(**space), 2).score == points


def test_small_total_area_is_labelled_without_points():
    result = _score_hosting(_space(total_area_sqm=25), 2)
    assert result.score == 0
    assert any("Small total area" in factor["label"] for factor in result.factors)


def test_large_total_area_is_labelled_with_full_points():
    result = _score_hosting(_space(total_area_sqm=55), 2)
    assert result.score == 15
    assert any("Good total area" in factor["label"] for factor in result.factors)


@pytest.mark.parametrize(
    ("total_area_sqm", "living_room_sqm", "points"),
    [(55, 25, 45), (None, 20, 20)],
    ids=["with-total", "without-total"],
)
def test_total_area_adds_to_living_room_points(total_area_sqm, living_room_sqm, points):
    """Living room points are (sqm - 10) * 2: 30 for 25 m² and 20 for 20 m²."""
    analysis = _space(total_area_sqm=total_area_sqm, living_room_sqm=living_room_sqm)
    assert _score_hosting(analysis, 2).score == points
