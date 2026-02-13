"""Tests for detail enrichment pipeline step."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import HttpUrl

from home_finder.filters.detail_enrichment import enrich_merged_properties, filter_by_floorplan
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
from home_finder.utils.image_cache import get_cache_dir, save_image_bytes


def _make_property(
    source: PropertySource = PropertySource.RIGHTMOVE,
    source_id: str = "123",
    bedrooms: int = 2,
    price_pcm: int = 2000,
    postcode: str | None = "E8 3RH",
) -> Property:
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/{source.value}/{source_id}"),
        title=f"{bedrooms} bed flat",
        price_pcm=price_pcm,
        bedrooms=bedrooms,
        address="123 Test St, London",
        postcode=postcode,
    )


def _make_merged(
    canonical: Property | None = None,
    sources: tuple[PropertySource, ...] | None = None,
    source_urls: dict[PropertySource, HttpUrl] | None = None,
    floorplan: PropertyImage | None = None,
    images: tuple[PropertyImage, ...] = (),
) -> MergedProperty:
    if canonical is None:
        canonical = _make_property()
    if sources is None:
        sources = (canonical.source,)
    if source_urls is None:
        source_urls = {canonical.source: canonical.url}
    return MergedProperty(
        canonical=canonical,
        sources=sources,
        source_urls=source_urls,
        images=images,
        floorplan=floorplan,
        min_price=canonical.price_pcm,
        max_price=canonical.price_pcm,
    )


class TestEnrichMergedProperties:
    """Tests for enrich_merged_properties()."""

    async def test_populates_images_and_floorplan(self) -> None:
        """Should populate images and floorplan from detail page."""
        merged = _make_merged()
        detail_data = DetailPageData(
            floorplan_url="https://example.com/floor.jpg",
            gallery_urls=["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
            description="Nice flat",
            features=["Gas hob", "Garden"],
        )

        fetcher = DetailFetcher()
        with patch.object(
            fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
        ):
            result = await enrich_merged_properties([merged], fetcher)

        assert len(result.enriched) == 1
        assert len(result.failed) == 0
        enriched = result.enriched[0]
        assert enriched.floorplan is not None
        assert enriched.floorplan.image_type == "floorplan"
        assert len(enriched.images) == 2
        assert all(img.image_type == "gallery" for img in enriched.images)

    async def test_multi_source_collects_from_all(self) -> None:
        """Should collect images from all source URLs."""
        rm_prop = _make_property(source=PropertySource.RIGHTMOVE, source_id="rm1")
        zp_url = HttpUrl("https://zoopla.co.uk/to-rent/details/zp1")
        merged = _make_merged(
            canonical=rm_prop,
            sources=(PropertySource.RIGHTMOVE, PropertySource.ZOOPLA),
            source_urls={PropertySource.RIGHTMOVE: rm_prop.url, PropertySource.ZOOPLA: zp_url},
        )

        rm_detail = DetailPageData(gallery_urls=["https://example.com/rm1.jpg"])
        zp_detail = DetailPageData(
            gallery_urls=["https://example.com/zp1.jpg"],
            floorplan_url="https://example.com/zp_floor.jpg",
        )

        call_count = 0

        async def mock_fetch(prop: Property) -> DetailPageData:
            nonlocal call_count
            call_count += 1
            if prop.source == PropertySource.RIGHTMOVE:
                return rm_detail
            return zp_detail

        fetcher = DetailFetcher()
        with patch.object(fetcher, "fetch_detail_page", side_effect=mock_fetch):
            result = await enrich_merged_properties([merged], fetcher)

        assert call_count == 2
        enriched = result.enriched[0]
        assert len(enriched.images) == 2
        assert enriched.floorplan is not None
        assert enriched.floorplan.source == PropertySource.ZOOPLA

    async def test_skips_pdf_floorplan_keeps_image_floorplan(self) -> None:
        """Should skip PDF floorplans and keep image-format ones."""
        rm_prop = _make_property(source=PropertySource.RIGHTMOVE, source_id="rm1")
        zp_url = HttpUrl("https://zoopla.co.uk/to-rent/details/zp1")
        merged = _make_merged(
            canonical=rm_prop,
            sources=(PropertySource.RIGHTMOVE, PropertySource.ZOOPLA),
            source_urls={PropertySource.RIGHTMOVE: rm_prop.url, PropertySource.ZOOPLA: zp_url},
        )

        rm_detail = DetailPageData(floorplan_url="https://example.com/floor.pdf")
        zp_detail = DetailPageData(floorplan_url="https://example.com/floor.jpg")

        async def mock_fetch(prop: Property) -> DetailPageData:
            if prop.source == PropertySource.RIGHTMOVE:
                return rm_detail
            return zp_detail

        fetcher = DetailFetcher()
        with patch.object(fetcher, "fetch_detail_page", side_effect=mock_fetch):
            result = await enrich_merged_properties([merged], fetcher)

        enriched = result.enriched[0]
        assert enriched.floorplan is not None
        assert str(enriched.floorplan.url).endswith(".jpg")

    async def test_handles_fetch_failure(self) -> None:
        """Should place properties with no images into failed list."""
        merged = _make_merged()
        fetcher = DetailFetcher()
        with patch.object(fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=None):
            result = await enrich_merged_properties([merged], fetcher)

        assert len(result.enriched) == 0
        assert len(result.failed) == 1
        failed = result.failed[0]
        assert failed.floorplan is None
        assert len(failed.images) == 0

    async def test_skips_cached_property(self, tmp_path: Path) -> None:
        """Should skip enrichment for properties with cached images on disk."""
        merged = _make_merged()
        data_dir = str(tmp_path)

        # Pre-populate cache
        cache_dir = get_cache_dir(data_dir, merged.unique_id)
        cache_dir.mkdir(parents=True)
        save_image_bytes(cache_dir / "gallery_000_abc12345.jpg", b"fake")

        # Mock storage to return images from DB
        gallery_img = PropertyImage(
            url=HttpUrl("https://example.com/img1.jpg"),
            source=PropertySource.RIGHTMOVE,
            image_type="gallery",
        )
        floorplan_img = PropertyImage(
            url=HttpUrl("https://example.com/floor.jpg"),
            source=PropertySource.RIGHTMOVE,
            image_type="floorplan",
        )
        mock_storage = AsyncMock()
        mock_storage.get_property_images = AsyncMock(return_value=[gallery_img, floorplan_img])

        fetcher = DetailFetcher()
        mock_fetch = AsyncMock()
        with patch.object(fetcher, "fetch_detail_page", mock_fetch):
            result = await enrich_merged_properties(
                [merged], fetcher, data_dir=data_dir, storage=mock_storage
            )

        # Should NOT have called fetch_detail_page
        mock_fetch.assert_not_called()
        # Should have loaded images from storage (into enriched)
        assert len(result.enriched) == 1
        assert len(result.enriched[0].images) == 1
        assert result.enriched[0].floorplan is not None

    async def test_caches_downloaded_images(self, tmp_path: Path) -> None:
        """Should download and cache images when data_dir is set."""
        merged = _make_merged()
        data_dir = str(tmp_path)

        detail_data = DetailPageData(
            gallery_urls=["https://example.com/img1.jpg"],
            floorplan_url="https://example.com/floor.jpg",
        )

        fetcher = DetailFetcher()
        with (
            patch.object(
                fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
            ),
            patch.object(
                fetcher, "download_image_bytes", new_callable=AsyncMock, return_value=b"imgdata"
            ),
        ):
            result = await enrich_merged_properties([merged], fetcher, data_dir=data_dir)

        assert len(result.enriched) == 1
        # Verify images were cached to disk
        cache_dir = get_cache_dir(data_dir, merged.unique_id)
        cached_files = list(cache_dir.iterdir())
        assert len(cached_files) == 2  # 1 gallery + 1 floorplan


class TestFilterByFloorplan:
    """Tests for filter_by_floorplan()."""

    def test_drops_properties_without_floorplan(self) -> None:
        """Should drop properties that have no floorplan."""
        with_fp = _make_merged(
            floorplan=PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=PropertySource.RIGHTMOVE,
                image_type="floorplan",
            ),
        )
        without_fp = _make_merged(
            canonical=_make_property(source_id="456"),
        )

        result = filter_by_floorplan([with_fp, without_fp])
        assert len(result) == 1
        assert result[0].floorplan is not None

    def test_passes_all_when_all_have_floorplans(self) -> None:
        """Should pass all properties when all have floorplans."""
        props = [
            _make_merged(
                canonical=_make_property(source_id=str(i)),
                floorplan=PropertyImage(
                    url=HttpUrl(f"https://example.com/floor{i}.jpg"),
                    source=PropertySource.RIGHTMOVE,
                    image_type="floorplan",
                ),
            )
            for i in range(3)
        ]
        result = filter_by_floorplan(props)
        assert len(result) == 3

    def test_returns_empty_when_none_have_floorplans(self) -> None:
        """Should return empty list when no properties have floorplans."""
        props = [_make_merged(canonical=_make_property(source_id=str(i))) for i in range(3)]
        result = filter_by_floorplan(props)
        assert len(result) == 0

    def test_handles_empty_input(self) -> None:
        """Should handle empty input list."""
        result = filter_by_floorplan([])
        assert result == []
