"""Tests for floorplan analysis filter."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from home_finder.filters.floorplan import DetailFetcher, FloorplanAnalysis
from home_finder.models import Property, PropertySource


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


@pytest.fixture
def rightmove_property() -> Property:
    """Sample Rightmove property."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="123456789",
        url="https://www.rightmove.co.uk/properties/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


@pytest.fixture
def fixtures_path() -> Path:
    """Path to test fixtures."""
    return Path(__file__).parent.parent / "fixtures"


class TestDetailFetcherRightmove:
    """Tests for Rightmove detail page parsing."""

    async def test_extracts_floorplan_url(
        self, rightmove_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from Rightmove detail page."""
        html = (fixtures_path / "rightmove_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/properties/123456789",
            html=html,
        )

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(rightmove_property)

        assert url == "https://media.rightmove.co.uk/floor/123_FLP_00.jpg"

    async def test_returns_none_when_no_floorplan(
        self, rightmove_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "rightmove_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/properties/123456789",
            html=html,
        )

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(rightmove_property)

        assert url is None

    async def test_returns_none_on_http_error(
        self, rightmove_property: Property, httpx_mock
    ):
        """Should return None when HTTP request fails."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/properties/123456789",
            status_code=404,
        )

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(rightmove_property)

        assert url is None


@pytest.fixture
def zoopla_property() -> Property:
    """Sample Zoopla property."""
    return Property(
        source=PropertySource.ZOOPLA,
        source_id="123456789",
        url="https://www.zoopla.co.uk/to-rent/details/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


@pytest.fixture
def openrent_property() -> Property:
    """Sample OpenRent property."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="123456789",
        url="https://www.openrent.com/property/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


@pytest.fixture
def onthemarket_property() -> Property:
    """Sample OnTheMarket property."""
    return Property(
        source=PropertySource.ONTHEMARKET,
        source_id="123456789",
        url="https://www.onthemarket.com/details/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


class TestDetailFetcherZoopla:
    """Tests for Zoopla detail page parsing."""

    async def test_extracts_floorplan_url(
        self, zoopla_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from Zoopla detail page."""
        html = (fixtures_path / "zoopla_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(zoopla_property)

        assert url == "https://lid.zoocdn.com/u/floor/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, zoopla_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "zoopla_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(zoopla_property)

        assert url is None


class TestDetailFetcherOpenRent:
    """Tests for OpenRent detail page parsing."""

    async def test_extracts_floorplan_url(
        self, openrent_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from OpenRent detail page."""
        html = (fixtures_path / "openrent_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(openrent_property)

        assert url == "https://www.openrent.com/floorplan/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, openrent_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "openrent_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(openrent_property)

        assert url is None


class TestDetailFetcherOnTheMarket:
    """Tests for OnTheMarket detail page parsing."""

    async def test_extracts_floorplan_url(
        self, onthemarket_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from OnTheMarket detail page."""
        html = (fixtures_path / "onthemarket_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(onthemarket_property)

        assert url == "https://media.onthemarket.com/floor/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, onthemarket_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "onthemarket_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(onthemarket_property)

        assert url is None
