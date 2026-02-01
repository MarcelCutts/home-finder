"""Shared pytest fixtures."""

from datetime import datetime
from pathlib import Path

import pytest

from home_finder.models import Property, PropertySource, SearchCriteria, TransportMode


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
        url="https://www.openrent.com/property/12345",
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
        url="https://www.rightmove.co.uk/properties/67890",
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
