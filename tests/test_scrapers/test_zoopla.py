"""Tests for Zoopla scraper."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from bs4 import BeautifulSoup

from home_finder.models import PropertySource
from home_finder.scrapers.parsing import extract_bedrooms, extract_postcode, extract_price
from home_finder.scrapers.zoopla import ZooplaListing, ZooplaScraper


@pytest.fixture
def zoopla_search_html(fixtures_path: Path) -> str:
    """Load Zoopla search results fixture."""
    return (fixtures_path / "zoopla_search.html").read_text()


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
        # Zoopla borough format uses "hackney-london-borough"
        assert "hackney-london-borough" in url
        # Must include q= parameter to prevent redirects
        assert "q=Hackney" in url

    def test_build_search_url_outcode(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL building with outcode area."""
        url = zoopla_scraper._build_search_url(
            area="E8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "/e8/" in url
        assert "q=E8" in url

    def test_build_search_url_non_london_area(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL building with area not in London boroughs list."""
        url = zoopla_scraper._build_search_url(
            area="manchester",
            min_price=1000,
            max_price=1500,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Should use the area as-is (not append -london-borough)
        assert "/manchester/" in url
        assert "hackney-london-borough" not in url

    def test_build_search_url_has_search_source(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL includes search_source parameter."""
        url = zoopla_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "search_source=to-rent" in url

    def test_build_search_url_pagination(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test URL building with page number."""
        url = zoopla_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            page=3,
        )
        assert "pn=3" in url

    def test_build_search_url_page_1_no_pn(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test that page 1 does not include pn parameter."""
        url = zoopla_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            page=1,
        )
        assert "pn=" not in url


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

    def test_extract_price(self) -> None:
        """Test price extraction from text."""
        assert extract_price("£2,300 pcm") == 2300
        assert extract_price("£1,950 pcm") == 1950
        assert extract_price("£500 pw") == 2166  # Weekly to monthly

    def test_extract_price_invalid(self) -> None:
        """Test price extraction with invalid text."""
        assert extract_price("POA") is None
        assert extract_price("") is None

    def test_extract_bedrooms(self) -> None:
        """Test bedroom extraction from title."""
        assert extract_bedrooms("1 bed flat to rent") == 1
        assert extract_bedrooms("2 bed apartment to rent") == 2
        assert extract_bedrooms("Studio to rent") == 0
        assert extract_bedrooms("3 bedroom house") == 3

    def test_extract_bedrooms_no_match(self) -> None:
        """Test bedroom extraction with no bedroom info."""
        assert extract_bedrooms("Flat to rent") is None

    def test_extract_postcode(self) -> None:
        """Test postcode extraction from address."""
        assert extract_postcode("Wayland Avenue, Hackney, London E8 3RH") == "E8 3RH"
        assert extract_postcode("Some Street, N1") == "N1"

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


class TestZooplaRscExtraction:
    """Tests for Zoopla RSC payload extraction."""

    def test_extract_rsc_listings_with_regular_listings(
        self, zoopla_scraper: ZooplaScraper
    ) -> None:
        """Test extraction from RSC payload containing regularListingsFormatted."""
        # Build a realistic RSC payload
        listings_data = [
            {
                "listingId": 123,
                "price": "£1850 pcm",
                "address": "Test Street",
                "title": "1 bed flat",
                "listingUris": {"detail": "/to-rent/details/123/"},
                "features": [{"iconId": "bed", "content": 1}],
            }
        ]
        rsc_json = json.dumps({"regularListingsFormatted": listings_data})
        # Wrap in RSC push format: self.__next_f.push([1, "79:{json}"])
        inner_str = f"79:{rsc_json}"
        # The push array contains [1, "79:{json}"]
        push_content = f"1,{json.dumps(inner_str)}"
        html = f"<script>self.__next_f.push([{push_content}])</script>"

        listings = zoopla_scraper._extract_rsc_listings(html)
        assert len(listings) == 1
        assert listings[0].listing_id == 123

    def test_extract_rsc_listings_with_listing_id(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction from RSC payload containing individual listings."""
        listing_data = {
            "listingId": 456,
            "price": "£2000 pcm",
            "address": "Another Street",
            "title": "2 bed flat",
            "listingUris": {"detail": "/to-rent/details/456/"},
            "features": [{"iconId": "bed", "content": 2}],
        }
        rsc_json = json.dumps(listing_data)
        inner_str = f"80:{rsc_json}"
        push_content = f"1,{json.dumps(inner_str)}"
        html = f"<script>self.__next_f.push([{push_content}])</script>"

        listings = zoopla_scraper._extract_rsc_listings(html)
        assert len(listings) == 1
        assert listings[0].listing_id == 456

    def test_extract_rsc_listings_empty_html(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction from HTML with no RSC data."""
        html = "<html><head></head><body></body></html>"
        listings = zoopla_scraper._extract_rsc_listings(html)
        assert listings == []

    def test_extract_rsc_listings_no_listing_data(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test extraction from RSC payload with no listing data."""
        html = """<script>self.__next_f.push([1,"some other content"])</script>"""
        listings = zoopla_scraper._extract_rsc_listings(html)
        assert listings == []

    def test_extract_rsc_listings_deduplicates(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test that duplicate listings are removed."""
        listing_data = {
            "listingId": 789,
            "price": "£1500 pcm",
            "address": "Dup Street",
            "title": "1 bed flat",
            "listingUris": {"detail": "/to-rent/details/789/"},
            "features": [{"iconId": "bed", "content": 1}],
        }
        rsc_json = json.dumps(listing_data)
        inner_str = f"80:{rsc_json}"
        push_content = f"1,{json.dumps(inner_str)}"
        # Same listing appears twice
        html = (
            f"<script>self.__next_f.push([{push_content}])</script>"
            f"<script>self.__next_f.push([{push_content}])</script>"
        )

        listings = zoopla_scraper._extract_rsc_listings(html)
        assert len(listings) == 1

    def test_parse_rsc_properties(self, zoopla_scraper: ZooplaScraper) -> None:
        """Test full RSC parsing pipeline to Property objects."""
        listings_data = [
            {
                "listingId": 67123456,
                "price": "£1,850 pcm",
                "priceUnformatted": 1850,
                "address": "Victoria Park Road, Hackney, London E9 5NA",
                "title": "1 bed flat to rent",
                "listingUris": {"detail": "/to-rent/details/67123456/"},
                "features": [{"iconId": "bed", "content": 1}],
                "image": {"src": "//lid.zoocdn.com/u/354/255/abc123.jpg"},
                "pos": {"lat": 51.5465, "lng": -0.0553},
            }
        ]
        rsc_json = json.dumps({"regularListingsFormatted": listings_data})
        inner_str = f"79:{rsc_json}"
        push_content = f"1,{json.dumps(inner_str)}"
        html = f"<script>self.__next_f.push([{push_content}])</script>"

        properties = zoopla_scraper._parse_rsc_properties(html)
        assert len(properties) == 1

        prop = properties[0]
        assert prop.source_id == "67123456"
        assert prop.price_pcm == 1850
        assert prop.bedrooms == 1
        assert "Victoria Park Road" in prop.address
        assert prop.postcode == "E9 5NA"
        assert prop.latitude == 51.5465
        assert prop.longitude == -0.0553

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


class TestZooplaEarlyStop:
    """Tests for early-stop pagination (requires newest-first sort)."""

    def test_search_url_sorts_by_newest(self, zoopla_scraper: ZooplaScraper) -> None:
        """Verify results_sort=newest_listings — required for early-stop correctness."""
        url = zoopla_scraper._build_search_url(
            area="e8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=0,
            max_bedrooms=2,
        )
        assert "results_sort=newest_listings" in url

    @pytest.mark.asyncio
    async def test_stops_when_all_results_known(
        self, zoopla_scraper: ZooplaScraper
    ) -> None:
        """When all page-1 properties are already in DB, stop without fetching page 2."""
        listings_data = [
            {
                "listingId": 100,
                "price": "£1,850 pcm",
                "priceUnformatted": 1850,
                "address": "Test Street, London E8 1AA",
                "title": "1 bed flat",
                "listingUris": {"detail": "/to-rent/details/100/"},
                "features": [{"iconId": "bed", "content": 1}],
            },
            {
                "listingId": 200,
                "price": "£2,000 pcm",
                "priceUnformatted": 2000,
                "address": "Another Street, London E8 2BB",
                "title": "2 bed flat",
                "listingUris": {"detail": "/to-rent/details/200/"},
                "features": [{"iconId": "bed", "content": 2}],
            },
        ]
        rsc_json = json.dumps({"regularListingsFormatted": listings_data})
        inner_str = f"79:{rsc_json}"
        push_content = f"1,{json.dumps(inner_str)}"
        page1_html = f"<script>self.__next_f.push([{push_content}])</script>"

        known_ids = {"100", "200"}

        mock_fetch = AsyncMock(return_value=page1_html)
        with patch.object(zoopla_scraper, "_fetch_page", mock_fetch):
            result = await zoopla_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="e8",
                known_source_ids=known_ids,
            )

        assert mock_fetch.call_count == 1  # Only page 1 fetched
        assert result == []  # All known → nothing returned

    @pytest.mark.asyncio
    async def test_continues_when_some_results_new(
        self, zoopla_scraper: ZooplaScraper
    ) -> None:
        """When only some results are known, don't early-stop — fetch next page."""
        listings_data = [
            {
                "listingId": 100,
                "price": "£1,850 pcm",
                "priceUnformatted": 1850,
                "address": "Test Street, London E8 1AA",
                "title": "1 bed flat",
                "listingUris": {"detail": "/to-rent/details/100/"},
                "features": [{"iconId": "bed", "content": 1}],
            },
            {
                "listingId": 200,
                "price": "£2,000 pcm",
                "priceUnformatted": 2000,
                "address": "Another Street, London E8 2BB",
                "title": "2 bed flat",
                "listingUris": {"detail": "/to-rent/details/200/"},
                "features": [{"iconId": "bed", "content": 2}],
            },
        ]
        rsc_json = json.dumps({"regularListingsFormatted": listings_data})
        inner_str = f"79:{rsc_json}"
        push_content = f"1,{json.dumps(inner_str)}"
        page1_html = f"<script>self.__next_f.push([{push_content}])</script>"

        known_ids = {"100"}  # Only one known — should not early-stop

        mock_fetch = AsyncMock(side_effect=[page1_html, "<html></html>"])
        with (
            patch.object(zoopla_scraper, "_fetch_page", mock_fetch),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await zoopla_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="e8",
                known_source_ids=known_ids,
            )

        assert mock_fetch.call_count >= 2  # Continued past page 1
        assert len(result) == 2  # Both page 1 properties returned
