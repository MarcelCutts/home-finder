"""Tests for LocationFilter."""

import pytest
from pydantic import HttpUrl

from home_finder.filters.location import (
    BOROUGH_OUTCODES,
    LocationFilter,
    extract_outcode,
    normalize_area,
)
from home_finder.models import Property, PropertySource


@pytest.fixture
def hackney_property() -> Property:
    """Property in Hackney (E8)."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="123",
        url=HttpUrl("https://example.com/123"),
        title="Flat in Hackney",
        price_pcm=2000,
        bedrooms=1,
        address="123 Mare Street, Hackney",
        postcode="E8 3RH",
    )


@pytest.fixture
def westminster_property() -> Property:
    """Property in Westminster (SW1V) - should be filtered out for East London search."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="456",
        url=HttpUrl("https://example.com/456"),
        title="Flat in Pimlico",
        price_pcm=2000,
        bedrooms=1,
        address="123 Claverton Street",
        postcode="SW1V 2SA",
    )


@pytest.fixture
def no_postcode_property() -> Property:
    """Property without a postcode."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="789",
        url=HttpUrl("https://example.com/789"),
        title="Flat somewhere",
        price_pcm=2000,
        bedrooms=1,
        address="123 Some Street, London",
        postcode=None,
    )


class TestExtractOutcode:
    """Tests for extract_outcode function."""

    @pytest.mark.parametrize(
        ("postcode", "expected"),
        [
            ("E8 3RH", "E8"),
            ("E8", "E8"),
            ("SW1V 2SA", "SW1V"),
            ("SW1V", "SW1V"),
            ("N1 5AA", "N1"),
            ("EC1A 1BB", "EC1A"),
            ("W11 4UL", "W11"),
            ("NW8 9AB", "NW8"),
            (None, None),
            ("", None),
            ("Invalid", None),
            ("123", None),
        ],
    )
    def test_extract_outcode(self, postcode: str | None, expected: str | None) -> None:
        """Test outcode extraction from various postcodes."""
        assert extract_outcode(postcode) == expected


class TestNormalizeArea:
    """Tests for normalize_area function."""

    @pytest.mark.parametrize(
        ("area", "expected"),
        [
            ("hackney", "hackney"),
            ("Hackney", "hackney"),
            ("HACKNEY", "hackney"),
            ("tower hamlets", "tower-hamlets"),
            ("Tower Hamlets", "tower-hamlets"),
            ("waltham forest", "waltham-forest"),
            ("E8", "e8"),
            ("e8", "e8"),
        ],
    )
    def test_normalize_area(self, area: str, expected: str) -> None:
        """Test area normalization."""
        assert normalize_area(area) == expected


class TestBoroughOutcodes:
    """Tests for borough outcode mapping."""

    def test_hackney_outcodes(self) -> None:
        """Test Hackney has expected outcodes."""
        outcodes = BOROUGH_OUTCODES["hackney"]
        assert "E5" in outcodes
        assert "E8" in outcodes
        assert "E9" in outcodes
        assert "N16" in outcodes

    def test_islington_outcodes(self) -> None:
        """Test Islington has expected outcodes."""
        outcodes = BOROUGH_OUTCODES["islington"]
        assert "N1" in outcodes
        assert "N5" in outcodes
        assert "N7" in outcodes
        assert "EC1" in outcodes

    def test_tower_hamlets_outcodes(self) -> None:
        """Test Tower Hamlets has expected outcodes."""
        outcodes = BOROUGH_OUTCODES["tower-hamlets"]
        assert "E1" in outcodes
        assert "E2" in outcodes
        assert "E3" in outcodes
        assert "E14" in outcodes

    def test_westminster_outcodes(self) -> None:
        """Test Westminster has expected outcodes (for leakage detection)."""
        outcodes = BOROUGH_OUTCODES["westminster"]
        assert "SW1V" in outcodes
        assert "W1" in outcodes
        assert "WC1" in outcodes


class TestLocationFilter:
    """Tests for LocationFilter class."""

    def test_default_strict_is_true(self, no_postcode_property: Property) -> None:
        """Test that LocationFilter defaults to strict=True.

        Kills mutant: strict: bool = True → strict: bool = False.
        """
        filter = LocationFilter(["hackney"])
        assert filter.strict is True
        # Default strict should reject no-postcode properties
        assert filter.is_valid_location(no_postcode_property) is False

    def test_filter_accepts_valid_location(self, hackney_property: Property) -> None:
        """Test filter accepts property in search area."""
        filter = LocationFilter(["hackney"])
        assert filter.is_valid_location(hackney_property) is True

    def test_filter_rejects_outside_location(
        self, hackney_property: Property, westminster_property: Property
    ) -> None:
        """Test filter rejects property outside search area."""
        filter = LocationFilter(["hackney"])
        assert filter.is_valid_location(hackney_property) is True
        assert filter.is_valid_location(westminster_property) is False

    def test_filter_with_outcode_search(self, hackney_property: Property) -> None:
        """Test filter works with outcode-based search."""
        filter = LocationFilter(["e8"])
        assert filter.is_valid_location(hackney_property) is True

        # E9 should not be accepted for E8 search
        e9_property = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url=HttpUrl("https://example.com/999"),
            title="Flat in Hackney Wick",
            price_pcm=2000,
            bedrooms=1,
            address="123 Fish Island",
            postcode="E9 5AA",
        )
        assert filter.is_valid_location(e9_property) is False

    def test_filter_strict_mode_rejects_no_postcode(self, no_postcode_property: Property) -> None:
        """Test strict mode rejects properties without postcodes."""
        filter = LocationFilter(["hackney"], strict=True)
        assert filter.is_valid_location(no_postcode_property) is False

    def test_filter_non_strict_allows_no_postcode(self, no_postcode_property: Property) -> None:
        """Test non-strict mode allows properties without postcodes."""
        filter = LocationFilter(["hackney"], strict=False)
        assert filter.is_valid_location(no_postcode_property) is True

    def test_filter_multiple_areas(
        self, hackney_property: Property, westminster_property: Property
    ) -> None:
        """Test filter with multiple search areas."""
        filter = LocationFilter(["hackney", "islington", "tower-hamlets"])
        assert filter.is_valid_location(hackney_property) is True
        assert filter.is_valid_location(westminster_property) is False

        # Add Westminster to search areas
        filter2 = LocationFilter(["hackney", "westminster"])
        assert filter2.is_valid_location(hackney_property) is True
        assert filter2.is_valid_location(westminster_property) is True

    def test_filter_properties_list(
        self,
        hackney_property: Property,
        westminster_property: Property,
        no_postcode_property: Property,
    ) -> None:
        """Test filtering a list of properties."""
        properties = [hackney_property, westminster_property, no_postcode_property]

        # Non-strict mode
        filter = LocationFilter(["hackney"], strict=False)
        valid = filter.filter_properties(properties)

        assert len(valid) == 2
        assert hackney_property in valid
        assert westminster_property not in valid
        assert no_postcode_property in valid  # allowed through non-strict

    def test_filter_properties_strict_mode(
        self,
        hackney_property: Property,
        westminster_property: Property,
        no_postcode_property: Property,
    ) -> None:
        """Test filtering a list of properties in strict mode."""
        properties = [hackney_property, westminster_property, no_postcode_property]

        filter = LocationFilter(["hackney"], strict=True)
        valid = filter.filter_properties(properties)

        assert len(valid) == 1
        assert hackney_property in valid
        assert westminster_property not in valid
        assert no_postcode_property not in valid

    def test_filter_builds_correct_valid_outcodes(self) -> None:
        """Test filter builds correct set of valid outcodes."""
        filter = LocationFilter(["hackney", "e3", "n15"])

        # Should include all Hackney outcodes
        assert "E5" in filter.valid_outcodes
        assert "E8" in filter.valid_outcodes
        assert "E9" in filter.valid_outcodes
        assert "N16" in filter.valid_outcodes

        # Should include E3 and N15 as specific outcodes
        assert "E3" in filter.valid_outcodes
        assert "N15" in filter.valid_outcodes

        # Should NOT include Westminster outcodes
        assert "SW1V" not in filter.valid_outcodes
        assert "W11" not in filter.valid_outcodes

    def test_real_world_leakage_scenario(self) -> None:
        """Test filter catches real-world leakage examples."""
        # These are the exact properties the user reported
        leaky_properties = [
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="171605924",
                url=HttpUrl("https://www.rightmove.co.uk/properties/171605924"),
                title="Flat, Claverton Street, SW1V",
                price_pcm=2167,
                bedrooms=1,
                address="Claverton Street, SW1V",
                postcode="SW1V",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="171587792",
                url=HttpUrl("https://www.rightmove.co.uk/properties/171587792"),
                title="Apartment, Abercorn Place, London, NW8",
                price_pcm=2150,
                bedrooms=1,
                address="Abercorn Place, London, NW8",
                postcode="NW8",
            ),
        ]

        # Filter for East/North London areas (user's actual search)
        search_areas = [
            "hackney",
            "islington",
            "haringey",
            "tower-hamlets",
            "e3",
            "e5",
            "e9",
            "e10",
            "n15",
        ]
        filter = LocationFilter(search_areas, strict=False)

        valid = filter.filter_properties(leaky_properties)

        # All leaky properties should be rejected
        assert len(valid) == 0
