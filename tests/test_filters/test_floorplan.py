"""Tests for floorplan analysis filter."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import TextBlock
from pydantic import HttpUrl, ValidationError
from pytest_httpx import HTTPXMock

from home_finder.filters.floorplan import FloorplanAnalysis, FloorplanFilter
from home_finder.models import Property, PropertySource
from home_finder.scrapers.detail_fetcher import DetailFetcher


class TestFloorplanAnalysis:
    """Tests for FloorplanAnalysis model."""

    def test_valid_analysis(self) -> None:
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

    def test_minimal_analysis(self) -> None:
        """Should create analysis with only required fields."""
        analysis = FloorplanAnalysis(
            is_spacious_enough=False,
            confidence="low",
            reasoning="Cannot determine room sizes from floorplan",
        )
        assert analysis.living_room_sqm is None
        assert analysis.is_spacious_enough is False

    def test_invalid_confidence(self) -> None:
        """Should reject invalid confidence values."""
        with pytest.raises(ValidationError):
            FloorplanAnalysis(
                is_spacious_enough=True,
                confidence="very high",  # type: ignore[arg-type]  # Intentionally invalid
                reasoning="Test",
            )

    def test_model_is_frozen(self) -> None:
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
        url=HttpUrl("https://www.rightmove.co.uk/properties/123456789"),
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
        self, rightmove_property: Property, fixtures_path: Path, httpx_mock: HTTPXMock
    ) -> None:
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
        self, rightmove_property: Property, fixtures_path: Path, httpx_mock: HTTPXMock
    ) -> None:
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
        self, rightmove_property: Property, httpx_mock: HTTPXMock
    ) -> None:
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
        url=HttpUrl("https://www.zoopla.co.uk/to-rent/details/123456789"),
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
        url=HttpUrl("https://www.openrent.com/property/123456789"),
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
        url=HttpUrl("https://www.onthemarket.com/details/123456789"),
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


class TestDetailFetcherZoopla:
    """Tests for Zoopla detail page parsing.

    Uses curl_cffi for TLS fingerprint impersonation, so we mock AsyncSession.
    """

    async def test_extracts_floorplan_url(
        self, zoopla_property: Property, fixtures_path: Path
    ) -> None:
        """Should extract floorplan URL from Zoopla detail page."""
        html = (fixtures_path / "zoopla_detail_with_floorplan.html").read_text()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("home_finder.scrapers.detail_fetcher.AsyncSession", return_value=mock_session):
            fetcher = DetailFetcher()
            url = await fetcher.fetch_floorplan_url(zoopla_property)

        assert url == "https://lid.zoocdn.com/u/floor/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, zoopla_property: Property, fixtures_path: Path
    ) -> None:
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "zoopla_detail_no_floorplan.html").read_text()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("home_finder.scrapers.detail_fetcher.AsyncSession", return_value=mock_session):
            fetcher = DetailFetcher()
            url = await fetcher.fetch_floorplan_url(zoopla_property)

        assert url is None

    async def test_returns_none_on_http_error(self, zoopla_property: Property) -> None:
        """Should return None when HTTP request fails with 403."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("home_finder.scrapers.detail_fetcher.AsyncSession", return_value=mock_session):
            fetcher = DetailFetcher()
            url = await fetcher.fetch_floorplan_url(zoopla_property)

        assert url is None


class TestDetailFetcherOpenRent:
    """Tests for OpenRent detail page parsing."""

    async def test_extracts_floorplan_url(
        self, openrent_property: Property, fixtures_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Should extract floorplan URL from OpenRent detail page."""
        html = (fixtures_path / "openrent_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(openrent_property)

        assert url == "https://www.openrent.com/floorplan/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, openrent_property: Property, fixtures_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "openrent_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(openrent_property)

        assert url is None


class TestDetailFetcherOnTheMarket:
    """Tests for OnTheMarket detail page parsing."""

    async def test_extracts_floorplan_url(
        self, onthemarket_property: Property, fixtures_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Should extract floorplan URL from OnTheMarket detail page."""
        html = (fixtures_path / "onthemarket_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(onthemarket_property)

        assert url == "https://media.onthemarket.com/floor/123.jpg"  # from 'original' field

    async def test_returns_none_when_no_floorplan(
        self, onthemarket_property: Property, fixtures_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "onthemarket_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(onthemarket_property)

        assert url is None


class TestFloorplanFilter:
    """Tests for FloorplanFilter."""

    async def test_filters_out_properties_without_floorplan(
        self, rightmove_property: Property
    ) -> None:
        """Properties without floorplans should be excluded."""
        with patch.object(DetailFetcher, "fetch_floorplan_url", return_value=None):
            floorplan_filter = FloorplanFilter(api_key="test-key")
            results = await floorplan_filter.filter_properties([rightmove_property])

        assert len(results) == 0

    async def test_two_bed_skips_llm_analysis(self, rightmove_property: Property) -> None:
        """2+ bed properties should auto-pass without LLM call."""
        # rightmove_property has 2 bedrooms
        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            floorplan_filter = FloorplanFilter(api_key="test-key")
            # Mock the anthropic client to verify it's NOT called
            floorplan_filter._client = MagicMock()

            results = await floorplan_filter.filter_properties([rightmove_property])

        assert len(results) == 1
        prop, analysis = results[0]
        assert analysis.is_spacious_enough is True
        assert "2+ bedrooms" in analysis.reasoning
        # Verify LLM was not called
        floorplan_filter._client.messages.create.assert_not_called()

    async def test_one_bed_spacious_passes(self) -> None:
        """1-bed with spacious living room should pass."""
        one_bed = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url=HttpUrl("https://www.rightmove.co.uk/properties/999"),
            title="1 bed flat",
            price_pcm=1800,
            bedrooms=1,
            address="Test Street",
        )

        mock_response = MagicMock()
        mock_response.content = [
            TextBlock(
                type="text",
                text='{"living_room_sqm": 25, "is_spacious_enough": true, '
                '"confidence": "high", "reasoning": "Large living room"}',
            )
        ]

        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            floorplan_filter = FloorplanFilter(api_key="test-key")
            floorplan_filter._client = MagicMock()
            floorplan_filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await floorplan_filter.filter_properties([one_bed])

        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.is_spacious_enough is True
        assert analysis.living_room_sqm == 25

    async def test_one_bed_small_filtered_out(self) -> None:
        """1-bed with small living room should be filtered out."""
        one_bed = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url=HttpUrl("https://www.rightmove.co.uk/properties/999"),
            title="1 bed flat",
            price_pcm=1800,
            bedrooms=1,
            address="Test Street",
        )

        mock_response = MagicMock()
        mock_response.content = [
            TextBlock(
                type="text",
                text='{"living_room_sqm": 12, "is_spacious_enough": false, '
                '"confidence": "high", "reasoning": "Small living room"}',
            )
        ]

        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            floorplan_filter = FloorplanFilter(api_key="test-key")
            floorplan_filter._client = MagicMock()
            floorplan_filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await floorplan_filter.filter_properties([one_bed])

        assert len(results) == 0

    async def test_llm_invalid_json_filters_out(self) -> None:
        """Invalid LLM response should filter out property (fail-safe)."""
        one_bed = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url=HttpUrl("https://www.rightmove.co.uk/properties/999"),
            title="1 bed flat",
            price_pcm=1800,
            bedrooms=1,
            address="Test Street",
        )

        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text="This is not JSON")]

        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            floorplan_filter = FloorplanFilter(api_key="test-key")
            floorplan_filter._client = MagicMock()
            floorplan_filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await floorplan_filter.filter_properties([one_bed])

        assert len(results) == 0
