"""Address normalisation used for location checks and listing matching."""

import pytest

from home_finder.utils.address import extract_outcode, is_outcode, normalize_street_name


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


@pytest.mark.parametrize(
    ("postcode", "outcode"),
    [
        ("E8 3RH", "E8"),
        ("E8", "E8"),
        ("SW1V 2SA", "SW1V"),
        ("SW1V", "SW1V"),
        ("N1 5AA", "N1"),
        ("EC1A 1BB", "EC1A"),
        ("EC1V 9BD", "EC1V"),
        ("SW1A 1AA", "SW1A"),
        ("NW8 9AB", "NW8"),
        ("w11 4ul", "W11"),
        ("e8 3rh", "E8"),
        (None, None),
        ("", None),
        ("Invalid", None),
        ("123", None),
    ],
)
def test_extract_outcode(postcode: str | None, outcode: str | None) -> None:
    assert extract_outcode(postcode) == outcode


class TestNormalizeStreetName:
    @pytest.mark.parametrize(
        ("address", "street"),
        [
            pytest.param("Mare St", "mare street", id="expands-st"),
            pytest.param("Victoria Rd", "victoria road", id="expands-rd"),
            pytest.param("Green Ave", "green avenue", id="expands-ave"),
            pytest.param("Rose Gdns", "rose gardens", id="expands-gdns"),
            pytest.param("Flat 2, Mare Street", "mare street", id="drops-flat-number"),
            pytest.param("123 Mare Street", "mare street", id="drops-house-number"),
            pytest.param("45a Victoria Road", "victoria road", id="drops-lettered-number"),
            pytest.param(
                "The Towers, 123 Mare Street, London", "mare street", id="picks-street-part"
            ),
            pytest.param("Mare Street, E8 3RH", "mare street", id="drops-postcode"),
            pytest.param(
                "Mare Street, Hackney, London", "mare street", id="drops-city-and-borough"
            ),
            pytest.param("Mare Street Hackney E8", "mare street", id="drops-borough-without-comma"),
            pytest.param("Mare Street near Dalston Junction", "mare street", id="stops-at-near"),
            pytest.param("Victoria Road off High Street", "victoria road", id="stops-at-off"),
            pytest.param("Green Lane opposite the park", "green lane", id="stops-at-opposite"),
        ],
    )
    def test_address_reduces_to_street_key(self, address: str, street: str) -> None:
        assert normalize_street_name(address) == street
