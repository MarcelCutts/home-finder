"""Tests for property deduplication."""

from pydantic import HttpUrl

from home_finder.filters.scoring import (
    coordinates_match,
    is_full_postcode,
    prices_match,
)
from home_finder.models import Property, PropertySource


class TestFuzzyPriceMatching:
    """Tests for fuzzy price matching."""

    def test_prices_match_exact(self) -> None:
        """Test that exact prices match."""
        assert prices_match(2000, 2000) is True

    def test_prices_match_within_tolerance(self) -> None:
        """Test that prices within 3% tolerance match."""
        # 1.5% difference
        assert prices_match(2000, 2030) is True
        # 3% difference (at boundary)
        assert prices_match(2000, 2060) is True
        # Reverse direction
        assert prices_match(2030, 2000) is True

    def test_prices_dont_match_outside_tolerance(self) -> None:
        """Test that prices outside 3% tolerance don't match."""
        # ~5% difference (was matching before, now too far)
        assert prices_match(2000, 2100) is False
        # ~10% difference
        assert prices_match(2000, 2200) is False

    def test_prices_match_zero_handling(self) -> None:
        """Test that zero prices are handled correctly."""
        assert prices_match(0, 0) is True
        assert prices_match(0, 2000) is False
        assert prices_match(2000, 0) is False


class TestFullPostcodeDetection:
    """Tests for full postcode validation."""

    def test_full_postcode_detected(self) -> None:
        """Test that full postcodes are correctly identified."""
        assert is_full_postcode("E8 3RH") is True
        assert is_full_postcode("SW1A 1AA") is True
        assert is_full_postcode("EC1A 1BB") is True
        assert is_full_postcode("W1A 0AX") is True

    def test_partial_postcode_rejected(self) -> None:
        """Test that partial postcodes (outcode only) are rejected."""
        assert is_full_postcode("E8") is False
        assert is_full_postcode("SW1A") is False
        assert is_full_postcode("EC1") is False

    def test_none_and_empty_rejected(self) -> None:
        """Test that None and empty strings are rejected."""
        assert is_full_postcode(None) is False
        assert is_full_postcode("") is False


class TestCoordinateMatching:
    """Tests for coordinate-based matching."""

    def test_coordinates_match_same_location(self) -> None:
        """Test that identical coordinates match."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        assert coordinates_match(prop1, prop2) is True

    def test_coordinates_match_within_50m(self) -> None:
        """Test that coordinates within 50m match."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        # ~30m away
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5077,
            longitude=-0.1278,
        )
        assert coordinates_match(prop1, prop2) is True

    def test_coordinates_dont_match_far_apart(self) -> None:
        """Test that coordinates >50m apart don't match."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        # ~500m away
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5120,
            longitude=-0.1278,
        )
        assert coordinates_match(prop1, prop2) is False

    def test_coordinates_match_requires_both_have_coords(self) -> None:
        """Test that matching fails if either property lacks coordinates."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
        )
        assert coordinates_match(prop1, prop2) is False
