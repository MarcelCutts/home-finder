"""Tests for Zoopla scraper."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from home_finder.models import PropertySource
from home_finder.scrapers.zoopla import ZooplaScraper
from home_finder.scrapers.zoopla_models import ZooplaListing, ZooplaNextData


@pytest.fixture
def zoopla_search_html(fixtures_path: Path) -> str:
    """Load Zoopla search results fixture."""
    return (fixtures_path / "zoopla_search.html").read_text()


@pytest.fixture
def zoopla_nextdata(fixtures_path: Path) -> ZooplaNextData:
    """Load and parse Zoopla __NEXT_DATA__ JSON fixture."""
    json_content = (fixtures_path / "zoopla_nextdata.json").read_text()
    return ZooplaNextData.model_validate_json(json_content)


@pytest.fixture
def zoopla_scraper() -> ZooplaScraper:
    """Create a Zoopla scraper instance."""
    return ZooplaScraper()


class TestZooplaScraper:
    """Tests for ZooplaScraper."""

    def test_source_property(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test that source returns ZOOPLA."""
        assert zoopla_scraper.source == PropertySource.ZOOPLA

    def test_build_search_url(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL building with search parameters."""
        url = zoopla_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "zoopla.co.uk" in url
        assert "price_min=1800" in url
        assert "price_max=2200" in url
        assert "beds_min=1" in url
        assert "beds_max=2" in url
        assert "to-rent" in url

    def test_build_search_url_with_area(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL building includes area."""
        url = zoopla_scraper._build_search_url(
            area="islington",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "islington" in url.lower()

    def test_build_search_url_london_borough(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL building uses correct London borough slug."""
        url = zoopla_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Zoopla requires "hackney-london" not just "hackney"
        assert "hackney-london" in url

    def test_build_search_url_non_london_area(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL building with area not in London boroughs list."""
        url = zoopla_scraper._build_search_url(
            area="manchester",
            min_price=1000,
            max_price=1500,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Should use the area as-is (not append -london)
        assert "/manchester/" in url
        assert "manchester-london" not in url


class TestZooplaHtmlParser:
    """Tests for Zoopla HTML parsing (fallback)."""

    def test_parse_search_results(
        self, zoopla_scraper: ZooplaScraper, zoopla_search_html: str
    ) -> None:
        """Test parsing of search results page."""
        soup = BeautifulSoup(zoopla_search_html, "html.parser")
        properties = zoopla_scraper._parse_search_results(
            soup, "https://www.zoopla.co.uk/to-rent/property/hackney/"
        )

        assert len(properties) == 3

        # Check first property
        prop1 = next(p for p in properties if p.source_id == "66543210")
        assert prop1.price_pcm == 2300
        assert prop1.bedrooms == 1
        assert "Wayland Avenue" in prop1.address
        assert "E8 3RH" in str(prop1.postcode)

        # Check second property
        prop2 = next(p for p in properties if p.source_id == "66789012")
        assert prop2.price_pcm == 1950
        assert prop2.bedrooms == 2
        assert "Mare Street" in prop2.address

        # Check third property
        prop3 = next(p for p in properties if p.source_id == "66901234")
        assert prop3.price_pcm == 2100
        assert prop3.bedrooms == 1
        assert "Dalston Lane" in prop3.address

    def test_extract_property_id(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test property ID extraction from URL."""
        url = "https://www.zoopla.co.uk/to-rent/details/66543210/"
        prop_id = zoopla_scraper._extract_property_id(url)
        assert prop_id == "66543210"

    def test_extract_property_id_no_match(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test property ID extraction with invalid URL."""
        url = "https://www.zoopla.co.uk/to-rent/property/hackney/"
        prop_id = zoopla_scraper._extract_property_id(url)
        assert prop_id is None

    def test_extract_price(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test price extraction from text."""
        assert zoopla_scraper._extract_price("£2,300 pcm") == 2300
        assert zoopla_scraper._extract_price("£1,950 pcm") == 1950
        assert zoopla_scraper._extract_price("£500 pw") == 2166  # Weekly to monthly

    def test_extract_price_invalid(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test price extraction with invalid text."""
        assert zoopla_scraper._extract_price("POA") is None
        assert zoopla_scraper._extract_price("") is None

    def test_extract_bedrooms(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test bedroom extraction from title."""
        assert zoopla_scraper._extract_bedrooms("1 bed flat to rent") == 1
        assert zoopla_scraper._extract_bedrooms("2 bed apartment to rent") == 2
        assert zoopla_scraper._extract_bedrooms("Studio to rent") == 0
        assert zoopla_scraper._extract_bedrooms("3 bedroom house") == 3

    def test_extract_bedrooms_no_match(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test bedroom extraction with no bedroom info."""
        assert zoopla_scraper._extract_bedrooms("Flat to rent") is None

    def test_extract_postcode(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test postcode extraction from address."""
        assert (
            zoopla_scraper._extract_postcode("Wayland Avenue, Hackney, London E8 3RH") == "E8 3RH"
        )
        assert zoopla_scraper._extract_postcode("Some Street, N1") == "N1"

    def test_parse_empty_results(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test parsing page with no results."""
        html = """
        <html>
        <body>
            <div class="css-1anhqz4-ListingsContainer">
                <p>No properties found</p>
            </div>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        properties = zoopla_scraper._parse_search_results(soup, "https://zoopla.co.uk")
        assert len(properties) == 0


class TestZooplaJsonExtraction:
    """Tests for Zoopla __NEXT_DATA__ JSON extraction."""

    def test_extract_next_data(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction of __NEXT_DATA__ script content."""
        html = """
        <html>
        <head>
            <script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"regularListingsFormatted":[]}}}</script>
        </head>
        <body></body>
        </html>
        """
        data = zoopla_scraper._extract_next_data(html)
        assert data is not None
        assert data.get_listings() == []

    def test_extract_next_data_missing(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction when __NEXT_DATA__ script is missing."""
        html = "<html><head></head><body></body></html>"
        data = zoopla_scraper._extract_next_data(html)
        assert data is None

    def test_extract_next_data_invalid_json(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction with invalid JSON content."""
        html = """
        <html>
        <head>
            <script id="__NEXT_DATA__" type="application/json">not valid json</script>
        </head>
        <body></body>
        </html>
        """
        data = zoopla_scraper._extract_next_data(html)
        assert data is None

    def test_extract_rsc_listings(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction from React Server Components format."""
        # Simulated RSC script content with escaped quotes
        rsc_content = """self.__next_f.push([1,"\\"regularListingsFormatted\\":[{\\"listingId\\":123,\\"price\\":\\"£1850 pcm\\",\\"address\\":\\"Test Street\\",\\"title\\":\\"1 bed flat\\",\\"listingUris\\":{\\"detail\\":\\"/to-rent/details/123/\\"},\\"features\\":[{\\"iconId\\":\\"bed\\",\\"content\\":1}]}],\\"extendedListingsFormatted\\":[]"])"""
        listings = zoopla_scraper._extract_rsc_listings(rsc_content)
        assert listings is not None
        assert len(listings) == 1
        assert listings[0].listing_id == 123

    def test_extract_rsc_listings_empty_array(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction when RSC has empty listings."""
        rsc_content = """self.__next_f.push([1,"\\"regularListingsFormatted\\":[],\\"extendedListingsFormatted\\":[]"])"""
        listings = zoopla_scraper._extract_rsc_listings(rsc_content)
        assert listings is not None
        assert len(listings) == 0

    def test_extract_rsc_listings_no_markers(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction when RSC markers are missing."""
        rsc_content = """self.__next_f.push([1,"some other content"])"""
        listings = zoopla_scraper._extract_rsc_listings(rsc_content)
        assert listings is None

    def test_parse_next_data_properties(
        self, zoopla_scraper: ZooplaScraper, zoopla_nextdata: ZooplaNextData
    ) -> None:
        """Test parsing properties from __NEXT_DATA__ JSON."""
        properties = zoopla_scraper._parse_next_data_properties(zoopla_nextdata)

        assert len(properties) == 4

        # Check first property (1 bed, pcm price, with coordinates)
        prop1 = next(p for p in properties if p.source_id == "67123456")
        assert prop1.price_pcm == 1850
        assert prop1.bedrooms == 1
        assert "Victoria Park Road" in prop1.address
        assert prop1.postcode == "E9 5NA"
        assert "zoopla.co.uk/to-rent/details/67123456" in str(prop1.url)
        assert prop1.image_url is not None
        assert "lid.zoocdn.com" in str(prop1.image_url)
        assert prop1.latitude == 51.5465
        assert prop1.longitude == -0.0553

        # Check second property (2 bed)
        prop2 = next(p for p in properties if p.source_id == "67234567")
        assert prop2.price_pcm == 2100
        assert prop2.bedrooms == 2
        assert "Mare Street" in prop2.address
        assert prop2.latitude == 51.5482

        # Check third property (studio)
        prop3 = next(p for p in properties if p.source_id == "67345678")
        assert prop3.price_pcm == 1600
        assert prop3.bedrooms == 0
        assert "Dalston Lane" in prop3.address

        # Check fourth property (weekly price, no image/coords)
        prop4 = next(p for p in properties if p.source_id == "67456789")
        assert prop4.price_pcm == 1950  # £450 pw * 52 / 12
        assert prop4.bedrooms == 1
        assert prop4.image_url is None
        assert prop4.latitude is None
        assert prop4.longitude is None

    def test_parse_next_data_empty_listings(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test parsing when regularListingsFormatted is empty."""
        data = ZooplaNextData.model_validate(
            {"props": {"pageProps": {"regularListingsFormatted": []}}}
        )
        properties = zoopla_scraper._parse_next_data_properties(data)
        assert len(properties) == 0

    def test_parse_next_data_missing_structure(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test parsing when expected JSON structure is missing."""
        data = ZooplaNextData.model_validate({"props": {}})
        properties = zoopla_scraper._parse_next_data_properties(data)
        assert len(properties) == 0

    def test_listing_to_property_missing_url(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test converting listing without URL returns None."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 123,
                "price": "£1,850 pcm",
                "features": [{"iconId": "bed", "content": 1}],
            }
        )
        prop = zoopla_scraper._listing_to_property(listing)
        assert prop is None

    def test_listing_to_property_missing_price(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test converting listing without price returns None."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 123,
                "detailUrl": "/to-rent/details/123/",
                "features": [{"iconId": "bed", "content": 1}],
            }
        )
        prop = zoopla_scraper._listing_to_property(listing)
        assert prop is None

    def test_listing_to_property_missing_bedrooms(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test converting listing without bedrooms returns None."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 123,
                "detailUrl": "/to-rent/details/123/",
                "price": "£1,850 pcm",
                "title": "Flat to rent",  # No bedroom info
                "features": [],
            }
        )
        prop = zoopla_scraper._listing_to_property(listing)
        assert prop is None

    def test_listing_to_property_bedrooms_from_title(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test converting listing that gets bedrooms from title."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 123,
                "detailUrl": "/to-rent/details/123/",
                "price": "£1,850 pcm",
                "title": "2 bedroom flat",
                "features": [],
            }
        )
        prop = zoopla_scraper._listing_to_property(listing)
        assert prop is not None
        assert prop.bedrooms == 2
