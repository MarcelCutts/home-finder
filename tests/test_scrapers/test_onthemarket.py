"""Tests for OnTheMarket scraper."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from home_finder.models import PropertySource
from home_finder.scrapers.onthemarket import OnTheMarketScraper


@pytest.fixture
def onthemarket_search_html(fixtures_path: Path) -> str:
    """Load OnTheMarket search results fixture."""
    return (fixtures_path / "onthemarket_search.html").read_text()


@pytest.fixture
def onthemarket_scraper() -> OnTheMarketScraper:
    """Create an OnTheMarket scraper instance."""
    return OnTheMarketScraper()


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


class TestOnTheMarketParser:
    """Tests for OnTheMarket HTML parsing."""

    def test_parse_search_results(
        self, onthemarket_scraper: OnTheMarketScraper, onthemarket_search_html: str
    ) -> None:
        """Test parsing of search results page."""
        soup = BeautifulSoup(onthemarket_search_html, "html.parser")
        properties = onthemarket_scraper._parse_search_results(
            soup, "https://www.onthemarket.com/to-rent/property/hackney/"
        )

        assert len(properties) == 3

        # Check first property
        prop1 = next(p for p in properties if p.source_id == "15234567")
        assert prop1.price_pcm == 2300
        assert prop1.bedrooms == 1
        assert "Wayland Avenue" in prop1.address
        assert "E8 3RH" in str(prop1.postcode)

        # Check second property
        prop2 = next(p for p in properties if p.source_id == "15345678")
        assert prop2.price_pcm == 1950
        assert prop2.bedrooms == 2
        assert "Mare Street" in prop2.address

        # Check third property
        prop3 = next(p for p in properties if p.source_id == "15456789")
        assert prop3.price_pcm == 2100
        assert prop3.bedrooms == 1
        assert "Dalston Lane" in prop3.address

    def test_extract_property_id(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test property ID extraction from URL."""
        url = "https://www.onthemarket.com/details/15234567/"
        prop_id = onthemarket_scraper._extract_property_id(url)
        assert prop_id == "15234567"

    def test_extract_property_id_from_data_attr(
        self, onthemarket_scraper: OnTheMarketScraper, onthemarket_search_html: str
    ) -> None:
        """Test property ID extraction from data attribute."""
        soup = BeautifulSoup(onthemarket_search_html, "html.parser")
        card = soup.find("li", class_="otm-PropertyCard")
        assert card is not None
        prop_id = card.get("data-property-id")
        assert prop_id == "15234567"

    def test_extract_property_id_no_match(self, onthemarket_scraper: OnTheMarketScraper) -> None:
        """Test property ID extraction with invalid URL."""
        url = "https://www.onthemarket.com/to-rent/property/hackney/"
        prop_id = onthemarket_scraper._extract_property_id(url)
        assert prop_id is None

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
        html = """
        <html>
        <body>
            <ul class="otm-PropertyCardList">
            </ul>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        properties = onthemarket_scraper._parse_search_results(soup, "https://onthemarket.com")
        assert len(properties) == 0
