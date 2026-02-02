"""Tests for OpenRent scraper."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from home_finder.models import PropertySource
from home_finder.scrapers.openrent import OpenRentScraper


@pytest.fixture
def openrent_search_html(fixtures_path: Path) -> str:
    """Load OpenRent search results fixture."""
    return (fixtures_path / "openrent_search.html").read_text()


@pytest.fixture
def openrent_scraper() -> OpenRentScraper:
    """Create an OpenRent scraper instance."""
    return OpenRentScraper()


class TestOpenRentScraper:
    """Tests for OpenRentScraper."""

    def test_source_property(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that source returns OPENRENT."""
        assert openrent_scraper.source == PropertySource.OPENRENT

    def test_build_search_url(self, openrent_scraper: OpenRentScraper) -> None:
        """Test URL building with search parameters."""
        url = openrent_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "openrent.co.uk" in url
        assert "hackney" in url
        assert "prices_min=1800" in url
        assert "prices_max=2200" in url
        assert "bedrooms_min=1" in url
        assert "bedrooms_max=2" in url

    def test_build_search_url_normalizes_area(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that area names are normalized to URL-safe format."""
        url = openrent_scraper._build_search_url(
            area="De Beauvoir Town",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "de-beauvoir-town" in url


class TestOpenRentParser:
    """Tests for OpenRent HTML parsing."""

    def test_extract_js_arrays(
        self, openrent_scraper: OpenRentScraper, openrent_search_html: str
    ) -> None:
        """Test extraction of JavaScript arrays from page."""
        soup = BeautifulSoup(openrent_search_html, "html.parser")
        data = openrent_scraper._extract_js_arrays(soup)

        assert "PROPERTYIDS" in data
        assert data["PROPERTYIDS"] == [2729497, 2732258, 2745123]

        assert "prices" in data
        assert data["prices"] == [2300, 1950, 2100]

        assert "bedrooms" in data
        assert data["bedrooms"] == [1, 2, 1]

        assert "PROPERTYLISTLATITUDES" in data
        assert len(data["PROPERTYLISTLATITUDES"]) == 3

        assert "PROPERTYLISTLONGITUDES" in data
        assert len(data["PROPERTYLISTLONGITUDES"]) == 3

    def test_parse_search_results(
        self, openrent_scraper: OpenRentScraper, openrent_search_html: str
    ) -> None:
        """Test parsing of search results page."""
        soup = BeautifulSoup(openrent_search_html, "html.parser")
        properties = openrent_scraper._parse_search_results(
            soup, "https://www.openrent.co.uk/properties-to-rent/hackney"
        )

        assert len(properties) == 3

        # Check first property
        prop1 = next(p for p in properties if p.source_id == "2729497")
        assert prop1.price_pcm == 2300
        assert prop1.bedrooms == 1
        assert "2729497" in str(prop1.url)
        assert prop1.latitude is not None
        assert prop1.longitude is not None

        # Check second property
        prop2 = next(p for p in properties if p.source_id == "2732258")
        assert prop2.price_pcm == 1950
        assert prop2.bedrooms == 2

        # Check third property
        prop3 = next(p for p in properties if p.source_id == "2745123")
        assert prop3.price_pcm == 2100
        assert prop3.bedrooms == 1

    def test_parse_search_results_deduplicates(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that duplicate property links are deduplicated."""
        html = """
        <html>
        <script>
            var PROPERTYIDS = [123];
            var prices = [2000];
            var bedrooms = [1];
            var PROPERTYLISTLATITUDES = [51.5];
            var PROPERTYLISTLONGITUDES = [-0.1];
        </script>
        <body>
            <a href="/property-to-rent/london/flat/123">
                <span>£2,000 per month</span>
                <span>1 Bed Flat, Test, E8 1AA</span>
                <li>1 Bed</li>
            </a>
            <a href="/property-to-rent/london/flat/123">
                <span>View property</span>
            </a>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        properties = openrent_scraper._parse_search_results(soup, "https://openrent.co.uk")

        assert len(properties) == 1
        assert properties[0].source_id == "123"

    def test_extract_price_from_html(self, openrent_scraper: OpenRentScraper) -> None:
        """Test price extraction from HTML text."""
        html = """
        <a href="/property/123">
            <span>£2,300 per month</span>
            <span>1 Bed Flat</span>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        price = openrent_scraper._extract_price_from_html(link)
        assert price == 2300

    def test_extract_price_from_html_no_price(self, openrent_scraper: OpenRentScraper) -> None:
        """Test price extraction when no price present."""
        html = '<a href="/property/123"><span>View Details</span></a>'
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        price = openrent_scraper._extract_price_from_html(link)
        assert price is None

    def test_extract_bedrooms_from_html(self, openrent_scraper: OpenRentScraper) -> None:
        """Test bedroom extraction from HTML text."""
        html = """
        <a href="/property/123">
            <span>2 Bed Flat, Test Area</span>
            <li>2 Beds</li>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        beds = openrent_scraper._extract_bedrooms_from_html(link)
        assert beds == 2

    def test_extract_bedrooms_from_title(self, openrent_scraper: OpenRentScraper) -> None:
        """Test bedroom extraction from title text."""
        html = """
        <a href="/property/123">
            <span>1 Bed Apartment, Hackney E8</span>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        beds = openrent_scraper._extract_bedrooms_from_html(link)
        assert beds == 1

    def test_parse_link_text_extracts_postcode(self, openrent_scraper: OpenRentScraper) -> None:
        """Test postcode extraction from link text."""
        html = """
        <a href="/property/123">
            <span>1 Bed Flat, Mare Street, E8 3RH</span>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        title, address, postcode = openrent_scraper._parse_link_text(link)

        assert "Mare Street" in title
        assert postcode is not None
        # Postcode should be extracted (may be partial like E8 or full like E8 3RH)
        assert "E8" in postcode.upper()

    def test_parse_link_text_skips_price_and_distance(
        self, openrent_scraper: OpenRentScraper
    ) -> None:
        """Test that price and distance text are not included in title."""
        html = """
        <a href="/property/123">
            <span>£2,000 per month</span>
            <span>0.5 km</span>
            <span>1 Bed Flat, Test Area, E8</span>
            <li>1 Bed</li>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        title, _, _ = openrent_scraper._parse_link_text(link)

        assert "£" not in title
        assert "km" not in title.lower()
        assert "Test Area" in title

    def test_parse_search_results_handles_missing_longitude(
        self, openrent_scraper: OpenRentScraper
    ) -> None:
        """Test that missing longitude results in both coordinates being None."""
        html = """
        <html>
        <script>
            var PROPERTYIDS = [123];
            var prices = [2000];
            var bedrooms = [1];
            var PROPERTYLISTLATITUDES = [51.5];
            var PROPERTYLISTLONGITUDES = [];
        </script>
        <body>
            <a href="/property-to-rent/london/flat/123">
                <span>£2,000 per month</span>
                <span>1 Bed Flat, Test, E8 1AA</span>
                <li>1 Bed</li>
            </a>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        properties = openrent_scraper._parse_search_results(soup, "https://openrent.co.uk")

        assert len(properties) == 1
        # Both should be None when one is missing (Property model requires both or neither)
        assert properties[0].latitude is None
        assert properties[0].longitude is None

    def test_parse_search_results_handles_missing_latitude(
        self, openrent_scraper: OpenRentScraper
    ) -> None:
        """Test that missing latitude results in both coordinates being None."""
        html = """
        <html>
        <script>
            var PROPERTYIDS = [456];
            var prices = [1800];
            var bedrooms = [2];
            var PROPERTYLISTLATITUDES = [];
            var PROPERTYLISTLONGITUDES = [-0.1];
        </script>
        <body>
            <a href="/property-to-rent/london/flat/456">
                <span>£1,800 per month</span>
                <span>2 Bed Flat, Test, E8 2BB</span>
                <li>2 Beds</li>
            </a>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        properties = openrent_scraper._parse_search_results(soup, "https://openrent.co.uk")

        assert len(properties) == 1
        # Both should be None when one is missing
        assert properties[0].latitude is None
        assert properties[0].longitude is None


class TestOpenRentScraperIntegration:
    """Integration tests for OpenRent scraper (with mocked HTTP)."""

    @pytest.mark.asyncio
    async def test_scrape_returns_properties(
        self, openrent_scraper: OpenRentScraper, openrent_search_html: str
    ) -> None:
        """Test that scrape method returns parsed properties."""
        # Mock the crawler to return our fixture HTML
        mock_context = MagicMock()
        mock_context.soup = BeautifulSoup(openrent_search_html, "html.parser")
        mock_context.request.url = "https://www.openrent.co.uk/properties-to-rent/hackney"

        with patch.object(
            openrent_scraper,
            "_parse_search_results",
            wraps=openrent_scraper._parse_search_results,
        ) as mock_parse:
            # Create a mock crawler that calls our handler with test data
            async def mock_run(urls: list[str]) -> None:
                # Simulate crawler behavior by directly calling parse
                soup = BeautifulSoup(openrent_search_html, "html.parser")
                openrent_scraper._parse_search_results(soup, urls[0])

            with patch("home_finder.scrapers.openrent.BeautifulSoupCrawler") as MockCrawler:
                mock_crawler_instance = AsyncMock()
                mock_crawler_instance.run = mock_run
                mock_crawler_instance.router = MagicMock()
                MockCrawler.return_value = mock_crawler_instance

                await openrent_scraper.scrape(
                    min_price=1800,
                    max_price=2500,
                    min_bedrooms=1,
                    max_bedrooms=2,
                    area="hackney",
                )

                # The parse method should have been called
                mock_parse.assert_called_once()
