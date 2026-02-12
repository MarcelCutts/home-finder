"""Shared fixtures for notifier tests."""

from datetime import datetime

import pytest
from pydantic import HttpUrl

from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
    TrackedProperty,
    TransportMode,
    ValueAnalysis,
)


@pytest.fixture
def sample_property() -> Property:
    """Create a sample property for notification tests."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=HttpUrl("https://openrent.com/property/12345"),
        title="1 Bed Flat, Mare Street",
        price_pcm=1900,
        bedrooms=1,
        address="123 Mare Street, Hackney, E8 3RH",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        first_seen=datetime(2025, 1, 20, 14, 30),
    )


@pytest.fixture
def sample_tracked_property(sample_property: Property) -> TrackedProperty:
    """Create a sample tracked property with commute info."""
    return TrackedProperty(
        property=sample_property,
        commute_minutes=18,
        transport_mode=TransportMode.CYCLING,
    )


@pytest.fixture
def sample_merged_property(sample_property: Property) -> MergedProperty:
    """Create a sample merged property with two sources."""
    zoopla_url = HttpUrl("https://www.zoopla.co.uk/to-rent/details/99999")
    return MergedProperty(
        canonical=sample_property,
        sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.OPENRENT: sample_property.url,
            PropertySource.ZOOPLA: zoopla_url,
        },
        images=(
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        ),
        min_price=1850,
        max_price=1900,
    )


@pytest.fixture
def sample_quality_analysis() -> PropertyQualityAnalysis:
    """Create a sample quality analysis with overall_rating."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher="yes",
            notes="Nice kitchen",
        ),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True, notes="Bright"),
        space=SpaceAnalysis(living_room_sqm=20.0, is_spacious_enough=True, confidence="high"),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2200,
            difference=-300,
            rating="excellent",
            note="£300 below E8 average",
        ),
        overall_rating=4,
        summary="Bright flat with modern kitchen.",
    )
