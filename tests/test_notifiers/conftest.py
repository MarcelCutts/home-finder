"""Shared fixtures for notifier tests.

Message assertions in this directory depend on these values (the "1 Bed Flat" title,
the "£300 below E8 average" value note, the viewing notes), so they are named
``notification_*`` rather than reusing the root ``sample_*`` fixture names.
"""

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
    ViewingNotes,
)


@pytest.fixture
def notification_property() -> Property:
    """A single-source OpenRent listing in E8 with coordinates."""
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
def notification_tracked_property(notification_property: Property) -> TrackedProperty:
    """The notification property with an 18-minute cycling commute."""
    return TrackedProperty(
        property=notification_property,
        commute_minutes=18,
        transport_mode=TransportMode.CYCLING,
    )


@pytest.fixture
def notification_merged_property(notification_property: Property) -> MergedProperty:
    """The notification property listed on OpenRent and Zoopla, with one gallery image."""
    zoopla_url = HttpUrl("https://www.zoopla.co.uk/to-rent/details/99999")
    return MergedProperty(
        canonical=notification_property,
        sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.OPENRENT: notification_property.url,
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
def notification_quality_analysis() -> PropertyQualityAnalysis:
    """A rated analysis with highlights, lowlights, a value note and viewing notes."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher="yes",
            notes="Nice kitchen",
        ),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True, notes="Bright"),
        space=SpaceAnalysis(living_room_sqm=20.0, is_spacious_enough=True),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2200,
            difference=-300,
            rating="excellent",
            note="£300 below E8 average",
        ),
        overall_rating=4,
        summary="Bright flat with modern kitchen.",
        one_line="Bright modern flat with gas kitchen, good natural light",
        highlights=["Gas hob", "Good light", "Spacious living room"],
        lowlights=["No garden", "Street noise"],
        viewing_notes=ViewingNotes(
            check_items=["Check water pressure", "Inspect window seals"],
            questions_for_agent=["Ask about sound insulation"],
            deal_breaker_tests=["Test internet speed"],
        ),
    )
