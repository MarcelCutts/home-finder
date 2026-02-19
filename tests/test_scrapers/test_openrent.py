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
        assert "within=2" in url

    def test_build_search_url_overrides_e10(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that E10 outcode is replaced with 'leyton' slug to fix geocoding."""
        url = openrent_scraper._build_search_url(
            area="e10",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "/leyton?" in url
        assert "/e10" not in url

    def test_build_search_url_no_override_for_normal_areas(
        self, openrent_scraper: OpenRentScraper
    ) -> None:
        """Test that non-overridden areas pass through unchanged."""
        url = openrent_scraper._build_search_url(
            area="e5",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "/e5?" in url

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
        assert prop1.image_url is not None
        assert "imagescdn.openrent.co.uk" in str(prop1.image_url)

        # Check second property
        prop2 = next(p for p in properties if p.source_id == "2732258")
        assert prop2.price_pcm == 1950
        assert prop2.bedrooms == 2
        assert prop2.image_url is not None

        # Check third property
        prop3 = next(p for p in properties if p.source_id == "2745123")
        assert prop3.price_pcm == 2100
        assert prop3.bedrooms == 1
        assert prop3.image_url is not None

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
        assert link is not None
        price = openrent_scraper._extract_price_from_html(link)
        assert price == 2300

    def test_extract_price_from_html_no_price(self, openrent_scraper: OpenRentScraper) -> None:
        """Test price extraction when no price present."""
        html = '<a href="/property/123"><span>View Details</span></a>'
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        assert link is not None
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
        assert link is not None
        beds = openrent_scraper._extract_bedrooms_from_html(link)
        assert beds == 2

    def test_extract_bedrooms_studio_text(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that 'Studio' text is parsed as 0 bedrooms."""
        html = """
        <a href="/property/123">
            <span>Studio</span>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        assert link is not None
        beds = openrent_scraper._extract_bedrooms_from_html(link)
        assert beds == 0

    def test_extract_bedrooms_studio_flat_title(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that 'Studio Flat, E2' title is parsed as 0 bedrooms."""
        html = """
        <a href="/property/123">
            <span>Studio Flat, E2</span>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        assert link is not None
        beds = openrent_scraper._extract_bedrooms_from_html(link)
        assert beds == 0

    def test_extract_bedrooms_from_title(self, openrent_scraper: OpenRentScraper) -> None:
        """Test bedroom extraction from title text."""
        html = """
        <a href="/property/123">
            <span>1 Bed Apartment, Hackney E8</span>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        assert link is not None
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
        assert link is not None
        title, _address, postcode = openrent_scraper._parse_link_text(link)

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
        assert link is not None
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
        # Mock _fetch_page to return fixture HTML on page 1, None on page 2
        call_count = 0

        async def mock_fetch(url: str) -> str | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return openrent_search_html
            return None  # Page 2 returns nothing → pagination stops

        with (
            patch.object(openrent_scraper, "_fetch_page", side_effect=mock_fetch),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await openrent_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
            )

        assert len(result) == 3
        assert all(p.source == PropertySource.OPENRENT for p in result)


class TestOpenRentNoEarlyStop:
    """Tests verifying OpenRent does NOT use early-stop pagination.

    OpenRent has no "newest first" sort (sortType only supports 0=Distance,
    1=Price↑, 2=Price↓). The early-stop assumption requires newest-first
    ordering, so it's intentionally disabled for this scraper.
    """

    def test_search_url_has_no_sort_type(self, openrent_scraper: OpenRentScraper) -> None:
        """Verify sortType is not included — OpenRent has no valid 'newest' sort."""
        url = openrent_scraper._build_search_url(
            area="e8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=0,
            max_bedrooms=2,
        )
        assert "sortType" not in url

    @pytest.mark.asyncio
    async def test_does_not_early_stop_even_when_all_known(
        self, openrent_scraper: OpenRentScraper, openrent_search_html: str
    ) -> None:
        """Even when all page-1 properties are known, OpenRent fetches page 2.

        Because results are sorted by distance (not newest), all-known on
        page 1 does NOT imply everything after is older — new listings may
        appear on later pages.
        """
        soup = BeautifulSoup(openrent_search_html, "html.parser")
        page1_props = openrent_scraper._parse_search_results(soup, "https://test")
        known_ids = {p.source_id for p in page1_props}
        assert len(known_ids) >= 2  # sanity

        pages_fetched: list[int] = []

        async def mock_fetch(url: str) -> str | None:
            idx = len(pages_fetched)
            pages_fetched.append(idx)
            if idx == 0:
                return openrent_search_html
            # Page 2 returns empty → pagination stops naturally
            return "<html></html>"

        with (
            patch.object(openrent_scraper, "_fetch_page", side_effect=mock_fetch),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await openrent_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
                known_source_ids=known_ids,
            )

        assert len(pages_fetched) >= 2  # Did NOT early-stop on page 1
        assert len(result) == len(page1_props)  # Page 1 props still returned


class TestOpenRent429Handling:
    """Tests for 429 rate limit handling."""

    @pytest.mark.asyncio
    async def test_retries_on_429(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that _fetch_page retries on 429 with exponential backoff."""
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.text = "<html>OK</html>"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[mock_response_429, mock_response_200])

        openrent_scraper._session = mock_session

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await openrent_scraper._fetch_page("https://openrent.co.uk/test")

        assert result == "<html>OK</html>"
        assert mock_session.get.call_count == 2
        mock_sleep.assert_called_once_with(2.0)  # Initial backoff

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self, openrent_scraper: OpenRentScraper) -> None:
        """Test that _fetch_page returns None after max retries."""
        mock_response = MagicMock()
        mock_response.status_code = 429

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)

        openrent_scraper._session = mock_session

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await openrent_scraper._fetch_page("https://openrent.co.uk/test")

        assert result is None
        assert mock_session.get.call_count == 4  # MAX_RETRIES
        # Should have slept 3 times (not on the last attempt)
        assert mock_sleep.call_count == 3
        # Verify exponential backoff: 2s, 4s, 8s
        mock_sleep.assert_any_call(2.0)
        mock_sleep.assert_any_call(4.0)
        mock_sleep.assert_any_call(8.0)

    @pytest.mark.asyncio
    async def test_proxy_passthrough(self) -> None:
        """Test that proxy_url is passed to curl_cffi session.get()."""
        scraper = OpenRentScraper(proxy_url="socks5://proxy:1080")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html>OK</html>"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        scraper._session = mock_session

        await scraper._fetch_page("https://openrent.co.uk/test")

        call_kwargs = mock_session.get.call_args
        assert call_kwargs[1]["proxy"] == "socks5://proxy:1080"

    @pytest.mark.asyncio
    async def test_session_cleanup(self) -> None:
        """Test that close() cleans up the session."""
        scraper = OpenRentScraper()
        mock_session = AsyncMock()
        scraper._session = mock_session

        await scraper.close()

        mock_session.close.assert_called_once()
        assert scraper._session is None
