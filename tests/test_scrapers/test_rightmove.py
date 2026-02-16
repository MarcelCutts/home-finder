"""Tests for Rightmove scraper."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup
from pytest_httpx import HTTPXMock

from home_finder.models import PropertySource
from home_finder.scrapers.parsing import extract_bedrooms, extract_postcode, extract_price
from home_finder.scrapers.rightmove import (
    RightmoveScraper,
    _outcode_cache,
    get_rightmove_outcode_id,
)


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

    @pytest.mark.asyncio
    async def test_build_search_url(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test URL building with search parameters."""
        url = await rightmove_scraper._build_search_url(
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

    @pytest.mark.asyncio
    async def test_build_search_url_with_location_identifier(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test URL building includes location identifier."""
        url = await rightmove_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Rightmove uses REGION% encoded identifiers
        assert "locationIdentifier=" in url

    @pytest.mark.asyncio
    async def test_build_search_url_with_outcode(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test URL building with outcode uses hardcoded OUTCODE identifier."""
        # E8 uses hardcoded mapping OUTCODE%5E762
        url = await rightmove_scraper._build_search_url(
            area="E8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Should use hardcoded OUTCODE identifier for E8
        assert "locationIdentifier=OUTCODE%5E762" in url
        assert "rightmove.co.uk" in url

    @pytest.mark.asyncio
    async def test_build_search_url_omits_min_bedrooms_zero(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test that minBedrooms is omitted from URL when min_bedrooms=0."""
        url = await rightmove_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=0,
            max_bedrooms=2,
        )
        assert "minBedrooms" not in url
        assert "maxBedrooms=2" in url

    @pytest.mark.asyncio
    async def test_build_search_url_outcode_fallback(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test URL building returns empty when outcode not found (no silent fallback)."""
        with patch(
            "home_finder.scrapers.rightmove.get_rightmove_outcode_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            url = await rightmove_scraper._build_search_url(
                area="ZZ99",
                min_price=1800,
                max_price=2200,
                min_bedrooms=1,
                max_bedrooms=2,
            )
            assert url == ""

    @pytest.mark.asyncio
    async def test_build_search_url_all_target_outcodes(
        self, rightmove_scraper: RightmoveScraper
    ) -> None:
        """Test URL building for all target outcodes uses hardcoded mappings."""
        expected = {
            "E3": "OUTCODE%5E756",
            "E5": "OUTCODE%5E758",
            "E8": "OUTCODE%5E762",
            "E9": "OUTCODE%5E763",
            "E10": "OUTCODE%5E745",
            "N15": "OUTCODE%5E1672",
        }
        for outcode, expected_id in expected.items():
            url = await rightmove_scraper._build_search_url(
                area=outcode,
                min_price=1800,
                max_price=2200,
                min_bedrooms=1,
                max_bedrooms=2,
            )
            assert f"locationIdentifier={expected_id}" in url, f"Wrong identifier for {outcode}"


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

    def test_extract_property_id_no_match(self, rightmove_scraper: RightmoveScraper) -> None:
        """Test property ID extraction with invalid URL."""
        url = "/search/results.html"
        prop_id = rightmove_scraper._extract_property_id(url)
        assert prop_id is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("£2,300 pcm", 2300),
            ("£1,950 pcm", 1950),
            ("£2,100 pcm", 2100),
            ("£500 pw", 2166),  # Weekly to monthly (500*52/12)
            ("£2,400 pcm£554 pw", 2400),  # Both present: use PCM
            ("£2,400 pcm\n£554 pw", 2400),  # Newline-separated
            ("£2,400 pcm (£554 pw)", 2400),  # Parenthesised pw
        ],
    )
    def test_extract_price(self, text: str, expected: int) -> None:
        """Test price extraction from text."""
        assert extract_price(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "Contact agent",
            "",
            "POA",
        ],
    )
    def test_extract_price_invalid(self, text: str) -> None:
        """Test price extraction with invalid text."""
        assert extract_price(text) is None

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("1 bedroom flat to rent", 1),
            ("2 bedroom apartment to rent", 2),
            ("Studio to rent", 0),
            ("3 bed house", 3),
        ],
    )
    def test_extract_bedrooms(self, title: str, expected: int) -> None:
        """Test bedroom extraction from title."""
        assert extract_bedrooms(title) == expected

    @pytest.mark.parametrize(
        "title",
        [
            "Flat to rent",
            "",
        ],
    )
    def test_extract_bedrooms_no_match(self, title: str) -> None:
        """Test bedroom extraction with no bedroom info."""
        assert extract_bedrooms(title) is None

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("Wayland Avenue, London E8", "E8"),
            ("Mare Street, Hackney, London E8 3RH", "E8 3RH"),
            ("Islington N1 2AA", "N1 2AA"),
        ],
    )
    def test_extract_postcode(self, address: str, expected: str) -> None:
        """Test postcode extraction from address."""
        assert extract_postcode(address) == expected

    @pytest.mark.parametrize(
        "address",
        [
            "Some Address, London",
            "",
        ],
    )
    def test_extract_postcode_no_match(self, address: str) -> None:
        """Test postcode extraction with no postcode."""
        assert extract_postcode(address) is None

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
        properties = rightmove_scraper._parse_search_results(soup, "https://rightmove.co.uk")
        assert len(properties) == 0


class TestRightmoveOutcodeResolver:
    """Tests for Rightmove outcode resolver."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        """Clear the outcode cache before each test."""
        _outcode_cache.clear()

    @pytest.mark.asyncio
    async def test_get_outcode_id_success(self, httpx_mock: HTTPXMock) -> None:
        """Test successful outcode lookup via mocked API."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/E8/",
            json={
                "typeAheadLocations": [
                    {
                        "displayName": "E8",
                        "locationIdentifier": "OUTCODE^707",
                    }
                ]
            },
        )

        result = await get_rightmove_outcode_id("E8")

        assert result == "OUTCODE^707"
        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert "E8" in str(requests[0].url)

    @pytest.mark.asyncio
    async def test_get_outcode_id_caching(self, httpx_mock: HTTPXMock) -> None:
        """Test that outcode lookups are cached."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/N1/5/",
            json={
                "typeAheadLocations": [
                    {
                        "displayName": "N15",
                        "locationIdentifier": "OUTCODE^123",
                    }
                ]
            },
        )

        # First call should hit the API
        result1 = await get_rightmove_outcode_id("N15")
        # Second call should use cache
        result2 = await get_rightmove_outcode_id("N15")

        assert result1 == "OUTCODE^123"
        assert result2 == "OUTCODE^123"
        # Should only call API once
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

    @pytest.mark.asyncio
    async def test_get_outcode_id_not_found(self, httpx_mock: HTTPXMock) -> None:
        """Test outcode lookup when outcode not in response."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/E8/",
            json={
                "typeAheadLocations": [
                    {
                        "displayName": "OTHER",
                        "locationIdentifier": "OUTCODE^999",
                    }
                ]
            },
        )

        result = await get_rightmove_outcode_id("E8")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_outcode_id_api_error(self, httpx_mock: HTTPXMock) -> None:
        """Test outcode lookup when API returns error."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/E8/",
            status_code=500,
        )

        result = await get_rightmove_outcode_id("E8")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_outcode_id_network_error(self, httpx_mock: HTTPXMock) -> None:
        """Test outcode lookup when network error occurs."""
        httpx_mock.add_exception(
            httpx.ConnectError("Network error"),
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/E8/",
        )

        result = await get_rightmove_outcode_id("E8")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_outcode_id_normalizes_case(self, httpx_mock: HTTPXMock) -> None:
        """Test that outcode lookup normalizes to uppercase."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/E8/",
            json={
                "typeAheadLocations": [
                    {
                        "displayName": "E8",
                        "locationIdentifier": "OUTCODE^707",
                    }
                ]
            },
        )

        result = await get_rightmove_outcode_id("e8")
        assert result == "OUTCODE^707"

    @pytest.mark.asyncio
    async def test_get_outcode_tokenization_short(self, httpx_mock: HTTPXMock) -> None:
        """Test URL tokenization for short outcode (E8 -> E8/)."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/E8/",
            json={"typeAheadLocations": []},
        )

        await get_rightmove_outcode_id("E8")

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert "/E8/" in str(requests[0].url)

    @pytest.mark.asyncio
    async def test_get_outcode_tokenization_long(self, httpx_mock: HTTPXMock) -> None:
        """Test URL tokenization for longer outcode (N15 -> N1/5/)."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/N1/5/",
            json={"typeAheadLocations": []},
        )

        await get_rightmove_outcode_id("N15")

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert "/N1/5/" in str(requests[0].url)

    @pytest.mark.asyncio
    async def test_get_outcode_matches_prefix(self, httpx_mock: HTTPXMock) -> None:
        """Test that outcode lookup matches display names starting with outcode."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/typeAhead/uknostreet/E8/",
            json={
                "typeAheadLocations": [
                    {
                        "displayName": "E8, Hackney",
                        "locationIdentifier": "OUTCODE^707",
                    }
                ]
            },
        )

        result = await get_rightmove_outcode_id("E8")
        assert result == "OUTCODE^707"


class TestRightmoveEarlyStop:
    """Tests for early-stop pagination (requires newest-first sort)."""

    @pytest.mark.asyncio
    async def test_search_url_sorts_by_newest(self, rightmove_scraper: RightmoveScraper) -> None:
        """Verify sortType=6 (newest listed) — required for early-stop correctness."""
        url = await rightmove_scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=0,
            max_bedrooms=2,
        )
        assert "sortType=6" in url

    @pytest.mark.asyncio
    async def test_stops_when_all_results_known(
        self, rightmove_scraper: RightmoveScraper, rightmove_search_html: str
    ) -> None:
        """When all page-1 properties are already in DB, stop without fetching page 2."""
        soup = BeautifulSoup(rightmove_search_html, "html.parser")
        page1_props = rightmove_scraper._parse_search_results(
            soup, "https://www.rightmove.co.uk/property-to-rent/find.html"
        )
        known_ids = {p.source_id for p in page1_props}
        assert len(known_ids) >= 2

        pages_fetched: list[int] = []

        with patch("home_finder.scrapers.rightmove.BeautifulSoupCrawler") as MockCrawler:

            def make_crawler(**kwargs):  # type: ignore[no-untyped-def]
                idx = len(pages_fetched)
                mock = MagicMock()
                handler: list = []
                mock.router = MagicMock()
                mock.router.default_handler = handler.append

                async def run(urls: list[str]) -> None:
                    ctx = MagicMock()
                    ctx.soup = BeautifulSoup(rightmove_search_html, "html.parser")
                    ctx.request.url = urls[0]
                    await handler[0](ctx)

                mock.run = run
                pages_fetched.append(idx)
                return mock

            MockCrawler.side_effect = make_crawler

            result = await rightmove_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
                known_source_ids=known_ids,
            )

        assert len(pages_fetched) == 1  # Only page 1 fetched
        assert result == []  # All known → nothing returned

    @pytest.mark.asyncio
    async def test_continues_when_some_results_new(
        self, rightmove_scraper: RightmoveScraper, rightmove_search_html: str
    ) -> None:
        """When only some results are known, don't early-stop — fetch next page."""
        soup = BeautifulSoup(rightmove_search_html, "html.parser")
        page1_props = rightmove_scraper._parse_search_results(
            soup, "https://www.rightmove.co.uk/property-to-rent/find.html"
        )
        known_ids = {page1_props[0].source_id}

        pages_fetched: list[int] = []

        with patch("home_finder.scrapers.rightmove.BeautifulSoupCrawler") as MockCrawler:

            def make_crawler(**kwargs):  # type: ignore[no-untyped-def]
                idx = len(pages_fetched)
                mock = MagicMock()
                handler: list = []
                mock.router = MagicMock()
                mock.router.default_handler = handler.append

                async def run(urls: list[str]) -> None:
                    ctx = MagicMock()
                    if idx == 0:
                        ctx.soup = BeautifulSoup(rightmove_search_html, "html.parser")
                    else:
                        ctx.soup = BeautifulSoup("<html></html>", "html.parser")
                    ctx.request.url = urls[0]
                    await handler[0](ctx)

                mock.run = run
                pages_fetched.append(idx)
                return mock

            MockCrawler.side_effect = make_crawler

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await rightmove_scraper.scrape(
                    min_price=1800,
                    max_price=2500,
                    min_bedrooms=1,
                    max_bedrooms=2,
                    area="hackney",
                    known_source_ids=known_ids,
                )

        assert len(pages_fetched) >= 2  # Continued past page 1
        assert len(result) == len(page1_props)
