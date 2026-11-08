"""The alert's space line prefers a listed floor area over the analysis estimate."""

from home_finder.models import SpaceAnalysis
from home_finder.notifiers.telegram import _format_space_info


def test_listed_floor_area_is_shown_without_estimate_marker(make_quality_analysis):
    assessment = make_quality_analysis(space=SpaceAnalysis(living_room_sqm=20.0))
    info = _format_space_info(assessment, floor_area_sqm=65.0)
    assert "65 m²" in info
    assert "~65" not in info


def test_estimated_total_area_is_marked_approximate(make_quality_analysis):
    assessment = make_quality_analysis(
        space=SpaceAnalysis(living_room_sqm=20.0, total_area_sqm=60.0)
    )
    assert "~60 m²" in _format_space_info(assessment)


def test_listed_floor_area_replaces_the_estimate(make_quality_analysis):
    assessment = make_quality_analysis(
        space=SpaceAnalysis(living_room_sqm=20.0, total_area_sqm=60.0)
    )
    info = _format_space_info(assessment, floor_area_sqm=70.0)
    assert "70 m²" in info
    assert "~60" not in info


def test_unknown_size_is_stated(make_quality_analysis):
    assessment = make_quality_analysis(space=SpaceAnalysis(living_room_sqm=None))
    assert _format_space_info(assessment) == "Size unknown"
