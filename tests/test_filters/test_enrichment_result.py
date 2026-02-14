"""Tests for EnrichmentResult split behavior in detail enrichment."""

from unittest.mock import AsyncMock, patch

from pydantic import HttpUrl

from home_finder.filters.detail_enrichment import EnrichmentResult, enrich_merged_properties
from home_finder.models import MergedProperty, Property, PropertySource
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData


def _make_property(source_id: str = "123") -> Property:
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/rightmove/{source_id}"),
        title="Test flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test St",
        postcode="E8 3RH",
    )


def _make_merged(source_id: str = "123") -> MergedProperty:
    prop = _make_property(source_id)
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        images=(),
        floorplan=None,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


class TestEnrichmentResultSplit:
    """Tests that enrichment correctly splits into enriched/failed."""

    async def test_successful_enrichment_goes_to_enriched(self) -> None:
        """Property with images goes to enriched list."""
        merged = _make_merged()
        detail = DetailPageData(gallery_urls=["https://example.com/img.jpg"])

        fetcher = DetailFetcher()
        with patch.object(
            fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail
        ):
            result = await enrich_merged_properties([merged], fetcher)

        assert len(result.enriched) == 1
        assert len(result.failed) == 0

    async def test_failed_enrichment_goes_to_failed(self) -> None:
        """Property with no images goes to failed list."""
        merged = _make_merged()

        fetcher = DetailFetcher()
        with patch.object(fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=None):
            result = await enrich_merged_properties([merged], fetcher)

        assert len(result.enriched) == 0
        assert len(result.failed) == 1

    async def test_floorplan_only_counts_as_enriched(self) -> None:
        """Property with only floorplan (no gallery) is enriched."""
        merged = _make_merged()
        detail = DetailPageData(floorplan_url="https://example.com/floor.jpg")

        fetcher = DetailFetcher()
        with patch.object(
            fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail
        ):
            result = await enrich_merged_properties([merged], fetcher)

        assert len(result.enriched) == 1
        assert len(result.failed) == 0

    async def test_mixed_batch_splits_correctly(self) -> None:
        """Batch with successes and failures splits correctly."""
        success_merged = _make_merged("success-1")
        fail_merged = _make_merged("fail-1")

        success_detail = DetailPageData(gallery_urls=["https://example.com/img.jpg"])

        async def mock_fetch(prop: Property) -> DetailPageData | None:
            if prop.source_id == "success-1":
                return success_detail
            return None

        fetcher = DetailFetcher()
        with patch.object(fetcher, "fetch_detail_page", side_effect=mock_fetch):
            result = await enrich_merged_properties([success_merged, fail_merged], fetcher)

        assert len(result.enriched) == 1
        assert len(result.failed) == 1
        assert result.enriched[0].unique_id == success_merged.unique_id
        assert result.failed[0].unique_id == fail_merged.unique_id

    async def test_empty_gallery_goes_to_failed(self) -> None:
        """Property with empty gallery list (no actual images) goes to failed."""
        merged = _make_merged()
        detail = DetailPageData(gallery_urls=[], description="Some desc")

        fetcher = DetailFetcher()
        with patch.object(
            fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail
        ):
            result = await enrich_merged_properties([merged], fetcher)

        assert len(result.enriched) == 0
        assert len(result.failed) == 1
