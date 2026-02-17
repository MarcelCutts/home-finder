"""Unit tests for DetailFetcher parsing logic.

Tests the parsing of each platform's detail page HTML without network access.
HTTP calls are mocked to return fixture HTML; assertions verify parsing correctness.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import HttpUrl

from home_finder.models import Property, PropertySource
from home_finder.scrapers.detail_fetcher import (
    DetailFetcher,
    DetailPageData,
    _find_dict_with_key,
    _is_epc_url,
    _zoopla_floorplan_from_html,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_property(source: PropertySource, source_id: str = "test-1") -> Property:
    """Create a minimal Property for detail fetching."""
    urls = {
        PropertySource.RIGHTMOVE: f"https://www.rightmove.co.uk/properties/{source_id}",
        PropertySource.ZOOPLA: f"https://www.zoopla.co.uk/to-rent/details/{source_id}",
        PropertySource.OPENRENT: f"https://www.openrent.com/property/{source_id}",
        PropertySource.ONTHEMARKET: f"https://www.onthemarket.com/details/{source_id}",
    }
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(urls[source]),
        title="Test Property",
        price_pcm=1800,
        bedrooms=1,
        address="123 Test Street",
    )


def _mock_httpx_response(html: str) -> MagicMock:
    """Create a mock httpx.Response with the given HTML."""
    resp = MagicMock()
    resp.text = html
    resp.status_code = 200
    resp.url = "https://example.com/property"
    return resp


def _mock_curl_response(html: str, status_code: int = 200) -> MagicMock:
    """Create a mock curl_cffi response."""
    resp = MagicMock()
    resp.text = html
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# _find_dict_with_key (pure utility)
# ---------------------------------------------------------------------------


class TestFindDictWithKey:
    def test_finds_key_in_flat_dict(self) -> None:
        data = {"foo": 1, "bar": 2}
        assert _find_dict_with_key(data, "foo") == data

    def test_finds_key_in_nested_dict(self) -> None:
        data = {"outer": {"inner": {"target": 42}}}
        result = _find_dict_with_key(data, "target")
        assert result == {"target": 42}

    def test_finds_key_in_list_of_dicts(self) -> None:
        data = [{"a": 1}, {"target": 2}]
        result = _find_dict_with_key(data, "target")
        assert result == {"target": 2}

    def test_returns_none_when_not_found(self) -> None:
        data = {"a": {"b": {"c": 1}}}
        assert _find_dict_with_key(data, "missing") is None

    def test_respects_depth_limit(self) -> None:
        # Build a deeply nested structure
        data: dict = {"level": 0}
        current = data
        for i in range(15):
            current["child"] = {"level": i + 1}
            current = current["child"]
        current["target"] = "found"
        # Default depth limit is 10, so this should not be found
        assert _find_dict_with_key(data, "target") is None

    def test_handles_non_dict_input(self) -> None:
        assert _find_dict_with_key("string", "key") is None
        assert _find_dict_with_key(42, "key") is None
        assert _find_dict_with_key(None, "key") is None


# ---------------------------------------------------------------------------
# _is_epc_url (EPC image detection)
# ---------------------------------------------------------------------------


class TestIsEpcUrl:
    def test_detects_epc_in_path(self) -> None:
        assert _is_epc_url("https://media.rightmove.co.uk/epc/123_EPC_00.png")

    def test_detects_epc_in_filename(self) -> None:
        assert _is_epc_url("https://imagescdn.openrent.co.uk/listings/999/epc_chart.jpg")

    def test_detects_energy_performance_hyphenated(self) -> None:
        assert _is_epc_url("https://cdn.example.com/energy-performance-chart.png")

    def test_detects_energy_performance_underscored(self) -> None:
        assert _is_epc_url("https://cdn.example.com/energy_performance_123.jpg")

    def test_case_insensitive(self) -> None:
        assert _is_epc_url("https://media.rightmove.co.uk/img/123_EPC_Graph.jpg")

    def test_normal_image_not_matched(self) -> None:
        assert not _is_epc_url("https://media.rightmove.co.uk/img/123_01.jpg")
        assert not _is_epc_url("https://lid.zoocdn.com/u/1024/768/abc123.jpg")

    def test_hash_based_zoopla_url_not_filterable(self) -> None:
        """Zoopla EPC images use opaque hash filenames — not detectable by URL alone.
        Caption-based filtering in the RSC pass handles these instead."""
        assert not _is_epc_url("https://lid.zoocdn.com/u/1024/768/5e2020de.png")


# ---------------------------------------------------------------------------
# Rightmove parsing
# ---------------------------------------------------------------------------


class TestRightmoveParsing:
    @pytest.fixture
    def fetcher(self) -> DetailFetcher:
        return DetailFetcher(max_gallery_images=10)

    @pytest.fixture
    def with_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "rightmove_detail_with_floorplan.html").read_text()

    @pytest.fixture
    def no_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "rightmove_detail_no_floorplan.html").read_text()

    async def test_extracts_floorplan(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url == "https://media.rightmove.co.uk/floor/123_FLP_00.jpg"

    async def test_extracts_gallery(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 3
        assert "img/123_01.jpg" in result.gallery_urls[0]

    async def test_extracts_description(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.description is not None
        assert "spacious one bedroom" in result.description

    async def test_extracts_features(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.features is not None
        assert "Modern kitchen" in result.features

    async def test_no_floorplan_returns_none(
        self, fetcher: DetailFetcher, no_floorplan: str
    ) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(no_floorplan)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url is None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2

    async def test_extracts_location(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.latitude == pytest.approx(51.5465)
        assert result.longitude == pytest.approx(-0.0553)
        assert result.postcode == "E8 3RH"

    async def test_missing_location_returns_none_coords(self, fetcher: DetailFetcher) -> None:
        html = """<!DOCTYPE html><html><body><script>
        window.PAGE_MODEL = {
            "propertyData": {
                "floorplans": [],
                "images": [{"url": "https://example.com/img.jpg"}],
                "text": {"description": "A flat."},
                "keyFeatures": []
            }
        };
        </script></body></html>"""
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(html)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.latitude is None
        assert result.longitude is None
        assert result.postcode is None

    async def test_no_page_model_returns_none(self, fetcher: DetailFetcher) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response("<html><body>No data</body></html>")
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_http_error_returns_none(self, fetcher: DetailFetcher) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("Connection failed")
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_respects_max_gallery_images(
        self, fixtures_path: Path, with_floorplan: str
    ) -> None:
        fetcher = DetailFetcher(max_gallery_images=2)
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2

    async def test_gallery_excludes_epc_images(self, fetcher: DetailFetcher) -> None:
        html = """<!DOCTYPE html><html><body><script>
        window.PAGE_MODEL = {
            "propertyData": {
                "floorplans": [],
                "images": [
                    {"url": "https://media.rightmove.co.uk/img/123_01.jpg"},
                    {"url": "https://media.rightmove.co.uk/epc/123_EPC_00.png"},
                    {"url": "https://media.rightmove.co.uk/img/123_02.jpg"}
                ],
                "text": {"description": "A flat."},
                "keyFeatures": []
            }
        };
        </script></body></html>"""
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(html)
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2
        for url in result.gallery_urls:
            assert "epc" not in url.lower()


# ---------------------------------------------------------------------------
# Zoopla parsing
# ---------------------------------------------------------------------------


class TestZooplaParsing:
    @pytest.fixture
    def fetcher(self) -> DetailFetcher:
        return DetailFetcher(max_gallery_images=10)

    @pytest.fixture
    def with_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "zoopla_detail_with_floorplan.html").read_text()

    @pytest.fixture
    def no_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "zoopla_detail_no_floorplan.html").read_text()

    async def test_extracts_floorplan(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url == "https://lid.zoocdn.com/u/floor/123.jpg"

    async def test_extracts_gallery(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 3

    async def test_extracts_description(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.description is not None
        assert "period conversion" in result.description

    async def test_extracts_features(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.features is not None
        assert "High ceilings" in result.features

    async def test_no_floorplan(self, fetcher: DetailFetcher, no_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(no_floorplan)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url is None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 1

    async def test_http_error_status_returns_none(self, fetcher: DetailFetcher) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response("", status_code=403)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_exception_returns_none(self, fetcher: DetailFetcher) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("TLS error")
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_rsc_caption_filters_epc(self, fetcher: DetailFetcher) -> None:
        """RSC path skips images with 'epc' in caption, keeps null-caption and normal."""
        html = """<!DOCTYPE html><html><body>
        \\"caption\\":\\"Living room\\",\\"filename\\":\\"aaa111.jpg\\"
        \\"caption\\":\\"EPC Rating\\",\\"filename\\":\\"epc222.jpg\\"
        \\"caption\\":null,\\"filename\\":\\"ccc444.jpg\\"
        \\"caption\\":\\"Bedroom\\",\\"filename\\":\\"bbb333.jpg\\"
        </body></html>"""
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(html)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 3
        hashes = [url.rsplit("/", 1)[-1].split(".")[0] for url in result.gallery_urls]
        assert "epc222" not in hashes
        assert "aaa111" in hashes
        assert "bbb333" in hashes
        assert "ccc444" in hashes

    async def test_rsc_null_caption_gallery_images(self, fetcher: DetailFetcher) -> None:
        """Null-caption images are extracted as gallery photos."""
        html = """<!DOCTYPE html><html><body>
        \\"caption\\":null,\\"filename\\":\\"aa00aa01.jpg\\"
        \\"caption\\":null,\\"filename\\":\\"bb00bb02.jpg\\"
        \\"caption\\":null,\\"filename\\":\\"cc00cc03.jpg\\"
        </body></html>"""
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(html)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 3
        hashes = [url.rsplit("/", 1)[-1].split(".")[0] for url in result.gallery_urls]
        assert hashes == ["aa00aa01", "bb00bb02", "cc00cc03"]

    async def test_rsc_filters_ee_rating_epc(self, fetcher: DetailFetcher) -> None:
        """'EE Rating' caption (Zoopla's EPC chart label) is filtered out."""
        html = """<!DOCTYPE html><html><body>
        \\"caption\\":null,\\"filename\\":\\"aa00aa01.jpg\\"
        \\"caption\\":\\"EE Rating\\",\\"filename\\":\\"ee00ee01.png\\"
        \\"caption\\":null,\\"filename\\":\\"bb00bb02.jpg\\"
        </body></html>"""
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(html)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2
        hashes = [url.rsplit("/", 1)[-1].split(".")[0] for url in result.gallery_urls]
        assert "ee00ee01" not in hashes
        assert "aa00aa01" in hashes
        assert "bb00bb02" in hashes

    async def test_rsc_excludes_floorplan_hashes(self, fetcher: DetailFetcher) -> None:
        """lc.zoocdn.com floorplan hashes are excluded from gallery results."""
        html = """<!DOCTYPE html><html><body>
        https://lc.zoocdn.com/8eb377a8.jpg
        \\"caption\\":null,\\"filename\\":\\"aaa11111.jpg\\"
        \\"caption\\":null,\\"filename\\":\\"8eb377a8.jpg\\"
        \\"caption\\":null,\\"filename\\":\\"bbb22222.jpg\\"
        </body></html>"""
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(html)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        hashes = [url.rsplit("/", 1)[-1].split(".")[0] for url in result.gallery_urls]
        assert "8eb377a8" not in hashes
        assert "aaa11111" in hashes
        assert "bbb22222" in hashes

    async def test_html_fallback_excludes_rsc_epc_hashes(self, fetcher: DetailFetcher) -> None:
        """HTML fallback inherits seen_hashes from RSC pass so EPC hashes don't reappear."""
        # RSC pass finds 1 normal + 1 EPC (skipped) → only 1 gallery image → triggers fallback
        # HTML fallback sees the EPC hash in full URL form — should still skip it
        html = """<!DOCTYPE html><html><body>
        \\"caption\\":\\"Kitchen\\",\\"filename\\":\\"aaa111.jpg\\"
        \\"caption\\":\\"EPC Rating\\",\\"filename\\":\\"epc222.jpg\\"
        https://lid.zoocdn.com/u/1024/768/epc222.jpg
        https://lid.zoocdn.com/u/1024/768/ccc333.jpg
        </body></html>"""
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(html)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        hashes = [url.rsplit("/", 1)[-1].split(".")[0] for url in result.gallery_urls]
        assert "epc222" not in hashes
        assert "aaa111" in hashes
        assert "ccc333" in hashes

    async def test_zoopla_hash_epc_not_filterable_by_url(self, fetcher: DetailFetcher) -> None:
        """Zoopla EPC images with opaque hash filenames can't be detected by URL.
        Only the RSC caption check catches these — the HTML-only path has no signal."""
        html = """<!DOCTYPE html><html><body>
        https://lid.zoocdn.com/u/1024/768/5e2020de.jpg
        https://lid.zoocdn.com/u/1024/768/abc12345.jpg
        </body></html>"""
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(html)
        )
        prop = _make_property(PropertySource.ZOOPLA)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        # Both pass through — no way to distinguish EPC from photo by hash URL alone
        assert len(result.gallery_urls) == 2


class TestZooplaFloorplanFromHtml:
    """Tests for _zoopla_floorplan_from_html extension-less URL support."""

    def test_extensionless_url_with_floor_in_path(self) -> None:
        """Extension-less lc.zoocdn.com URL with 'floor' in path -> extracted."""
        html = '<img src="https://lc.zoocdn.com/u/floor/abc123" />'
        result = _zoopla_floorplan_from_html(html)
        assert result == "https://lc.zoocdn.com/u/floor/abc123"

    def test_extension_based_url_preferred(self) -> None:
        """When both forms present -> extension-based URL returned first."""
        html = """
        <img src="https://lc.zoocdn.com/u/floor/abc123" />
        <img src="https://lc.zoocdn.com/fp/plan.jpg" />
        """
        result = _zoopla_floorplan_from_html(html)
        assert result is not None
        assert result.endswith(".jpg")

    def test_no_match_returns_none(self) -> None:
        html = "<html><body>No floorplan here</body></html>"
        assert _zoopla_floorplan_from_html(html) is None


# ---------------------------------------------------------------------------
# OpenRent parsing
# ---------------------------------------------------------------------------


class TestOpenRentParsing:
    @pytest.fixture
    def fetcher(self) -> DetailFetcher:
        return DetailFetcher(max_gallery_images=10)

    @pytest.fixture
    def with_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "openrent_detail_with_floorplan.html").read_text()

    @pytest.fixture
    def no_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "openrent_detail_no_floorplan.html").read_text()

    async def test_extracts_floorplan(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url is not None
        assert "floorplan" in result.floorplan_url.lower()

    async def test_gallery_excludes_floorplan(
        self, fetcher: DetailFetcher, with_floorplan: str
    ) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        # Gallery should NOT include the floorplan URL
        for url in result.gallery_urls:
            assert "floorplan" not in url.lower()

    async def test_gallery_has_full_urls(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(with_floorplan)
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        for url in result.gallery_urls:
            assert url.startswith("https://")

    async def test_no_floorplan(self, fetcher: DetailFetcher, no_floorplan: str) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(no_floorplan)
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url is None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2

    async def test_redirect_to_homepage_returns_none(self, fetcher: DetailFetcher) -> None:
        resp = _mock_httpx_response("<html>Homepage</html>")
        resp.url = "https://www.openrent.com/properties-to-rent/london"
        fetcher._httpx_get_with_retry = AsyncMock(return_value=resp)  # type: ignore[method-assign]
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_exception_returns_none(self, fetcher: DetailFetcher) -> None:
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("Timeout")
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_gallery_excludes_epc_lightbox(self, fetcher: DetailFetcher) -> None:
        """PhotoSwipe lightbox items with 'epc' in URL are filtered."""
        html = """<!DOCTYPE html><html><body>
        <a href="//imagescdn.openrent.co.uk/listings/999/o_photo1.JPG"
           class="lightbox_item"></a>
        <a href="//imagescdn.openrent.co.uk/listings/999/o_epc_chart.JPG"
           class="lightbox_item"></a>
        <a href="//imagescdn.openrent.co.uk/listings/999/o_photo2.JPG"
           class="lightbox_item"></a>
        </body></html>"""
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(html)
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2
        for url in result.gallery_urls:
            assert "epc" not in url.lower()

    async def test_gallery_excludes_epc_legacy_lightbox(self, fetcher: DetailFetcher) -> None:
        """Legacy data-lightbox="gallery" items with 'epc' in URL are filtered."""
        html = """<!DOCTYPE html><html><body>
        <a href="//imagescdn.openrent.co.uk/listings/999/o_photo1.JPG"
           data-lightbox="gallery"></a>
        <a href="//imagescdn.openrent.co.uk/listings/999/o_epc_graph.JPG"
           data-lightbox="gallery"></a>
        </body></html>"""
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(html)
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 1
        assert "epc" not in result.gallery_urls[0].lower()

    async def test_gallery_excludes_epc_cdn_fallback(self, fetcher: DetailFetcher) -> None:
        """CDN URL pattern fallback also filters EPC images."""
        html = """<!DOCTYPE html><html><body>
        <img src="//imagescdn.openrent.co.uk/listings/999/o_bedroom.jpg" />
        <img src="//imagescdn.openrent.co.uk/listings/999/o_epc_rating.jpg" />
        </body></html>"""
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_httpx_response(html)
        )
        prop = _make_property(PropertySource.OPENRENT)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 1
        assert "epc" not in result.gallery_urls[0].lower()


# ---------------------------------------------------------------------------
# OnTheMarket parsing
# ---------------------------------------------------------------------------


class TestOnTheMarketParsing:
    @pytest.fixture
    def fetcher(self) -> DetailFetcher:
        return DetailFetcher(max_gallery_images=10)

    @pytest.fixture
    def with_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "onthemarket_detail_with_floorplan.html").read_text()

    @pytest.fixture
    def no_floorplan(self, fixtures_path: Path) -> str:
        return (fixtures_path / "onthemarket_detail_no_floorplan.html").read_text()

    async def test_extracts_floorplan(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url == "https://media.onthemarket.com/floor/123.jpg"

    async def test_extracts_gallery(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 3

    async def test_extracts_description(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.description is not None
        assert "bright and modern" in result.description

    async def test_extracts_features(self, fetcher: DetailFetcher, with_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(with_floorplan)
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.features is not None
        assert "Chain free" in result.features

    async def test_no_floorplan(self, fetcher: DetailFetcher, no_floorplan: str) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(no_floorplan)
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.floorplan_url is None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2

    async def test_no_next_data_returns_none(self, fetcher: DetailFetcher) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response("<html><body>No data</body></html>")
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_http_error_returns_none(self, fetcher: DetailFetcher) -> None:
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response("", status_code=403)
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is None

    async def test_gallery_excludes_epc_images(self, fetcher: DetailFetcher) -> None:
        html = """<!DOCTYPE html><html><body>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"initialReduxState":{"property":{
            "floorplans":[],
            "images":[
                {"original":"https://media.onthemarket.com/img/photo1.jpg"},
                {"original":"https://media.onthemarket.com/epc/epc_chart.png"},
                {"original":"https://media.onthemarket.com/img/photo2.jpg"}
            ],
            "description":"A nice flat."
        }}}}
        </script></body></html>"""
        fetcher._curl_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_curl_response(html)
        )
        prop = _make_property(PropertySource.ONTHEMARKET)
        result = await fetcher.fetch_detail_page(prop)
        assert result is not None
        assert result.gallery_urls is not None
        assert len(result.gallery_urls) == 2
        for url in result.gallery_urls:
            assert "epc" not in url.lower()


# ---------------------------------------------------------------------------
# DetailPageData
# ---------------------------------------------------------------------------


class TestDetailPageData:
    def test_defaults_to_none(self) -> None:
        data = DetailPageData()
        assert data.floorplan_url is None
        assert data.gallery_urls is None
        assert data.description is None
        assert data.features is None

    def test_stores_values(self) -> None:
        data = DetailPageData(
            floorplan_url="https://example.com/floor.jpg",
            gallery_urls=["https://example.com/1.jpg"],
            description="Test",
            features=["Feature 1"],
        )
        assert data.floorplan_url == "https://example.com/floor.jpg"
        assert data.gallery_urls == ["https://example.com/1.jpg"]


# ---------------------------------------------------------------------------
# Close / cleanup
# ---------------------------------------------------------------------------


class TestDetailFetcherLifecycle:
    async def test_close_without_clients(self) -> None:
        fetcher = DetailFetcher()
        await fetcher.close()  # Should not raise

    async def test_close_with_clients(self) -> None:
        fetcher = DetailFetcher()
        # Simulate clients being created
        mock_client = AsyncMock()
        mock_session = AsyncMock()
        fetcher._client = mock_client
        fetcher._curl_session = mock_session

        await fetcher.close()

        mock_client.aclose.assert_awaited_once()
        mock_session.close.assert_awaited_once()
        assert fetcher._client is None
        assert fetcher._curl_session is None

    async def test_fetch_floorplan_url_delegates(self) -> None:
        fetcher = DetailFetcher()
        fetcher.fetch_detail_page = AsyncMock(  # type: ignore[method-assign]
            return_value=DetailPageData(floorplan_url="https://example.com/floor.jpg")
        )
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_floorplan_url(prop)
        assert result == "https://example.com/floor.jpg"

    async def test_fetch_floorplan_url_returns_none_on_failure(self) -> None:
        fetcher = DetailFetcher()
        fetcher.fetch_detail_page = AsyncMock(return_value=None)  # type: ignore[method-assign]
        prop = _make_property(PropertySource.RIGHTMOVE)
        result = await fetcher.fetch_floorplan_url(prop)
        assert result is None


# ---------------------------------------------------------------------------
# download_image_bytes
# ---------------------------------------------------------------------------


class TestDownloadImageBytes:
    async def test_uses_curl_for_zoocdn(self) -> None:
        fetcher = DetailFetcher()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"image-data"
        fetcher._curl_get_with_retry = AsyncMock(return_value=mock_resp)  # type: ignore[method-assign]

        result = await fetcher.download_image_bytes("https://lid.zoocdn.com/photo.jpg")
        assert result == b"image-data"
        fetcher._curl_get_with_retry.assert_awaited_once()

    async def test_uses_curl_for_onthemarket(self) -> None:
        fetcher = DetailFetcher()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"otm-data"
        fetcher._curl_get_with_retry = AsyncMock(return_value=mock_resp)  # type: ignore[method-assign]

        result = await fetcher.download_image_bytes("https://media.onthemarket.com/img.jpg")
        assert result == b"otm-data"

    async def test_uses_httpx_for_other_urls(self) -> None:
        fetcher = DetailFetcher()
        mock_resp = MagicMock()
        mock_resp.content = b"httpx-data"
        fetcher._httpx_get_with_retry = AsyncMock(return_value=mock_resp)  # type: ignore[method-assign]

        result = await fetcher.download_image_bytes("https://example.com/photo.jpg")
        assert result == b"httpx-data"

    async def test_curl_non_200_returns_none(self) -> None:
        fetcher = DetailFetcher()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        fetcher._curl_get_with_retry = AsyncMock(return_value=mock_resp)  # type: ignore[method-assign]

        result = await fetcher.download_image_bytes("https://lid.zoocdn.com/photo.jpg")
        assert result is None

    async def test_exception_returns_none(self) -> None:
        fetcher = DetailFetcher()
        fetcher._httpx_get_with_retry = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("Network error")
        )
        result = await fetcher.download_image_bytes("https://example.com/photo.jpg")
        assert result is None
