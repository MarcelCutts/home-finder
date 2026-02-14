"""Tests for Zoopla scraper."""

import json
from pathlib import Path
from types import SimpleNamespace
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
    async def test_stops_when_all_results_known(self, zoopla_scraper: ZooplaScraper) -> None:
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
    async def test_continues_when_some_results_new(self, zoopla_scraper: ZooplaScraper) -> None:
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


def _make_response(
    status_code: int = 200,
    text: str = "<html></html>",
    headers: dict[str, str] | None = None,
) -> SimpleNamespace:
    """Create a fake curl_cffi response for testing."""
    return SimpleNamespace(
        status_code=status_code,
        text=text,
        headers=headers or {},
    )


class TestCloudflareDetection:
    """Tests for Cloudflare challenge detection."""

    def test_detects_cf_mitigated_header(self, zoopla_scraper: ZooplaScraper) -> None:
        """cf-mitigated: challenge header should be detected."""
        resp = _make_response(status_code=403, headers={"cf-mitigated": "challenge"})
        assert zoopla_scraper._is_cloudflare_challenge(resp) is True

    def test_detects_403_with_challenge_html(self, zoopla_scraper: ZooplaScraper) -> None:
        """403 with 'Just a moment' in body is a Cloudflare challenge."""
        resp = _make_response(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
        )
        assert zoopla_scraper._is_cloudflare_challenge(resp) is True

    def test_detects_503_with_challenge_html(self, zoopla_scraper: ZooplaScraper) -> None:
        """503 with Cloudflare markers is a challenge."""
        resp = _make_response(
            status_code=503,
            text="<html><body>Cloudflare Ray ID: abc123</body></html>",
        )
        assert zoopla_scraper._is_cloudflare_challenge(resp) is True

    def test_detects_cf_chl_opt_marker(self, zoopla_scraper: ZooplaScraper) -> None:
        """403 with _cf_chl_opt script is a challenge."""
        resp = _make_response(
            status_code=403,
            text="<script>window._cf_chl_opt={}</script>",
        )
        assert zoopla_scraper._is_cloudflare_challenge(resp) is True

    def test_normal_403_is_not_challenge(self, zoopla_scraper: ZooplaScraper) -> None:
        """403 without Cloudflare markers is NOT a challenge."""
        resp = _make_response(
            status_code=403,
            text="<html><body>Access Denied</body></html>",
        )
        assert zoopla_scraper._is_cloudflare_challenge(resp) is False

    def test_200_is_not_challenge(self, zoopla_scraper: ZooplaScraper) -> None:
        """Normal 200 response is not a challenge."""
        resp = _make_response(status_code=200, text="<html>OK</html>")
        assert zoopla_scraper._is_cloudflare_challenge(resp) is False

    def test_200_with_cf_mitigated_is_challenge(self, zoopla_scraper: ZooplaScraper) -> None:
        """200 with cf-mitigated header is a soft challenge."""
        resp = _make_response(
            status_code=200,
            text="<html>challenge page</html>",
            headers={"cf-mitigated": "challenge"},
        )
        assert zoopla_scraper._is_cloudflare_challenge(resp) is True


class TestFetchPageRetry:
    """Tests for _fetch_page retry logic with Cloudflare handling."""

    @pytest.mark.asyncio
    async def test_retries_on_403_challenge(self, zoopla_scraper: ZooplaScraper) -> None:
        """403 Cloudflare challenge should be retried, then succeed on 200."""
        challenge_resp = _make_response(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
        )
        ok_resp = _make_response(status_code=200, text="<html>property data</html>")

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[challenge_resp, ok_resp])

        with (
            patch.object(zoopla_scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await zoopla_scraper._fetch_page("https://example.com")

        assert result == "<html>property data</html>"
        assert mock_session.get.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_soft_200_challenge(self, zoopla_scraper: ZooplaScraper) -> None:
        """200 response with cf-mitigated header should be retried."""
        soft_challenge = _make_response(
            status_code=200,
            text="<html>challenge</html>",
            headers={"cf-mitigated": "challenge"},
        )
        ok_resp = _make_response(status_code=200, text="<html>real data</html>")

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[soft_challenge, ok_resp])

        with (
            patch.object(zoopla_scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await zoopla_scraper._fetch_page("https://example.com")

        assert result == "<html>real data</html>"
        assert mock_session.get.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_hard_404(self, zoopla_scraper: ZooplaScraper) -> None:
        """404 without challenge markers should NOT be retried."""
        resp = _make_response(status_code=404, text="Not Found")

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=resp)

        with patch.object(zoopla_scraper, "_get_session", return_value=mock_session):
            result = await zoopla_scraper._fetch_page("https://example.com")

        assert result is None
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_exception(self, zoopla_scraper: ZooplaScraper) -> None:
        """Network exceptions should be retried with backoff."""
        ok_resp = _make_response(status_code=200, text="<html>data</html>")

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(
            side_effect=[ConnectionError("timeout"), ok_resp],
        )

        with (
            patch.object(zoopla_scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await zoopla_scraper._fetch_page("https://example.com")

        assert result == "<html>data</html>"
        assert mock_session.get.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_after_4_attempts(self, zoopla_scraper: ZooplaScraper) -> None:
        """After 4 failed challenge attempts, return None."""
        challenge_resp = _make_response(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
        )

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=challenge_resp)

        with (
            patch.object(zoopla_scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await zoopla_scraper._fetch_page("https://example.com")

        assert result is None
        assert mock_session.get.call_count == 4

    @pytest.mark.asyncio
    async def test_passes_impersonate_target(self, zoopla_scraper: ZooplaScraper) -> None:
        """impersonate_target kwarg should be forwarded to session.get."""
        ok_resp = _make_response(status_code=200, text="<html>ok</html>")

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=ok_resp)

        with patch.object(zoopla_scraper, "_get_session", return_value=mock_session):
            await zoopla_scraper._fetch_page("https://example.com", impersonate_target="safari")

        call_kwargs = mock_session.get.call_args[1]
        assert call_kwargs["impersonate"] == "safari"


class TestProfileRotation:
    """Tests for browser profile rotation."""

    def test_pick_impersonate_target_returns_valid(self, zoopla_scraper: ZooplaScraper) -> None:
        """Returned target should be from the known list."""
        for _ in range(20):
            target = zoopla_scraper._pick_impersonate_target()
            assert target in ZooplaScraper._IMPERSONATE_TARGETS

    @pytest.mark.asyncio
    async def test_scrape_passes_impersonate_to_fetch(self, zoopla_scraper: ZooplaScraper) -> None:
        """scrape() should pass a chosen impersonate target to _fetch_page."""
        mock_fetch = AsyncMock(return_value="<html></html>")
        with patch.object(zoopla_scraper, "_fetch_page", mock_fetch):
            await zoopla_scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="e8",
            )

        # _fetch_page should have been called with impersonate_target kwarg
        call_kwargs = mock_fetch.call_args[1]
        assert "impersonate_target" in call_kwargs
        assert call_kwargs["impersonate_target"] in ZooplaScraper._IMPERSONATE_TARGETS


class TestWarmUp:
    """Tests for homepage warm-up before searching."""

    @pytest.mark.asyncio
    async def test_warm_up_visits_homepage(self) -> None:
        """Warm-up should GET the homepage to establish cookies."""
        scraper = ZooplaScraper()
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=_make_response(200))

        with (
            patch.object(scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            await scraper._warm_up()

        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        assert call_args[0][0] == "https://www.zoopla.co.uk/"
        assert scraper._warmed_up is True

    @pytest.mark.asyncio
    async def test_warm_up_only_once(self) -> None:
        """Warm-up should not re-run if already done."""
        scraper = ZooplaScraper()
        scraper._warmed_up = True
        mock_session = AsyncMock()

        with patch.object(scraper, "_get_session", return_value=mock_session):
            await scraper._warm_up()

        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_warm_up_failure_is_graceful(self) -> None:
        """Warm-up failure should not prevent scraping."""
        scraper = ZooplaScraper()
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=ConnectionError("timeout"))

        with patch.object(scraper, "_get_session", return_value=mock_session):
            await scraper._warm_up()

        # Should be marked as warmed up (won't retry)
        assert scraper._warmed_up is True

    @pytest.mark.asyncio
    async def test_scrape_calls_warm_up(self) -> None:
        """scrape() should call _warm_up before fetching pages."""
        scraper = ZooplaScraper()
        warm_up_called = False

        async def mock_warm_up() -> None:
            nonlocal warm_up_called
            warm_up_called = True

        mock_fetch = AsyncMock(return_value="<html></html>")
        with (
            patch.object(scraper, "_warm_up", side_effect=mock_warm_up),
            patch.object(scraper, "_fetch_page", mock_fetch),
        ):
            await scraper.scrape(
                min_price=1800,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="e8",
            )

        assert warm_up_called


class TestAdaptiveDelay:
    """Tests for adaptive inter-area delay based on consecutive blocks."""

    @pytest.mark.asyncio
    async def test_normal_delay_no_blocks(self) -> None:
        """With 0 consecutive blocks, delay should be 10-20s."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 0

        sleep_patch = patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock)
        with sleep_patch as mock_sleep:
            await scraper.area_delay()

        delay = mock_sleep.call_args[0][0]
        assert 10.0 <= delay <= 20.0

    @pytest.mark.asyncio
    async def test_slowdown_delay_1_block(self) -> None:
        """With 1 consecutive block, delay should be 20-40s."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 1

        sleep_patch = patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock)
        with sleep_patch as mock_sleep:
            await scraper.area_delay()

        delay = mock_sleep.call_args[0][0]
        assert 20.0 <= delay <= 40.0

    @pytest.mark.asyncio
    async def test_extended_cooldown_3_blocks(self) -> None:
        """With 3+ consecutive blocks, delay should be 45-75s."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 3

        sleep_patch = patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock)
        with sleep_patch as mock_sleep:
            await scraper.area_delay()

        delay = mock_sleep.call_args[0][0]
        assert 45.0 <= delay <= 75.0


class TestConsecutiveBlockTracking:
    """Tests for consecutive block counter and session refresh."""

    @pytest.mark.asyncio
    async def test_success_resets_counter(self) -> None:
        """Successful fetch should reset consecutive block counter."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 3

        ok_resp = _make_response(status_code=200, text="<html>data</html>")
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=ok_resp)

        with patch.object(scraper, "_get_session", return_value=mock_session):
            result = await scraper._fetch_page("https://example.com")

        assert result == "<html>data</html>"
        assert scraper._consecutive_blocks == 0

    @pytest.mark.asyncio
    async def test_exhaustion_increments_counter(self) -> None:
        """Exhausted retries should increment consecutive block counter."""
        scraper = ZooplaScraper()
        assert scraper._consecutive_blocks == 0

        challenge_resp = _make_response(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
        )
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=challenge_resp)

        with (
            patch.object(scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await scraper._fetch_page("https://example.com")

        assert result is None
        assert scraper._consecutive_blocks == 1

    @pytest.mark.asyncio
    async def test_session_reset_at_2_blocks(self) -> None:
        """Session should be reset after 2 consecutive blocks."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 1  # Will become 2 after this exhaustion

        challenge_resp = _make_response(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
        )
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=challenge_resp)
        mock_session.close = AsyncMock()

        with (
            patch.object(scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            scraper._session = mock_session
            await scraper._fetch_page("https://example.com")

        assert scraper._consecutive_blocks == 2
        assert scraper._session is None
        assert scraper._warmed_up is False

    @pytest.mark.asyncio
    async def test_no_session_reset_at_1_block(self) -> None:
        """Session should NOT be reset after only 1 block."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 0

        challenge_resp = _make_response(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
        )
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=challenge_resp)

        with (
            patch.object(scraper, "_get_session", return_value=mock_session),
            patch("home_finder.scrapers.zoopla.asyncio.sleep", new_callable=AsyncMock),
        ):
            scraper._session = mock_session
            await scraper._fetch_page("https://example.com")

        assert scraper._consecutive_blocks == 1
        assert scraper._session is mock_session  # Not reset


class TestSkipRemainingAreas:
    """Tests for should_skip_remaining_areas property."""

    def test_skip_at_5_blocks(self) -> None:
        """Should skip when consecutive blocks >= 5."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 5
        assert scraper.should_skip_remaining_areas is True

    def test_no_skip_at_4_blocks(self) -> None:
        """Should not skip when consecutive blocks < 5."""
        scraper = ZooplaScraper()
        scraper._consecutive_blocks = 4
        assert scraper.should_skip_remaining_areas is False

    def test_no_skip_at_0_blocks(self) -> None:
        """Should not skip with no blocks."""
        scraper = ZooplaScraper()
        assert scraper.should_skip_remaining_areas is False


class TestMaxAreasPerRun:
    """Tests for max_areas_per_run property."""

    def test_default_none(self) -> None:
        """Default max_areas should be None (unlimited)."""
        scraper = ZooplaScraper()
        assert scraper.max_areas_per_run is None

    def test_custom_value(self) -> None:
        """Custom max_areas should be reflected."""
        scraper = ZooplaScraper(max_areas=4)
        assert scraper.max_areas_per_run == 4


class TestSessionReset:
    """Tests for session reset."""

    @pytest.mark.asyncio
    async def test_reset_clears_session_and_warmup(self) -> None:
        """_reset_session should close session and clear warmed_up flag."""
        scraper = ZooplaScraper()
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()
        scraper._session = mock_session
        scraper._warmed_up = True

        await scraper._reset_session()

        mock_session.close.assert_called_once()
        assert scraper._session is None
        assert scraper._warmed_up is False

    @pytest.mark.asyncio
    async def test_reset_noop_without_session(self) -> None:
        """_reset_session should be safe to call without an active session."""
        scraper = ZooplaScraper()
        scraper._warmed_up = True

        await scraper._reset_session()

        assert scraper._session is None
        assert scraper._warmed_up is False
