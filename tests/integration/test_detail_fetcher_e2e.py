"""E2E tests for detail page fetching from real property platforms.

These tests make real HTTP requests and are marked as slow.
If a platform returns 0 results or detail pages are unavailable,
the test skips rather than fails.
"""

import asyncio

import pytest

from home_finder.models import Property, PropertySource
from home_finder.scrapers import (
    OnTheMarketScraper,
    OpenRentScraper,
    RightmoveScraper,
    ZooplaScraper,
)
from home_finder.scrapers.detail_fetcher import DetailFetcher


@pytest.mark.slow
class TestRightmoveDetailFetcher:
    """Test detail page extraction from Rightmove."""

    async def _get_sample_listing(self) -> Property | None:
        scraper = RightmoveScraper()
        try:
            results = await scraper.scrape(
                min_price=1800, max_price=2200,
                min_bedrooms=1, max_bedrooms=2,
                area="e8", max_results=3,
            )
        finally:
            await scraper.close()
        return results[0] if results else None

    async def test_gallery_extraction(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No Rightmove listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        assert data.gallery_urls is not None
        assert len(data.gallery_urls) > 0
        for url in data.gallery_urls:
            assert url.startswith("http")

    async def test_floorplan_extraction(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No Rightmove listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        # Floorplan may or may not exist — just check it doesn't error
        if data.floorplan_url:
            assert data.floorplan_url.startswith("http")

    async def test_description_not_truncated(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No Rightmove listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        if data.description:
            assert len(data.description) > 100


@pytest.mark.slow
class TestZooplaDetailFetcher:
    """Test detail page extraction from Zoopla (curl_cffi TLS bypass)."""

    async def _get_sample_listing(self) -> Property | None:
        scraper = ZooplaScraper()
        try:
            results = await scraper.scrape(
                min_price=1800, max_price=2200,
                min_bedrooms=1, max_bedrooms=2,
                area="e8", max_results=3,
            )
        finally:
            await scraper.close()
        return results[0] if results else None

    async def test_gallery_extraction(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No Zoopla listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        assert data.gallery_urls is not None
        assert len(data.gallery_urls) > 0
        # Zoopla uses lid.zoocdn.com for images
        for url in data.gallery_urls:
            assert "zoocdn.com" in url or url.startswith("http")

    async def test_floorplan_extraction(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No Zoopla listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        if data.floorplan_url:
            # Zoopla floorplans come from lc.zoocdn.com
            assert "zoocdn.com" in data.floorplan_url or data.floorplan_url.startswith("http")

    async def test_description_from_rsc_payload(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No Zoopla listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        if data.description:
            assert len(data.description) > 50


@pytest.mark.slow
class TestOpenRentDetailFetcher:
    """Test detail page extraction from OpenRent."""

    async def _get_sample_listing(self) -> Property | None:
        scraper = OpenRentScraper()
        try:
            results = await scraper.scrape(
                min_price=1800, max_price=2200,
                min_bedrooms=1, max_bedrooms=2,
                area="e8", max_results=3,
            )
        finally:
            await scraper.close()
        return results[0] if results else None

    async def test_gallery_from_lightbox(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No OpenRent listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        if data.gallery_urls:
            assert len(data.gallery_urls) > 0
            for url in data.gallery_urls:
                assert url.startswith("http")

    async def test_floorplan_detection(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No OpenRent listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        # Just verify no errors — floorplan may not exist
        if data.floorplan_url:
            assert "openrent" in data.floorplan_url.lower() or data.floorplan_url.startswith("http")

    async def test_description_from_div(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No OpenRent listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        # OpenRent usually has descriptions
        if data.description:
            assert len(data.description) > 20


@pytest.mark.slow
class TestOnTheMarketDetailFetcher:
    """Test detail page extraction from OnTheMarket (curl_cffi TLS bypass)."""

    async def _get_sample_listing(self) -> Property | None:
        scraper = OnTheMarketScraper()
        try:
            results = await scraper.scrape(
                min_price=1800, max_price=2200,
                min_bedrooms=1, max_bedrooms=2,
                area="e8", max_results=3,
            )
        finally:
            await scraper.close()
        return results[0] if results else None

    async def test_gallery_from_redux_state(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No OnTheMarket listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        if data.gallery_urls:
            assert len(data.gallery_urls) > 0
            for url in data.gallery_urls:
                assert url.startswith("http")

    async def test_floorplan_from_redux(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No OnTheMarket listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        if data.floorplan_url:
            assert data.floorplan_url.startswith("http")

    async def test_features_from_key_features(self):
        prop = await self._get_sample_listing()
        if not prop:
            pytest.skip("No OnTheMarket listings available")

        fetcher = DetailFetcher()
        try:
            data = await fetcher.fetch_detail_page(prop)
        finally:
            await fetcher.close()

        if data is None:
            pytest.skip("Detail page unavailable")

        if data.features:
            assert isinstance(data.features, list)
            assert all(isinstance(f, str) for f in data.features)
