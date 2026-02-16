"""Tests for OnTheMarket scraper."""

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_finder.models import PropertySource
from home_finder.scrapers.onthemarket import OnTheMarketScraper
from home_finder.scrapers.parsing import extract_bedrooms, extract_postcode, extract_price


@pytest.fixture
def onthemarket_scraper() -> Generator[OnTheMarketScraper, None, None]:
    """Create an OnTheMarket scraper instance with session cleanup.

    Preventive: cancel curl_cffi's background task if a real session was created.
    """
    scraper = OnTheMarketScraper()
    yield scraper
    if scraper._session is not None:
        acurl = getattr(scraper._session, "_acurl", None)
        if acurl is not None and hasattr(acurl, "_timeout_checker"):
            acurl._timeout_checker.cancel()


@pytest.fixture
def sample_listing() -> dict[str, Any]:
    """Sample listing from __NEXT_DATA__ JSON."""
    return {
        "id": 15234567,
        "property-title": "1 bedroom flat to rent",
        "address": "Wayland Avenue, Hackney, London E8 3RH",
        "short-price": "£2,300",
        "bedrooms": 1,
        "details-url": "/details/15234567/",
        "images": [
            {
                "default": "https://media.onthemarket.com/image1.jpg",
                "webp": "https://media.onthemarket.com/image1.webp",
            },
            {
                "default": "https://media.onthemarket.com/image2.jpg",
                "webp": "https://media.onthemarket.com/image2.webp",
            },
        ],
        "location": {"lat": 51.549, "lon": -0.055},
    }


@pytest.fixture
def sample_next_data(sample_listing: dict[str, Any]) -> str:
    """Sample HTML with __NEXT_DATA__ JSON."""
    next_data = {
        "props": {
            "initialReduxState": {
                "results": {
                    "list": [
                        sample_listing,
                        {
                            "id": 15345678,
                            "property-title": "2 bedroom apartment to rent",
                            "address": "Mare Street, London E8",
                            "short-price": "£1,950",
                            "bedrooms": 2,
                            "details-url": "/details/15345678/",
                            "images": [],
                        },
                        {
                            "id": 15456789,
                            "property-title": "Studio to rent",
                            "address": "Dalston Lane, London E8",
                            "short-price": "£2,100",
                            "bedrooms": 0,
                            "details-url": "/details/15456789/",
                        },
                    ]
                }
            }
        }
    }
    data = json.dumps(next_data)
    script = f'<script id="__NEXT_DATA__" type="application/json">{data}</script>'
    return f"<html><body>{script}</body></html>"


class TestOnTheMarketScraper:
    """Tests for OnTheMarketScraper."""

    def test_source_property(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test that source returns ONTHEMARKET."""
        assert onthemarket_scraper.source == PropertySource.ONTHEMARKET

    def test_build_search_url(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test URL building with search parameters."""
        url = onthemarket_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "onthemarket.com" in url
        assert "min-price=1800" in url
        assert "max-price=2200" in url
        assert "min-bedrooms=1" in url
        assert "max-bedrooms=2" in url
        assert "to-rent" in url

    def test_build_search_url_with_area(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test URL building includes area."""
        url = onthemarket_scraper._build_search_url(
            area="islington",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "islington" in url.lower()

    async def test_scrape_uses_curl_cffi(
        self, onthemarket_scraper: OnTheMarketScraper, sample_next_data: str
    ) -> None:
        """Test that scrape uses curl_cffi with Chrome impersonation."""
        # Page 1 returns data, page 2 returns empty to stop pagination
        mock_response_with_data = MagicMock()
        mock_response_with_data.status_code = 200
        mock_response_with_data.text = sample_next_data

        empty_next_data = '{"props":{"initialReduxState":{"results":{"list":[]}}}}'
        mock_response_empty = MagicMock()
        mock_response_empty.status_code = 200
        mock_response_empty.text = empty_next_data

        mock_session = MagicMock()
        mock_session.get = AsyncMock(side_effect=[mock_response_with_data, mock_response_empty])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("home_finder.scrapers.onthemarket.AsyncSession", return_value=mock_session):
            properties = await onthemarket_scraper.scrape(
                min_price=1500,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
            )

        # Verify curl_cffi was used with impersonation
        assert mock_session.get.call_count >= 1
        call_kwargs = mock_session.get.call_args_list[0][1]
        assert call_kwargs["impersonate"] == "chrome"

        # Verify properties were parsed from first page
        assert len(properties) == 3


class TestOnTheMarketParser:
    """Tests for OnTheMarket JSON parsing."""

    def test_parse_next_data(
        self, onthemarket_scraper: OnTheMarketScraper, sample_next_data: str
    ) -> None:
        """Test parsing of __NEXT_DATA__ JSON."""
        properties = onthemarket_scraper._parse_next_data(sample_next_data)

        assert len(properties) == 3

        # Check first property
        prop1 = next(p for p in properties if p.source_id == "15234567")
        assert prop1.price_pcm == 2300
        assert prop1.bedrooms == 1
        assert "Wayland Avenue" in prop1.address
        assert "E8 3RH" in str(prop1.postcode)
        assert prop1.latitude == 51.549
        assert prop1.longitude == -0.055

        # Check second property
        prop2 = next(p for p in properties if p.source_id == "15345678")
        assert prop2.price_pcm == 1950
        assert prop2.bedrooms == 2
        assert "Mare Street" in prop2.address

        # Check third property (studio)
        prop3 = next(p for p in properties if p.source_id == "15456789")
        assert prop3.price_pcm == 2100
        assert prop3.bedrooms == 0
        assert "Dalston Lane" in prop3.address

    def test_listing_to_property(
        self, onthemarket_scraper: OnTheMarketScraper, sample_listing: dict[str, Any]
    ) -> None:
        """Test conversion of listing dict to Property."""
        prop = onthemarket_scraper._listing_to_property(sample_listing)

        assert prop is not None
        assert prop.source == PropertySource.ONTHEMARKET
        assert prop.source_id == "15234567"
        assert prop.price_pcm == 2300
        assert prop.bedrooms == 1
        assert "Wayland Avenue" in prop.address
        assert prop.url.path == "/details/15234567/"
        assert prop.image_url is not None
        assert "image1.jpg" in str(prop.image_url)

    def test_listing_to_property_missing_id(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test listing without ID returns None."""
        listing = {"address": "Test Address", "short-price": "£1,000"}
        prop = onthemarket_scraper._listing_to_property(listing)
        assert prop is None

    def test_listing_to_property_extracts_id_from_url(
        self, onthemarket_scraper: OnTheMarketScraper
    ) -> None:
        """Test ID extraction from details-url when id field is missing."""
        listing = {
            "details-url": "/details/99999999/",
            "address": "Test Address",
            "short-price": "£1,000",
            "bedrooms": 1,
        }
        prop = onthemarket_scraper._listing_to_property(listing)
        assert prop is not None
        assert prop.source_id == "99999999"

    def test_extract_price(self) -> None:
        """Test price extraction from text."""
        assert extract_price("£2,300 pcm") == 2300
        assert extract_price("£1,950 pcm") == 1950
        assert extract_price("£500 pw") == 2166

    def test_extract_price_invalid(self) -> None:
        """Test price extraction with invalid text."""
        assert extract_price("POA") is None
        assert extract_price("") is None

    def test_extract_bedrooms(self) -> None:
        """Test bedroom extraction from title."""
        assert extract_bedrooms("1 bedroom flat to rent") == 1
        assert extract_bedrooms("2 bedroom apartment to rent") == 2
        assert extract_bedrooms("Studio to rent") == 0

    def test_extract_bedrooms_no_match(self) -> None:
        """Test bedroom extraction with no bedroom info."""
        assert extract_bedrooms("Flat to rent") is None

    def test_extract_postcode(self) -> None:
        """Test postcode extraction from address."""
        assert extract_postcode("Wayland Avenue, Hackney, London E8 3RH") == "E8 3RH"

    def test_parse_empty_results(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test parsing page with no results."""
        next_data = '{"props":{"initialReduxState":{"results":{"list":[]}}}}'
        html = f'<html><body><script id="__NEXT_DATA__">{next_data}</script></body></html>'
        properties = onthemarket_scraper._parse_next_data(html)
        assert len(properties) == 0

    def test_parse_no_next_data(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test parsing page without __NEXT_DATA__."""
        html = "<html><body><p>No data</p></body></html>"
        properties = onthemarket_scraper._parse_next_data(html)
        assert len(properties) == 0


class TestOnTheMarketEarlyStop:
    """Tests for early-stop pagination (requires newest-first sort)."""

    def test_search_url_sorts_by_newest(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Verify sort-field=update_date — required for early-stop correctness."""
        url = onthemarket_scraper._build_search_url(
            area="e8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=0,
            max_bedrooms=2,
        )
        assert "sort-field=update_date" in url

    @pytest.mark.asyncio
    async def test_stops_when_all_results_known(
        self, onthemarket_scraper: OnTheMarketScraper, sample_next_data: str
    ) -> None:
        """When all page-1 properties are already in DB, stop without fetching page 2."""
        known_ids = {"15234567", "15345678", "15456789"}

        mock_fetch = AsyncMock(return_value=sample_next_data)
        with patch.object(onthemarket_scraper, "_fetch_page", mock_fetch):
            result = await onthemarket_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=0,
                max_bedrooms=2,
                area="hackney",
                known_source_ids=known_ids,
            )

        assert mock_fetch.call_count == 1  # Only page 1 fetched
        assert result == []  # All known → nothing returned

    @pytest.mark.asyncio
    async def test_continues_when_some_results_new(
        self, onthemarket_scraper: OnTheMarketScraper, sample_next_data: str
    ) -> None:
        """When only some results are known, don't early-stop — fetch next page."""
        known_ids = {"15234567"}  # Only one known — should not early-stop

        empty_data = json.dumps({"props": {"initialReduxState": {"results": {"list": []}}}})
        empty_html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{empty_data}</script></body></html>"
        )

        mock_fetch = AsyncMock(side_effect=[sample_next_data, empty_html])
        with (
            patch.object(onthemarket_scraper, "_fetch_page", mock_fetch),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await onthemarket_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=0,
                max_bedrooms=2,
                area="hackney",
                known_source_ids=known_ids,
            )

        assert mock_fetch.call_count >= 2  # Continued past page 1
        assert len(result) == 3  # All page 1 properties returned
