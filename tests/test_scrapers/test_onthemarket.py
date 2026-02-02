"""Tests for OnTheMarket scraper."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_finder.models import PropertySource
from home_finder.scrapers.onthemarket import OnTheMarketScraper


@pytest.fixture
def onthemarket_scraper() -> OnTheMarketScraper:
    """Create an OnTheMarket scraper instance."""
    return OnTheMarketScraper()


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
            {"default": "https://media.onthemarket.com/image1.jpg", "webp": "https://media.onthemarket.com/image1.webp"},
            {"default": "https://media.onthemarket.com/image2.jpg", "webp": "https://media.onthemarket.com/image2.webp"},
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
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script></body></html>'


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

    async def test_scrape_uses_curl_cffi(self, onthemarket_scraper: OnTheMarketScraper, sample_next_data: str) -> None:
        """Test that scrape uses curl_cffi with Chrome impersonation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = sample_next_data

        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_response)
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
        mock_session.get.assert_called_once()
        call_kwargs = mock_session.get.call_args[1]
        assert call_kwargs["impersonate"] == "chrome"

        # Verify properties were parsed
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

    def test_listing_to_property_extracts_id_from_url(self, onthemarket_scraper: OnTheMarketScraper) -> None:
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

    def test_extract_price(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test price extraction from text."""
        assert onthemarket_scraper._extract_price("£2,300 pcm") == 2300
        assert onthemarket_scraper._extract_price("£1,950 pcm") == 1950
        assert onthemarket_scraper._extract_price("£500 pw") == 2166

    def test_extract_price_invalid(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test price extraction with invalid text."""
        assert onthemarket_scraper._extract_price("POA") is None
        assert onthemarket_scraper._extract_price("") is None

    def test_extract_bedrooms(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test bedroom extraction from title."""
        assert onthemarket_scraper._extract_bedrooms("1 bedroom flat to rent") == 1
        assert onthemarket_scraper._extract_bedrooms("2 bedroom apartment to rent") == 2
        assert onthemarket_scraper._extract_bedrooms("Studio to rent") == 0

    def test_extract_bedrooms_no_match(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test bedroom extraction with no bedroom info."""
        assert onthemarket_scraper._extract_bedrooms("Flat to rent") is None

    def test_extract_postcode(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test postcode extraction from address."""
        assert (
            onthemarket_scraper._extract_postcode("Wayland Avenue, Hackney, London E8 3RH")
            == "E8 3RH"
        )

    def test_parse_empty_results(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test parsing page with no results."""
        html = '<html><body><script id="__NEXT_DATA__">{"props":{"initialReduxState":{"results":{"list":[]}}}}</script></body></html>'
        properties = onthemarket_scraper._parse_next_data(html)
        assert len(properties) == 0

    def test_parse_no_next_data(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test parsing page without __NEXT_DATA__."""
        html = "<html><body><p>No data</p></body></html>"
        properties = onthemarket_scraper._parse_next_data(html)
        assert len(properties) == 0
