"""Tests for floorplan analysis filter."""

import pytest
from pydantic import ValidationError

from home_finder.filters.floorplan import FloorplanAnalysis


class TestFloorplanAnalysis:
    """Tests for FloorplanAnalysis model."""

    def test_valid_analysis(self):
        """Should create valid analysis with all fields."""
        analysis = FloorplanAnalysis(
            living_room_sqm=25.5,
            is_spacious_enough=True,
            confidence="high",
            reasoning="Living room is 25.5 sqm, suitable for office and hosting",
        )
        assert analysis.living_room_sqm == 25.5
        assert analysis.is_spacious_enough is True
        assert analysis.confidence == "high"

    def test_minimal_analysis(self):
        """Should create analysis with only required fields."""
        analysis = FloorplanAnalysis(
            is_spacious_enough=False,
            confidence="low",
            reasoning="Cannot determine room sizes from floorplan",
        )
        assert analysis.living_room_sqm is None
        assert analysis.is_spacious_enough is False

    def test_invalid_confidence(self):
        """Should reject invalid confidence values."""
        with pytest.raises(ValidationError):
            FloorplanAnalysis(
                is_spacious_enough=True,
                confidence="very high",  # Invalid
                reasoning="Test",
            )

    def test_model_is_frozen(self):
        """Should be immutable."""
        analysis = FloorplanAnalysis(
            is_spacious_enough=True,
            confidence="high",
            reasoning="Test",
        )
        with pytest.raises(ValidationError):
            analysis.is_spacious_enough = False
