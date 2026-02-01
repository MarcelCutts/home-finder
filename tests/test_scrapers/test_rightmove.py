"""Tests for Rightmove scraper."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from home_finder.models import PropertySource
from home_finder.scrapers.rightmove import RightmoveScraper


@pytest.fixture
def rightmove_search_html(fixtures_path: Path) -> str:
    """Load Rightmove search results fixture."""
    return (fixtures_path / "rightmove_search.html").read_text()


@pytest.fixture
def rightmove_scraper() -> RightmoveScraper:
    """Create a Rightmove scraper instance."""
    return RightmoveScraper()


class TestRightmoveScraper:
    """Tests for RightmoveScraper."""

    def test_source_property(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test that source returns RIGHTMOVE."""
        assert rightmove_scraper.source == PropertySource.RIGHTMOVE

    def test_build_search_url(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test URL building with search parameters."""
        url = rightmove_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "rightmove.co.uk" in url
        assert "minPrice=1800" in url
        assert "maxPrice=2200" in url
        assert "minBedrooms=1" in url
        assert "maxBedrooms=2" in url
        # Should use property-to-rent endpoint
        assert "property-to-rent" in url

    def test_build_search_url_with_location_identifier(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test URL building includes location identifier."""
        url = rightmove_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Rightmove uses REGION% encoded identifiers
        assert "locationIdentifier=" in url


class TestRightmoveParser:
    """Tests for Rightmove HTML parsing."""

    def test_parse_search_results(
        self, rightmove_scraper: RightmoveScraper, rightmove_search_html: str
    ) -> None:
        """Test parsing of search results page."""
        soup = BeautifulSoup(rightmove_search_html, "html.parser")
        properties = rightmove_scraper._parse_search_results(
            soup, "https://www.rightmove.co.uk/property-to-rent/find.html"
        )

        assert len(properties) == 3

        # Check first property
        prop1 = next(p for p in properties if p.source_id == "128459731")
        assert prop1.price_pcm == 2300
        assert prop1.bedrooms == 1
        assert "Wayland Avenue" in prop1.address
        assert "128459731" in str(prop1.url)

        # Check second property
        prop2 = next(p for p in properties if p.source_id == "128512847")
        assert prop2.price_pcm == 1950
        assert prop2.bedrooms == 2
        assert "Mare Street" in prop2.address

        # Check third property
        prop3 = next(p for p in properties if p.source_id == "128623958")
        assert prop3.price_pcm == 2100
        assert prop3.bedrooms == 1
        assert "Dalston Lane" in prop3.address

    def test_extract_property_id(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test property ID extraction from URL."""
        url = "/properties/128459731#/?channel=RES_LET"
        prop_id = rightmove_scraper._extract_property_id(url)
        assert prop_id == "128459731"

    def test_extract_property_id_no_match(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test property ID extraction with invalid URL."""
        url = "/search/results.html"
        prop_id = rightmove_scraper._extract_property_id(url)
        assert prop_id is None

    def test_extract_price(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test price extraction from text."""
        assert rightmove_scraper._extract_price("£2,300 pcm") == 2300
        assert rightmove_scraper._extract_price("£1,950 pcm") == 1950
        assert rightmove_scraper._extract_price("£2,100 pcm") == 2100
        assert rightmove_scraper._extract_price("£500 pw") == 2166  # Weekly to monthly (500*52/12)

    def test_extract_price_invalid(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test price extraction with invalid text."""
        assert rightmove_scraper._extract_price("Contact agent") is None
        assert rightmove_scraper._extract_price("") is None
        assert rightmove_scraper._extract_price("POA") is None

    def test_extract_bedrooms(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test bedroom extraction from title."""
        assert rightmove_scraper._extract_bedrooms("1 bedroom flat to rent") == 1
        assert rightmove_scraper._extract_bedrooms("2 bedroom apartment to rent") == 2
        assert rightmove_scraper._extract_bedrooms("Studio to rent") == 0
        assert rightmove_scraper._extract_bedrooms("3 bed house") == 3

    def test_extract_bedrooms_no_match(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test bedroom extraction with no bedroom info."""
        assert rightmove_scraper._extract_bedrooms("Flat to rent") is None
        assert rightmove_scraper._extract_bedrooms("") is None

    def test_extract_postcode(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test postcode extraction from address."""
        assert rightmove_scraper._extract_postcode("Wayland Avenue, London E8") == "E8"
        assert (
            rightmove_scraper._extract_postcode("Mare Street, Hackney, London E8 3RH")
            == "E8 3RH"
        )
        assert rightmove_scraper._extract_postcode("Islington N1 2AA") == "N1 2AA"

    def test_extract_postcode_no_match(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test postcode extraction with no postcode."""
        assert rightmove_scraper._extract_postcode("Some Address, London") is None
        assert rightmove_scraper._extract_postcode("") is None

    def test_parse_empty_results(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test parsing page with no results."""
        html = """
        <html>
        <body>
            <div class="l-searchResults">
                <div class="no-results">No properties found</div>
            </div>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        properties = rightmove_scraper._parse_search_results(
            soup, "https://rightmove.co.uk"
        )
        assert len(properties) == 0
