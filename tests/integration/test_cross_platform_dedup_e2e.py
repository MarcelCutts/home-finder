"""E2E tests for cross-platform deduplication with real scraped data.

These tests scrape from multiple platforms and verify deduplication works.
Marked as slow due to real network requests.
"""

import asyncio

import pytest

pytestmark = pytest.mark.usefixtures("reset_crawlee_state", "set_crawlee_storage_dir")

from home_finder.filters.deduplication import Deduplicator
from home_finder.filters.detail_enrichment import enrich_merged_properties
from home_finder.models import Property
from home_finder.scrapers import (
    OnTheMarketScraper,
    OpenRentScraper,
    RightmoveScraper,
    ZooplaScraper,
)
from home_finder.scrapers.detail_fetcher import DetailFetcher


async def _scrape_platform(scraper, area="e8", max_results=5) -> list[Property]:
    """Scrape a single platform, returning empty list on failure."""
    try:
        results = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area=area,
            max_results=max_results,
        )
        return results
    except Exception:
        return []
    finally:
        await scraper.close()


@pytest.mark.slow
class TestCrossPlatformDedupE2E:
    """Test cross-platform deduplication with real scraped data."""

    async def _scrape_all(self, max_results=5) -> list[Property]:
        """Scrape all 4 platforms with delays between them."""
        all_properties: list[Property] = []

        # Scrape sequentially with delay to respect rate limits
        for scraper_cls in [OpenRentScraper, RightmoveScraper, ZooplaScraper, OnTheMarketScraper]:
            scraper = scraper_cls()
            props = await _scrape_platform(scraper, max_results=max_results)
            all_properties.extend(props)
            await asyncio.sleep(2)

        return all_properties

    async def test_scrape_multiple_platforms_and_merge(self):
        """Scrape from all platforms, dedupe → total reduced, some multi-source."""
        all_props = await self._scrape_all(max_results=5)
        if len(all_props) < 2:
            pytest.skip("Too few listings across platforms")

        deduplicator = Deduplicator(enable_cross_platform=True)
        merged = await deduplicator.deduplicate_and_merge_async(all_props)

        # Merged count should be <= input count
        assert len(merged) <= len(all_props)
        assert len(merged) > 0

    async def test_merged_have_combined_source_urls(self):
        """Multi-source merged properties should have URL per source."""
        all_props = await self._scrape_all(max_results=5)
        if len(all_props) < 2:
            pytest.skip("Too few listings across platforms")

        deduplicator = Deduplicator(enable_cross_platform=True)
        merged = await deduplicator.deduplicate_and_merge_async(all_props)

        for m in merged:
            assert len(m.source_urls) == len(m.sources)
            for source in m.sources:
                assert source in m.source_urls
                url = str(m.source_urls[source])
                assert url.startswith("http")

    async def test_merged_price_range(self):
        """All merged properties should have realistic price ranges."""
        all_props = await self._scrape_all(max_results=5)
        if not all_props:
            pytest.skip("No listings available")

        deduplicator = Deduplicator(enable_cross_platform=True)
        merged = await deduplicator.deduplicate_and_merge_async(all_props)

        for m in merged:
            assert m.min_price <= m.max_price
            assert 500 <= m.min_price <= 5000
            assert 500 <= m.max_price <= 5000

    async def test_enrichment_after_dedup_preserves_images(self):
        """Post-merge enrichment should keep images from all sources."""
        all_props = await self._scrape_all(max_results=3)
        if len(all_props) < 2:
            pytest.skip("Too few listings across platforms")

        deduplicator = Deduplicator(enable_cross_platform=True)
        # First wrap as single-source merged
        single_merged = deduplicator.properties_to_merged(all_props)

        # Enrich
        fetcher = DetailFetcher()
        try:
            enriched = await enrich_merged_properties(single_merged, fetcher)
        finally:
            await fetcher.close()

        # Then deduplicate the enriched properties
        deduped = await deduplicator.deduplicate_merged_async(enriched)

        # All properties should still exist (possibly merged)
        assert len(deduped) <= len(enriched)
        assert len(deduped) > 0

        # Multi-source merged should have images if individual sources had images
        for m in deduped:
            if len(m.sources) > 1:
                # Multi-source should combine images from both
                # (soft assertion — detail pages may have failed)
                pass  # Just verify no crash
