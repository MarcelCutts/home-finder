"""Tests for floor area tracking across the pipeline.

Covers: per-platform extraction, quality model, DB round-trip,
dedup merge source priority, web display, fit score bonus.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db import PropertyStorage
from home_finder.filters.deduplication import Deduplicator
from home_finder.filters.fit_score import _score_hosting
from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
    SpaceAnalysis,
)
from home_finder.notifiers.telegram import _format_space_info
from home_finder.scrapers.detail_fetcher import (
    DetailFetcher,
    _zoopla_size_from_rsc,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_property(source: PropertySource, source_id: str = "test-1") -> Property:
    """Create minimal Property for detail fetching."""
    urls = {
        PropertySource.RIGHTMOVE: f"https://www.rightmove.co.uk/properties/{source_id}",
        PropertySource.ZOOPLA: f"https://www.zoopla.co.uk/to-rent/details/{source_id}",
        PropertySource.ONTHEMARKET: f"https://www.onthemarket.com/details/{source_id}",
        PropertySource.OPENRENT: f"https://www.openrent.com/property-to-rent/{source_id}",
    }
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(urls[source]),
        title="Test Property",
        price_pcm=1800,
        bedrooms=2,
        address="123 Test Street",
        postcode="E8 3RH",
    )


def _mock_httpx_response(html: str) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.status_code = 200
    resp.url = "https://example.com/property"
    return resp


def _mock_curl_response(html: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.status_code = status_code
    return resp


def _make_merged(
    prop: Property,
    floor_area_sqft: int | None = None,
    floor_area_source: str | None = None,
) -> MergedProperty:
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
        floor_area_sqft=floor_area_sqft,
        floor_area_source=floor_area_source,
    )


def _hosting_analysis(**overrides: object) -> dict[str, Any]:
    """Build a minimal analysis dict for hosting scorer testing."""
    base: dict[str, Any] = {
        "space": {},
        "light_space": {},
        "outdoor_space": {},
        "flooring_noise": {},
    }
    for key, val in overrides.items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            base[key].update(val)
        else:
            base[key] = val
    return base


# ── Detail Fetcher: Rightmove ──────────────────────────────────────────────


class TestRightmoveFloorArea:
    """Test floor area extraction from Rightmove PAGE_MODEL sizings."""

    @pytest.fixture
    def fetcher(self) -> DetailFetcher:
        return DetailFetcher(max_gallery_images=10)

    def _rightmove_html(self, sizings: list[dict[str, Any]]) -> str:
        """Build minimal Rightmove HTML with PAGE_MODEL containing sizings."""
        page_model = {
            "propertyData": {
                "images": [],
                "floorplans": [],
                "text": {"description": "A flat."},
                "sizings": sizings,
            }
        }
        return f"""<!DOCTYPE html><html><body>
        <script>window.PAGE_MODEL = {json.dumps(page_model)}</script>
        </body></html>"""

    async def test_extracts_sqft_from_sizings(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([{"unit": "sqft", "minimumSize": 650, "maximumSize": 700}])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft == 700
        assert result.floor_area_source == "rightmove"

    async def test_prefers_maximum_size(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([{"unit": "sqft", "minimumSize": 500, "maximumSize": 800}])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft == 800

    async def test_falls_back_to_minimum_size(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([{"unit": "sqft", "minimumSize": 600}])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft == 600

    async def test_ignores_non_sqft_units(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([{"unit": "sqm", "minimumSize": 60}])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft is None

    async def test_rejects_below_min_bound(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([{"unit": "sqft", "minimumSize": 99}])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft is None

    async def test_rejects_above_max_bound(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([{"unit": "sqft", "minimumSize": 5001}])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft is None

    async def test_accepts_boundary_values(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([{"unit": "sqft", "minimumSize": 100}])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft == 100

    async def test_no_sizings_returns_none(self, fetcher: DetailFetcher) -> None:
        html = self._rightmove_html([])
        fetcher._httpx_get_with_retry = AsyncMock(return_value=_mock_httpx_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.RIGHTMOVE))
        assert result is not None
        assert result.floor_area_sqft is None
        assert result.floor_area_source is None


# ── Detail Fetcher: Zoopla ─────────────────────────────────────────────────


class TestZooplaFloorArea:
    """Test floor area extraction from Zoopla RSC payload."""

    def test_zoopla_size_from_rsc_extracts_value(self) -> None:
        """Test the _zoopla_size_from_rsc helper directly with synthetic RSC payload."""
        # Build a minimal RSC chunk with sizeSqft
        rsc_payload = json.dumps({"sizeSqft": 750, "sizeSource": "agent"})
        rsc_chunk = f"1:{rsc_payload}"
        html = f"""<!DOCTYPE html><html><body>
        <script>self.__next_f.push([1, {json.dumps(rsc_chunk)}])</script>
        </body></html>"""
        result = _zoopla_size_from_rsc(html)
        assert result == 750

    def test_zoopla_size_from_rsc_no_size(self) -> None:
        html = """<!DOCTYPE html><html><body>
        <script>self.__next_f.push([1, "1:{}\\"otherField\\":42}"])</script>
        </body></html>"""
        result = _zoopla_size_from_rsc(html)
        assert result is None

    def test_zoopla_size_from_rsc_rejects_too_small(self) -> None:
        rsc_payload = json.dumps({"sizeSqft": 50})
        rsc_chunk = f"1:{rsc_payload}"
        html = f"""<!DOCTYPE html><html><body>
        <script>self.__next_f.push([1, {json.dumps(rsc_chunk)}])</script>
        </body></html>"""
        result = _zoopla_size_from_rsc(html)
        assert result is None

    def test_zoopla_size_from_rsc_rejects_too_large(self) -> None:
        rsc_payload = json.dumps({"sizeSqft": 6000})
        rsc_chunk = f"1:{rsc_payload}"
        html = f"""<!DOCTYPE html><html><body>
        <script>self.__next_f.push([1, {json.dumps(rsc_chunk)}])</script>
        </body></html>"""
        result = _zoopla_size_from_rsc(html)
        assert result is None


# ── Detail Fetcher: OnTheMarket ────────────────────────────────────────────


class TestOnTheMarketFloorArea:
    """Test floor area extraction from OTM __NEXT_DATA__ Redux state."""

    @pytest.fixture
    def fetcher(self) -> DetailFetcher:
        return DetailFetcher(max_gallery_images=10)

    def _otm_html(self, property_data: dict[str, Any]) -> str:
        """Build minimal OTM HTML with __NEXT_DATA__."""
        base = {
            "floorplans": [],
            "images": [],
            "description": "A flat.",
            "keyFeatures": [],
            "features": [],
        }
        base.update(property_data)
        next_data = {"props": {"initialReduxState": {"property": base}}}
        return f"""<!DOCTYPE html><html><body>
        <script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>
        </body></html>"""

    async def test_extracts_minimum_area_sqft(self, fetcher: DetailFetcher) -> None:
        html = self._otm_html({"minimumAreaSqFt": 850, "minimumAreaSqM": 79})
        fetcher._curl_get_with_retry = AsyncMock(return_value=_mock_curl_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.ONTHEMARKET))
        assert result is not None
        assert result.floor_area_sqft == 850
        assert result.floor_area_source == "onthemarket"

    async def test_rejects_below_min(self, fetcher: DetailFetcher) -> None:
        html = self._otm_html({"minimumAreaSqFt": 50})
        fetcher._curl_get_with_retry = AsyncMock(return_value=_mock_curl_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.ONTHEMARKET))
        assert result is not None
        assert result.floor_area_sqft is None

    async def test_rejects_above_max(self, fetcher: DetailFetcher) -> None:
        html = self._otm_html({"minimumAreaSqFt": 5001})
        fetcher._curl_get_with_retry = AsyncMock(return_value=_mock_curl_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.ONTHEMARKET))
        assert result is not None
        assert result.floor_area_sqft is None

    async def test_no_area_returns_none(self, fetcher: DetailFetcher) -> None:
        html = self._otm_html({})
        fetcher._curl_get_with_retry = AsyncMock(return_value=_mock_curl_response(html))
        result = await fetcher.fetch_detail_page(_make_property(PropertySource.ONTHEMARKET))
        assert result is not None
        assert result.floor_area_sqft is None
        assert result.floor_area_source is None


# ── Quality Model ──────────────────────────────────────────────────────────


class TestSpaceAnalysisTotalArea:
    """Test total_area_sqm field on SpaceAnalysis model."""

    def test_default_is_none(self) -> None:
        space = SpaceAnalysis(living_room_sqm=20.0)
        assert space.total_area_sqm is None

    def test_accepts_value(self) -> None:
        space = SpaceAnalysis(living_room_sqm=20.0, total_area_sqm=65.0)
        assert space.total_area_sqm == 65.0

    def test_serializes_to_dict(self) -> None:
        space = SpaceAnalysis(living_room_sqm=20.0, total_area_sqm=65.0)
        d = space.model_dump()
        assert d["total_area_sqm"] == 65.0

    def test_none_serializes(self) -> None:
        space = SpaceAnalysis(living_room_sqm=20.0)
        d = space.model_dump()
        assert d["total_area_sqm"] is None


# ── DB Round-Trip ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


class TestDBFloorAreaRoundTrip:
    """Test that floor_area_sqft/floor_area_source survive save+load."""

    def _make_merged_with_area(
        self,
        make_property: Callable[..., Property],
        floor_area_sqft: int | None = None,
        floor_area_source: str | None = None,
    ) -> MergedProperty:
        prop = make_property()
        return MergedProperty(
            canonical=prop,
            sources=(prop.source,),
            source_urls={prop.source: prop.url},
            min_price=prop.price_pcm,
            max_price=prop.price_pcm,
            floor_area_sqft=floor_area_sqft,
            floor_area_source=floor_area_source,
        )

    @pytest.mark.asyncio
    async def test_saves_and_loads_floor_area(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        merged = self._make_merged_with_area(make_property, 750, "zoopla")
        await storage.save_merged_property(merged)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqft, floor_area_source FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqft"] == 750
        assert row["floor_area_source"] == "zoopla"

    @pytest.mark.asyncio
    async def test_null_floor_area(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        merged = self._make_merged_with_area(make_property)
        await storage.save_merged_property(merged)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqft, floor_area_source FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqft"] is None
        assert row["floor_area_source"] is None

    @pytest.mark.asyncio
    async def test_floor_area_in_paginated_results(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        # Must have image_url to pass the "has images" filter in paginated query
        prop = make_property(image_url=HttpUrl("https://example.com/img.jpg"))
        merged = MergedProperty(
            canonical=prop,
            sources=(prop.source,),
            source_urls={prop.source: prop.url},
            min_price=prop.price_pcm,
            max_price=prop.price_pcm,
            floor_area_sqft=650,
            floor_area_source="rightmove",
        )
        await storage.save_merged_property(merged)

        from home_finder.web.filters import PropertyFilter

        properties, total = await storage.get_properties_paginated(
            PropertyFilter(), sort="newest", page=1, per_page=10
        )
        assert total == 1
        result_prop = properties[0]
        assert result_prop["floor_area_sqft"] == 650
        assert result_prop["floor_area_source"] == "rightmove"


# ── Dedup Merge Source Priority ────────────────────────────────────────────


class TestDedupFloorAreaMerge:
    """Test floor area source priority during cross-platform dedup merge."""

    def test_zoopla_beats_rightmove(self) -> None:
        """Zoopla floor area (priority 3) should be preferred over Rightmove (priority 1)."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        rm_prop = _make_property(PropertySource.RIGHTMOVE, "rm-1")
        zp_prop = _make_property(PropertySource.ZOOPLA, "zp-1")

        rm_merged = MergedProperty(
            canonical=rm_prop,
            sources=(PropertySource.RIGHTMOVE,),
            source_urls={PropertySource.RIGHTMOVE: rm_prop.url},
            min_price=1800,
            max_price=1800,
            floor_area_sqft=680,
            floor_area_source="rightmove",
        )
        zp_merged = MergedProperty(
            canonical=zp_prop,
            sources=(PropertySource.ZOOPLA,),
            source_urls={PropertySource.ZOOPLA: zp_prop.url},
            min_price=1800,
            max_price=1800,
            floor_area_sqft=700,
            floor_area_source="zoopla",
        )

        result = deduplicator._merge_merged_properties([rm_merged, zp_merged])
        assert result.floor_area_sqft == 700
        assert result.floor_area_source == "zoopla"

    def test_otm_beats_rightmove(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)
        rm_prop = _make_property(PropertySource.RIGHTMOVE, "rm-2")
        otm_prop = _make_property(PropertySource.ONTHEMARKET, "otm-2")

        rm_merged = _make_merged(rm_prop, floor_area_sqft=680, floor_area_source="rightmove")
        otm_merged = _make_merged(otm_prop, floor_area_sqft=690, floor_area_source="onthemarket")

        result = deduplicator._merge_merged_properties([rm_merged, otm_merged])
        assert result.floor_area_sqft == 690
        assert result.floor_area_source == "onthemarket"

    def test_keeps_only_value_when_one_has_area(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)
        prop_a = _make_property(PropertySource.OPENRENT, "or-1")
        prop_b = _make_property(PropertySource.ZOOPLA, "zp-2")

        a_merged = _make_merged(prop_a)  # No floor area
        b_merged = _make_merged(prop_b, floor_area_sqft=550, floor_area_source="zoopla")

        result = deduplicator._merge_merged_properties([a_merged, b_merged])
        assert result.floor_area_sqft == 550
        assert result.floor_area_source == "zoopla"

    def test_no_floor_area_stays_none(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)
        prop_a = _make_property(PropertySource.OPENRENT, "or-2")
        prop_b = _make_property(PropertySource.RIGHTMOVE, "rm-3")

        a_merged = _make_merged(prop_a)
        b_merged = _make_merged(prop_b)

        result = deduplicator._merge_merged_properties([a_merged, b_merged])
        assert result.floor_area_sqft is None
        assert result.floor_area_source is None


# ── Fit Score: Total Area Bonus ────────────────────────────────────────────


class TestFitScoreFloorArea:
    """Test total_area_sqm bonus in _score_hosting()."""

    def test_no_total_area_no_bonus(self) -> None:
        result = _score_hosting(_hosting_analysis(space={}), 2)
        assert result.score == 0

    def test_total_area_below_30sqm_zero_bonus(self) -> None:
        result = _score_hosting(_hosting_analysis(space={"total_area_sqm": 25}), 2)
        assert result.score == 0
        assert any("Small total area" in f["label"] for f in result.factors)

    def test_total_area_at_30sqm_zero_bonus(self) -> None:
        result = _score_hosting(_hosting_analysis(space={"total_area_sqm": 30}), 2)
        assert result.score == 0

    def test_total_area_at_55sqm_full_bonus(self) -> None:
        result = _score_hosting(_hosting_analysis(space={"total_area_sqm": 55}), 2)
        assert result.score == 15
        assert any("Good total area" in f["label"] for f in result.factors)

    def test_total_area_graduated_midpoint(self) -> None:
        """At 42.5sqm (midpoint): 12.5/25 * 15 = 7.5 points."""
        result = _score_hosting(_hosting_analysis(space={"total_area_sqm": 42.5}), 2)
        assert result.score == 7.5

    def test_total_area_above_55sqm_capped(self) -> None:
        """Above 55sqm still max 15 points."""
        result = _score_hosting(_hosting_analysis(space={"total_area_sqm": 100}), 2)
        assert result.score == 15

    def test_total_area_combined_with_living_sqm(self) -> None:
        """Both total_area_sqm and living_room_sqm contribute."""
        analysis = _hosting_analysis(
            space={"total_area_sqm": 55, "living_room_sqm": 25}  # +15  # +30
        )
        result = _score_hosting(analysis, 2)
        assert result.score == 45  # 15 + 30

    def test_none_total_area_no_effect(self) -> None:
        analysis = _hosting_analysis(space={"total_area_sqm": None, "living_room_sqm": 20})
        result = _score_hosting(analysis, 2)
        # Only living_room_sqm contributes: (20-10)*(30/15) = 20
        assert result.score == 20

    def test_string_total_area_ignored(self) -> None:
        """Non-numeric total_area_sqm should be safely ignored."""
        analysis = _hosting_analysis(space={"total_area_sqm": "unknown"})
        result = _score_hosting(analysis, 2)
        assert result.score == 0


# ── Telegram: Space Info Formatting ────────────────────────────────────────


class TestTelegramFloorArea:
    """Test floor area in Telegram space info formatting."""

    def test_with_scraped_floor_area(self, sample_quality_analysis: Any) -> None:
        result = _format_space_info(sample_quality_analysis, floor_area_sqft=700)
        assert "700 ft²" in result
        assert "65m²" in result  # 700 * 0.0929 ≈ 65

    def test_with_claude_estimate(self, make_quality_analysis: Callable) -> None:
        qa = make_quality_analysis(space=SpaceAnalysis(living_room_sqm=20.0, total_area_sqm=60.0))
        result = _format_space_info(qa)
        assert "~645 ft²" in result or "~60m²" in result

    def test_without_any_area(self, make_quality_analysis: Callable) -> None:
        qa = make_quality_analysis(space=SpaceAnalysis(living_room_sqm=None))
        result = _format_space_info(qa)
        assert result == "Size unknown"

    def test_scraped_preferred_over_estimate(self, make_quality_analysis: Callable) -> None:
        """When both scraped and estimated are available, scraped wins."""
        qa = make_quality_analysis(space=SpaceAnalysis(living_room_sqm=20.0, total_area_sqm=60.0))
        result = _format_space_info(qa, floor_area_sqft=750)
        assert "750 ft²" in result
        # Should NOT show ~645 ft² (the estimate)
        assert "~645" not in result


# ── Web Display ────────────────────────────────────────────────────────────


class TestWebFloorAreaDisplay:
    """Test floor area badge visibility in web dashboard."""

    @pytest_asyncio.fixture
    async def web_storage(self) -> AsyncGenerator[PropertyStorage, None]:
        s = PropertyStorage(":memory:")
        await s.initialize()
        yield s
        await s.close()

    @pytest.fixture
    def app(self, web_storage: PropertyStorage) -> Any:
        from fastapi import FastAPI
        from pydantic import SecretStr

        from home_finder.config import Settings
        from home_finder.web.routes import router

        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas="e8",
            database_path=":memory:",
        )
        app = FastAPI()
        app.state.storage = web_storage
        app.state.settings = settings
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app: Any) -> Any:
        from starlette.testclient import TestClient

        return TestClient(app)

    def _make_merged_with_area(
        self,
        make_property: Callable[..., Property],
        floor_area_sqft: int | None = None,
        floor_area_source: str | None = None,
    ) -> MergedProperty:
        prop = make_property(
            image_url=HttpUrl("https://example.com/img.jpg"),
            latitude=51.5465,
            longitude=-0.0553,
        )
        return MergedProperty(
            canonical=prop,
            sources=(prop.source,),
            source_urls={prop.source: prop.url},
            min_price=prop.price_pcm,
            max_price=prop.price_pcm,
            floor_area_sqft=floor_area_sqft,
            floor_area_source=floor_area_source,
        )

    @pytest.mark.asyncio
    async def test_card_shows_sqft_badge(
        self,
        client: Any,
        web_storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        merged = self._make_merged_with_area(make_property, 650, "zoopla")
        await web_storage.save_merged_property(merged)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "650 ft²" in resp.text

    @pytest.mark.asyncio
    async def test_card_hides_sqft_when_absent(
        self,
        client: Any,
        web_storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        merged = self._make_merged_with_area(make_property)
        await web_storage.save_merged_property(merged)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "ft²" not in resp.text
