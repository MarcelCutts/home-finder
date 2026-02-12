"""Tests for address utilities including outcode detection."""

import pytest

from home_finder.utils.address import is_outcode


class TestIsOutcode:
    """Tests for is_outcode function."""

    @pytest.mark.parametrize(
        "outcode",
        [
            "E8",
            "E3",
            "E5",
            "E9",
            "E10",
            "N1",
            "N15",
            "N16",
            "SW1",
            "SW1A",
            "EC1",
            "EC1A",
            "WC1",
            "W1",
            "SE1",
            "NW1",
        ],
    )
    def test_valid_outcodes(self, outcode: str) -> None:
        """Test that valid outcodes are detected."""
        assert is_outcode(outcode) is True

    @pytest.mark.parametrize(
        "outcode",
        [
            "e8",
            "e10",
            "n15",
            "sw1a",
        ],
    )
    def test_lowercase_outcodes(self, outcode: str) -> None:
        """Test that lowercase outcodes are also detected."""
        assert is_outcode(outcode) is True

    @pytest.mark.parametrize(
        "outcode",
        [
            " E8 ",
            " N15",
            "E10 ",
        ],
    )
    def test_outcodes_with_whitespace(self, outcode: str) -> None:
        """Test that outcodes with surrounding whitespace are detected."""
        assert is_outcode(outcode) is True

    @pytest.mark.parametrize(
        "area",
        [
            "hackney",
            "islington",
            "tower-hamlets",
            "haringey",
            "Hackney",
            "Tower Hamlets",
            "london",
            "London",
        ],
    )
    def test_boroughs_not_detected_as_outcodes(self, area: str) -> None:
        """Test that borough names are not detected as outcodes."""
        assert is_outcode(area) is False

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            " ",
            "E8 3RH",  # Full postcode, not just outcode
            "123",
            "ABC",
            "E",
            "EEE1",
            "1E",
            "E123",
        ],
    )
    def test_invalid_formats(self, invalid: str) -> None:
        """Test that invalid formats are not detected as outcodes."""
        assert is_outcode(invalid) is False
