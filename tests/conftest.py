"""Shared pytest fixtures."""

import os
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings
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
    SearchCriteria,
    SpaceAnalysis,
    TransportMode,
    ValueAnalysis,
)

# Hypothesis settings profiles for different environments
settings.register_profile("fast", max_examples=10)
settings.register_profile(
    "ci",
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "fast"))


@pytest.fixture
def fixtures_path() -> Path:
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_property() -> Property:
    """A valid sample property for testing."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=HttpUrl("https://www.openrent.com/property/12345"),
        title="Spacious 1-bed flat in Hackney",
        price_pcm=1850,
        bedrooms=1,
        address="123 Mare Street, Hackney, London",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        description="A lovely flat with good transport links.",
        first_seen=datetime(2025, 1, 15, 10, 30),
    )


@pytest.fixture
def sample_property_no_coords() -> Property:
    """A valid sample property without coordinates."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="67890",
        url=HttpUrl("https://www.rightmove.co.uk/properties/67890"),
        title="2-bed apartment in Islington",
        price_pcm=2100,
        bedrooms=2,
        address="45 Upper Street, Islington, London",
        postcode="N1 0NY",
        first_seen=datetime(2025, 1, 16, 14, 0),
    )


@pytest.fixture
def default_search_criteria() -> SearchCriteria:
    """Default search criteria matching the plan requirements."""
    return SearchCriteria(
        min_price=1800,
        max_price=2200,
        min_bedrooms=1,
        max_bedrooms=2,
        destination_postcode="N1 5AA",
        max_commute_minutes=30,
        transport_modes=(TransportMode.CYCLING, TransportMode.PUBLIC_TRANSPORT),
    )


@pytest.fixture
def enriched_merged_property() -> MergedProperty:
    """MergedProperty with gallery, floorplan, descriptions, multi-source."""
    openrent_url = HttpUrl("https://www.openrent.com/property/12345")
    zoopla_url = HttpUrl("https://www.zoopla.co.uk/to-rent/details/99999")

    canonical = Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=openrent_url,
        title="Spacious 2-bed flat in Hackney",
        price_pcm=1800,
        bedrooms=2,
        address="123 Mare Street, Hackney, London",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        description="A lovely 2-bed flat with good transport links.",
        first_seen=datetime(2025, 1, 15, 10, 30),
    )

    return MergedProperty(
        canonical=canonical,
        sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.OPENRENT: openrent_url,
            PropertySource.ZOOPLA: zoopla_url,
        },
        images=(
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img2.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img3.jpg"),
                source=PropertySource.ZOOPLA,
                image_type="gallery",
            ),
        ),
        floorplan=PropertyImage(
            url=HttpUrl("https://example.com/floorplan.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="floorplan",
        ),
        min_price=1800,
        max_price=1850,
        descriptions={
            PropertySource.OPENRENT: "A lovely 2-bed flat with good transport links.",
            PropertySource.ZOOPLA: "Spacious two bedroom apartment near Mare Street.",
        },
    )


@pytest.fixture
def sample_quality_analysis() -> PropertyQualityAnalysis:
    """Complete PropertyQualityAnalysis for notification/DB tests."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher=True,
            has_washing_machine="yes",
            notes="Modern integrated kitchen with gas hob",
        ),
        condition=ConditionAnalysis(
            overall_condition="good",
            has_visible_damp="no",
            has_visible_mold="no",
            has_worn_fixtures=False,
            maintenance_concerns=[],
            confidence="high",
        ),
        light_space=LightSpaceAnalysis(
            natural_light="good",
            window_sizes="medium",
            feels_spacious=True,
            ceiling_height="standard",
            notes="Good natural light throughout",
        ),
        space=SpaceAnalysis(
            living_room_sqm=22.0,
            is_spacious_enough=True,
            confidence="high",
        ),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2350,
            difference=-550,
            rating="excellent",
            note="£550 below E8 average",
            quality_adjusted_rating="excellent",
            quality_adjusted_note="Good condition at well below market rate",
        ),
        overall_rating=4,
        summary="Bright, well-maintained flat with modern kitchen. Good for home office and hosting.",
    )
