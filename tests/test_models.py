"""Tests for Pydantic models."""

from datetime import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from home_finder.models import (
    Property,
    PropertySource,
    SearchCriteria,
    TransportMode,
)


class TestProperty:
    """Tests for the Property model."""

    def test_valid_property(self, sample_property: Property) -> None:
        """Test that a valid property is created correctly."""
        assert sample_property.source == PropertySource.OPENRENT
        assert sample_property.source_id == "12345"
        assert sample_property.price_pcm == 1850
        assert sample_property.bedrooms == 1
        assert sample_property.postcode == "E8 3RH"

    def test_unique_id(self, sample_property: Property) -> None:
        """Test unique_id property."""
        assert sample_property.unique_id == "openrent:12345"

    def test_postcode_normalization(self) -> None:
        """Test that postcodes are normalized."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=1000,
            bedrooms=1,
            address="Test Address",
            postcode="e8  3rh",  # lowercase with extra space
        )
        assert prop.postcode == "E8 3RH"

    def test_postcode_none_allowed(self, sample_property_no_coords: Property) -> None:
        """Test that postcode can be None."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=1000,
            bedrooms=1,
            address="Test Address",
            postcode=None,
        )
        assert prop.postcode is None

    def test_negative_price_rejected(self) -> None:
        """Test that negative prices are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Property(
                source=PropertySource.OPENRENT,
                source_id="1",
                url="https://example.com/1",
                title="Test",
                price_pcm=-100,
                bedrooms=1,
                address="Test Address",
            )
        assert "price_pcm" in str(exc_info.value)

    def test_negative_bedrooms_rejected(self) -> None:
        """Test that negative bedrooms are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Property(
                source=PropertySource.OPENRENT,
                source_id="1",
                url="https://example.com/1",
                title="Test",
                price_pcm=1000,
                bedrooms=-1,
                address="Test Address",
            )
        assert "bedrooms" in str(exc_info.value)

    def test_invalid_latitude_rejected(self) -> None:
        """Test that invalid latitude is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Property(
                source=PropertySource.OPENRENT,
                source_id="1",
                url="https://example.com/1",
                title="Test",
                price_pcm=1000,
                bedrooms=1,
                address="Test Address",
                latitude=91.0,  # Invalid: > 90
                longitude=0.0,
            )
        assert "latitude" in str(exc_info.value)

    def test_invalid_longitude_rejected(self) -> None:
        """Test that invalid longitude is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Property(
                source=PropertySource.OPENRENT,
                source_id="1",
                url="https://example.com/1",
                title="Test",
                price_pcm=1000,
                bedrooms=1,
                address="Test Address",
                latitude=51.5,
                longitude=181.0,  # Invalid: > 180
            )
        assert "longitude" in str(exc_info.value)

    def test_partial_coordinates_rejected(self) -> None:
        """Test that having only latitude or only longitude is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Property(
                source=PropertySource.OPENRENT,
                source_id="1",
                url="https://example.com/1",
                title="Test",
                price_pcm=1000,
                bedrooms=1,
                address="Test Address",
                latitude=51.5,
                longitude=None,
            )
        assert "latitude and longitude" in str(exc_info.value)

    def test_invalid_url_rejected(self) -> None:
        """Test that invalid URLs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Property(
                source=PropertySource.OPENRENT,
                source_id="1",
                url="not-a-url",
                title="Test",
                price_pcm=1000,
                bedrooms=1,
                address="Test Address",
            )
        assert "url" in str(exc_info.value)

    def test_property_is_immutable(self, sample_property: Property) -> None:
        """Test that Property instances are immutable."""
        with pytest.raises(ValidationError):
            sample_property.price_pcm = 2000  # type: ignore[misc]

    @given(
        price=st.integers(min_value=0, max_value=100000),
        bedrooms=st.integers(min_value=0, max_value=10),
    )
    def test_valid_price_and_bedrooms_hypothesis(self, price: int, bedrooms: int) -> None:
        """Property-based test for valid price and bedroom combinations."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=price,
            bedrooms=bedrooms,
            address="Test Address",
        )
        assert prop.price_pcm == price
        assert prop.bedrooms == bedrooms


class TestSearchCriteria:
    """Tests for the SearchCriteria model."""

    def test_valid_criteria(self, default_search_criteria: SearchCriteria) -> None:
        """Test that valid search criteria is created correctly."""
        assert default_search_criteria.min_price == 1800
        assert default_search_criteria.max_price == 2200
        assert default_search_criteria.min_bedrooms == 1
        assert default_search_criteria.max_bedrooms == 2
        assert default_search_criteria.destination_postcode == "N1 5AA"
        assert default_search_criteria.max_commute_minutes == 30

    def test_postcode_normalization(self) -> None:
        """Test that destination postcode is normalized."""
        criteria = SearchCriteria(
            max_price=2000,
            max_bedrooms=2,
            destination_postcode="n1  5aa",
            max_commute_minutes=30,
        )
        assert criteria.destination_postcode == "N1 5AA"

    def test_invalid_price_range(self) -> None:
        """Test that min_price > max_price is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SearchCriteria(
                min_price=2500,
                max_price=2000,
                max_bedrooms=2,
                destination_postcode="N1 5AA",
                max_commute_minutes=30,
            )
        assert "min_price" in str(exc_info.value)

    def test_invalid_bedroom_range(self) -> None:
        """Test that min_bedrooms > max_bedrooms is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SearchCriteria(
                max_price=2000,
                min_bedrooms=3,
                max_bedrooms=2,
                destination_postcode="N1 5AA",
                max_commute_minutes=30,
            )
        assert "min_bedrooms" in str(exc_info.value)

    def test_commute_minutes_bounds(self) -> None:
        """Test that commute minutes must be within bounds."""
        with pytest.raises(ValidationError):
            SearchCriteria(
                max_price=2000,
                max_bedrooms=2,
                destination_postcode="N1 5AA",
                max_commute_minutes=0,
            )

        with pytest.raises(ValidationError):
            SearchCriteria(
                max_price=2000,
                max_bedrooms=2,
                destination_postcode="N1 5AA",
                max_commute_minutes=150,
            )

    def test_matches_property_in_range(
        self, default_search_criteria: SearchCriteria, sample_property: Property
    ) -> None:
        """Test that a property within criteria matches."""
        assert default_search_criteria.matches_property(sample_property) is True

    def test_matches_property_price_too_low(self, default_search_criteria: SearchCriteria) -> None:
        """Test that a property with price below range doesn't match."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=1500,  # Below 1800
            bedrooms=1,
            address="Test Address",
        )
        assert default_search_criteria.matches_property(prop) is False

    def test_matches_property_price_too_high(self, default_search_criteria: SearchCriteria) -> None:
        """Test that a property with price above range doesn't match."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=2500,  # Above 2200
            bedrooms=1,
            address="Test Address",
        )
        assert default_search_criteria.matches_property(prop) is False

    def test_matches_property_bedrooms_too_few(
        self, default_search_criteria: SearchCriteria
    ) -> None:
        """Test that a property with too few bedrooms doesn't match."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=1900,
            bedrooms=0,  # Below 1
            address="Test Address",
        )
        assert default_search_criteria.matches_property(prop) is False

    def test_matches_property_bedrooms_too_many(
        self, default_search_criteria: SearchCriteria
    ) -> None:
        """Test that a property with too many bedrooms doesn't match."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=1900,
            bedrooms=3,  # Above 2
            address="Test Address",
        )
        assert default_search_criteria.matches_property(prop) is False

    def test_matches_property_at_boundaries(self, default_search_criteria: SearchCriteria) -> None:
        """Test that properties at exact boundary values match."""
        # At min price and min bedrooms
        prop_min = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="Test",
            price_pcm=1800,
            bedrooms=1,
            address="Test Address",
        )
        assert default_search_criteria.matches_property(prop_min) is True

        # At max price and max bedrooms
        prop_max = Property(
            source=PropertySource.OPENRENT,
            source_id="2",
            url="https://example.com/2",
            title="Test",
            price_pcm=2200,
            bedrooms=2,
            address="Test Address",
        )
        assert default_search_criteria.matches_property(prop_max) is True

    @given(
        min_price=st.integers(min_value=0, max_value=5000),
        max_price=st.integers(min_value=0, max_value=10000),
        min_beds=st.integers(min_value=0, max_value=5),
        max_beds=st.integers(min_value=0, max_value=10),
    )
    def test_criteria_validation_hypothesis(
        self, min_price: int, max_price: int, min_beds: int, max_beds: int
    ) -> None:
        """Property-based test for SearchCriteria validation."""
        valid = min_price <= max_price and min_beds <= max_beds
        if valid:
            criteria = SearchCriteria(
                min_price=min_price,
                max_price=max_price,
                min_bedrooms=min_beds,
                max_bedrooms=max_beds,
                destination_postcode="N1 5AA",
                max_commute_minutes=30,
            )
            assert criteria.min_price == min_price
            assert criteria.max_price == max_price
        else:
            with pytest.raises(ValidationError):
                SearchCriteria(
                    min_price=min_price,
                    max_price=max_price,
                    min_bedrooms=min_beds,
                    max_bedrooms=max_beds,
                    destination_postcode="N1 5AA",
                    max_commute_minutes=30,
                )


class TestPropertySource:
    """Tests for PropertySource enum."""

    def test_all_sources_have_values(self) -> None:
        """Test that all property sources have string values."""
        assert PropertySource.RIGHTMOVE.value == "rightmove"
        assert PropertySource.ZOOPLA.value == "zoopla"
        assert PropertySource.OPENRENT.value == "openrent"
        assert PropertySource.ONTHEMARKET.value == "onthemarket"


class TestTransportMode:
    """Tests for TransportMode enum."""

    def test_all_modes_have_values(self) -> None:
        """Test that all transport modes have string values."""
        assert TransportMode.CYCLING.value == "cycling"
        assert TransportMode.PUBLIC_TRANSPORT.value == "public_transport"
        assert TransportMode.DRIVING.value == "driving"
        assert TransportMode.WALKING.value == "walking"
