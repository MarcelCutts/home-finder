"""Tests for detail enrichment pipeline step."""

from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw
from pydantic import HttpUrl

from home_finder.filters.detail_enrichment import (
    _detect_floorplan_in_gallery,
    _load_cached_property,
    enrich_merged_properties,
    filter_by_floorplan,
)
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertySource,
)
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
from home_finder.utils.image_cache import get_cache_dir, get_cached_image_path, save_image_bytes


class TestEnrichMergedProperties:
    """Tests for enrich_merged_properties()."""

    async def test_populates_images_and_floorplan(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should populate images and floorplan from detail page."""
        merged = make_merged_property()
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

    async def test_multi_source_collects_from_all(
        self, make_property: Callable[..., Property]
    ) -> None:
        """Should collect images from all source URLs."""
        rm_prop = make_property(source=PropertySource.RIGHTMOVE, source_id="rm1")
        zp_url = HttpUrl("https://zoopla.co.uk/to-rent/details/zp1")
        merged = MergedProperty(
            canonical=rm_prop,
            sources=(PropertySource.RIGHTMOVE, PropertySource.ZOOPLA),
            source_urls={PropertySource.RIGHTMOVE: rm_prop.url, PropertySource.ZOOPLA: zp_url},
            images=(),
            floorplan=None,
            min_price=rm_prop.price_pcm,
            max_price=rm_prop.price_pcm,
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

    async def test_skips_pdf_floorplan_keeps_image_floorplan(
        self, make_property: Callable[..., Property]
    ) -> None:
        """Should skip PDF floorplans and keep image-format ones."""
        rm_prop = make_property(source=PropertySource.RIGHTMOVE, source_id="rm1")
        zp_url = HttpUrl("https://zoopla.co.uk/to-rent/details/zp1")
        merged = MergedProperty(
            canonical=rm_prop,
            sources=(PropertySource.RIGHTMOVE, PropertySource.ZOOPLA),
            source_urls={PropertySource.RIGHTMOVE: rm_prop.url, PropertySource.ZOOPLA: zp_url},
            images=(),
            floorplan=None,
            min_price=rm_prop.price_pcm,
            max_price=rm_prop.price_pcm,
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

    async def test_handles_fetch_failure(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should place properties with no images into failed list."""
        merged = make_merged_property()
        fetcher = DetailFetcher()
        with patch.object(fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=None):
            result = await enrich_merged_properties([merged], fetcher)

        assert len(result.enriched) == 0
        assert len(result.failed) == 1
        failed = result.failed[0]
        assert failed.floorplan is None
        assert len(failed.images) == 0

    async def test_skips_cached_property(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should skip enrichment for properties with cached images on disk."""
        merged = make_merged_property()
        data_dir = str(tmp_path)

        # Pre-populate cache
        cache_dir = get_cache_dir(data_dir, merged.unique_id)
        cache_dir.mkdir(parents=True)
        save_image_bytes(cache_dir / "gallery_000_abc12345.jpg", b"fake")

        # Mock storage to return images and property row from DB
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
        mock_storage.get_property_images_and_row = AsyncMock(
            return_value=([gallery_img, floorplan_img], merged.canonical)
        )

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

    async def test_clears_stale_cache_and_reenriches(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should clear cache and re-enrich when DB has no image records for a cached property."""
        merged = make_merged_property()
        data_dir = str(tmp_path)

        # Pre-populate disk cache (simulates a previous run that cached but didn't save to DB)
        cache_dir = get_cache_dir(data_dir, merged.unique_id)
        cache_dir.mkdir(parents=True)
        save_image_bytes(cache_dir / "gallery_000_abc12345.jpg", b"fake")

        # Mock storage returns NO images (property never saved to DB)
        mock_storage = AsyncMock()
        mock_storage.get_property_images_and_row = AsyncMock(return_value=([], None))

        # Detail fetcher should be called after cache is cleared
        detail_data = DetailPageData(
            floorplan_url="https://example.com/floor.jpg",
            gallery_urls=["https://example.com/img1.jpg"],
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
            result = await enrich_merged_properties(
                [merged], fetcher, data_dir=data_dir, storage=mock_storage
            )

        # Should have re-enriched successfully
        assert len(result.enriched) == 1
        assert result.enriched[0].floorplan is not None
        assert len(result.enriched[0].images) == 1

    async def test_backfills_coordinates_from_detail_page(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should update canonical with lat/lon when missing and detail page has them."""
        merged = make_merged_property(postcode="E8", latitude=None, longitude=None)
        detail_data = DetailPageData(
            gallery_urls=["https://example.com/img1.jpg"],
            latitude=51.5465,
            longitude=-0.0553,
            postcode="E8 3RH",
        )

        fetcher = DetailFetcher()
        with patch.object(
            fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
        ):
            result = await enrich_merged_properties([merged], fetcher)

        enriched = result.enriched[0]
        assert enriched.canonical.latitude == 51.5465
        assert enriched.canonical.longitude == -0.0553
        assert enriched.canonical.postcode == "E8 3RH"

    async def test_does_not_overwrite_existing_coordinates(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should keep existing canonical coordinates if present."""
        merged = make_merged_property(latitude=51.0, longitude=-0.1)
        detail_data = DetailPageData(
            gallery_urls=["https://example.com/img1.jpg"],
            latitude=51.9999,
            longitude=-0.9999,
        )

        fetcher = DetailFetcher()
        with patch.object(
            fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
        ):
            result = await enrich_merged_properties([merged], fetcher)

        enriched = result.enriched[0]
        assert enriched.canonical.latitude == 51.0
        assert enriched.canonical.longitude == -0.1

    async def test_does_not_overwrite_full_postcode_with_another(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should not replace an existing full postcode."""
        merged = make_merged_property(postcode="E8 3RH")
        detail_data = DetailPageData(
            gallery_urls=["https://example.com/img1.jpg"],
            postcode="E8 9ZZ",
        )

        fetcher = DetailFetcher()
        with patch.object(
            fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
        ):
            result = await enrich_merged_properties([merged], fetcher)

        enriched = result.enriched[0]
        assert enriched.canonical.postcode == "E8 3RH"

    async def test_caches_downloaded_images(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should download and cache images when data_dir is set."""
        merged = make_merged_property()
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

    def test_drops_properties_without_floorplan(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should drop properties that have no floorplan."""
        with_fp = make_merged_property(
            sources=(PropertySource.RIGHTMOVE,),
            floorplan=PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=PropertySource.RIGHTMOVE,
                image_type="floorplan",
            ),
        )
        without_fp = make_merged_property(
            sources=(PropertySource.RIGHTMOVE,), source_id="456"
        )

        result = filter_by_floorplan([with_fp, without_fp])
        assert len(result) == 1
        assert result[0].floorplan is not None

    def test_passes_all_when_all_have_floorplans(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should pass all properties when all have floorplans."""
        props = [
            make_merged_property(
                source_id=str(i),
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

    def test_returns_empty_when_none_have_floorplans(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should return empty list when no properties have floorplans."""
        props = [
            make_merged_property(sources=(PropertySource.RIGHTMOVE,), source_id=str(i))
            for i in range(3)
        ]
        result = filter_by_floorplan(props)
        assert len(result) == 0

    def test_exempts_openrent_only_properties(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """OpenRent-only properties should pass without a floorplan."""
        openrent_prop = make_merged_property(
            sources=(PropertySource.OPENRENT,), source_id="111"
        )
        rightmove_no_fp = make_merged_property(
            sources=(PropertySource.RIGHTMOVE,), source_id="222"
        )
        result = filter_by_floorplan([openrent_prop, rightmove_no_fp])
        assert len(result) == 1
        assert result[0].canonical.source == PropertySource.OPENRENT

    def test_mixed_source_with_openrent_still_requires_floorplan(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Properties on OpenRent + another platform should still need a floorplan."""
        mixed = make_merged_property(
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA), source_id="333"
        )
        result = filter_by_floorplan([mixed])
        assert len(result) == 0

    def test_handles_empty_input(self) -> None:
        """Should handle empty input list."""
        result = filter_by_floorplan([])
        assert result == []


def _make_floorplan_bytes() -> bytes:
    """Create synthetic floorplan image bytes (black lines on white)."""
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 380, 280], outline="black", width=3)
    draw.line([(200, 20), (200, 280)], fill="black", width=2)
    draw.line([(20, 150), (200, 150)], fill="black", width=2)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_photo_bytes() -> bytes:
    """Create synthetic room photo image bytes (colorful)."""
    img = Image.new("RGB", (400, 300), (135, 206, 235))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 200, 400, 300], fill=(34, 139, 34))
    draw.rectangle([50, 100, 150, 200], fill=(139, 69, 19))
    draw.rectangle([200, 120, 250, 160], fill=(220, 20, 60))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestDetectFloorplanInGallery:
    """Tests for _detect_floorplan_in_gallery()."""

    def test_detects_floorplan_in_gallery(self, tmp_path: Path) -> None:
        """Should reclassify a floorplan image from gallery."""
        unique_id = "openrent:12345"
        data_dir = str(tmp_path)

        # Create gallery images: 2 photos + 1 floorplan (last)
        images = [
            PropertyImage(
                url=HttpUrl(f"https://example.com/img{i}.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            )
            for i in range(3)
        ]

        # Cache photo bytes for first two, floorplan for the last
        for idx, img in enumerate(images):
            path = get_cached_image_path(data_dir, unique_id, str(img.url), "gallery", idx)
            if idx < 2:
                save_image_bytes(path, _make_photo_bytes())
            else:
                save_image_bytes(path, _make_floorplan_bytes())

        floorplan, remaining, detected_idx = _detect_floorplan_in_gallery(
            images, unique_id, data_dir
        )

        assert floorplan is not None
        assert floorplan.image_type == "floorplan"
        assert str(floorplan.url) == "https://example.com/img2.jpg"
        assert detected_idx == 2
        assert len(remaining) == 2
        assert all(img.image_type == "gallery" for img in remaining)

    def test_no_floorplan_in_gallery(self, tmp_path: Path) -> None:
        """Should return None when no floorplan detected."""
        unique_id = "openrent:12345"
        data_dir = str(tmp_path)

        images = [
            PropertyImage(
                url=HttpUrl(f"https://example.com/img{i}.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            )
            for i in range(2)
        ]

        for idx, img in enumerate(images):
            path = get_cached_image_path(data_dir, unique_id, str(img.url), "gallery", idx)
            save_image_bytes(path, _make_photo_bytes())

        floorplan, remaining, detected_idx = _detect_floorplan_in_gallery(
            images, unique_id, data_dir
        )

        assert floorplan is None
        assert detected_idx == -1
        assert len(remaining) == 2

    def test_empty_gallery(self, tmp_path: Path) -> None:
        """Should handle empty image list."""
        floorplan, remaining, detected_idx = _detect_floorplan_in_gallery([], "id:1", str(tmp_path))
        assert floorplan is None
        assert detected_idx == -1
        assert remaining == []

    def test_missing_cache_files_skipped(self, tmp_path: Path) -> None:
        """Should skip images with no cached bytes on disk."""
        unique_id = "openrent:99999"
        data_dir = str(tmp_path)

        images = [
            PropertyImage(
                url=HttpUrl("https://example.com/img0.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            )
        ]
        # Don't cache anything — file won't exist

        floorplan, remaining, detected_idx = _detect_floorplan_in_gallery(
            images, unique_id, data_dir
        )

        assert floorplan is None
        assert detected_idx == -1
        assert len(remaining) == 1


class TestEnrichSingleFloorplanDetection:
    """Integration tests: _enrich_single detects floorplans via PIL heuristic."""

    async def test_detects_floorplan_when_detail_page_has_none(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """OpenRent property with no structural floorplan -> PIL detects one in gallery."""
        merged = make_merged_property(
            sources=(PropertySource.OPENRENT,), source_id="or1"
        )
        data_dir = str(tmp_path)

        # Detail page returns 3 gallery images, no floorplan
        detail_data = DetailPageData(
            gallery_urls=[
                "https://example.com/photo1.jpg",
                "https://example.com/photo2.jpg",
                "https://example.com/floorplan.jpg",  # actually a floorplan image
            ],
        )

        fetcher = DetailFetcher()

        # download_image_bytes returns photo for first two, floorplan for third
        async def mock_download(url: str) -> bytes:
            if "floorplan" in url:
                return _make_floorplan_bytes()
            return _make_photo_bytes()

        with (
            patch.object(
                fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
            ),
            patch.object(fetcher, "download_image_bytes", side_effect=mock_download),
        ):
            result = await enrich_merged_properties([merged], fetcher, data_dir=data_dir)

        assert len(result.enriched) == 1
        enriched = result.enriched[0]

        # Floorplan should be detected and separated from gallery
        assert enriched.floorplan is not None
        assert enriched.floorplan.image_type == "floorplan"
        assert "floorplan.jpg" in str(enriched.floorplan.url)

        # Gallery should have only the 2 photos remaining
        assert len(enriched.images) == 2
        assert all(img.image_type == "gallery" for img in enriched.images)

    async def test_recaches_detected_floorplan(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Detected floorplan should be copied to floorplan cache path."""
        merged = make_merged_property(
            sources=(PropertySource.OPENRENT,), source_id="or2"
        )
        data_dir = str(tmp_path)

        floorplan_bytes = _make_floorplan_bytes()
        detail_data = DetailPageData(
            gallery_urls=["https://example.com/img0.jpg"],
        )

        fetcher = DetailFetcher()
        with (
            patch.object(
                fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
            ),
            patch.object(
                fetcher,
                "download_image_bytes",
                new_callable=AsyncMock,
                return_value=floorplan_bytes,
            ),
        ):
            result = await enrich_merged_properties([merged], fetcher, data_dir=data_dir)

        enriched = result.enriched[0]
        assert enriched.floorplan is not None

        # Verify the floorplan cache file exists at the "floorplan" path
        fp_cache = get_cached_image_path(
            data_dir, merged.unique_id, str(enriched.floorplan.url), "floorplan", 0
        )
        assert fp_cache.is_file()
        assert fp_cache.read_bytes() == floorplan_bytes

    async def test_skips_detection_when_structural_floorplan_exists(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should not run PIL detection when detail page provides a floorplan."""
        merged = make_merged_property(
            sources=(PropertySource.OPENRENT,), source_id="or3"
        )
        data_dir = str(tmp_path)

        # Detail page returns both gallery images and a dedicated floorplan
        detail_data = DetailPageData(
            gallery_urls=["https://example.com/photo.jpg"],
            floorplan_url="https://example.com/real_floor.jpg",
        )

        fetcher = DetailFetcher()
        with (
            patch.object(
                fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
            ),
            patch.object(
                fetcher,
                "download_image_bytes",
                new_callable=AsyncMock,
                return_value=_make_floorplan_bytes(),
            ),
            patch(
                "home_finder.filters.detail_enrichment._detect_floorplan_in_gallery"
            ) as mock_detect,
        ):
            result = await enrich_merged_properties([merged], fetcher, data_dir=data_dir)

        # PIL detection should never be called — structural floorplan was found
        mock_detect.assert_not_called()
        enriched = result.enriched[0]
        assert enriched.floorplan is not None
        assert "real_floor.jpg" in str(enriched.floorplan.url)


class TestLoadCachedPropertyBackfillsCoords:
    """Tests for _load_cached_property coordinate backfill from DB."""

    async def test_backfills_coords_from_db(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should backfill coordinates from DB when in-memory canonical lacks them."""
        merged = make_merged_property(latitude=None, longitude=None, postcode="E8")

        # DB has a row with coordinates (from a previous enrichment)
        db_prop = merged.canonical.model_copy(
            update={"latitude": 51.5465, "longitude": -0.0553, "postcode": "E8 3RH"}
        )

        gallery_img = PropertyImage(
            url=HttpUrl("https://example.com/img1.jpg"),
            source=PropertySource.RIGHTMOVE,
            image_type="gallery",
        )
        mock_storage = AsyncMock()
        mock_storage.get_property_images_and_row = AsyncMock(
            return_value=([gallery_img], db_prop)
        )

        result = await _load_cached_property(merged, mock_storage)

        assert result.canonical.latitude == 51.5465
        assert result.canonical.longitude == -0.0553
        assert result.canonical.postcode == "E8 3RH"

    async def test_does_not_overwrite_existing_coords(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should not overwrite coordinates that already exist in canonical."""
        merged = make_merged_property(latitude=51.0, longitude=-0.1, postcode="E8 3RH")

        # DB has different coordinates
        db_prop = merged.canonical.model_copy(
            update={"latitude": 99.0, "longitude": -99.0}
        )

        mock_storage = AsyncMock()
        mock_storage.get_property_images_and_row = AsyncMock(
            return_value=([], db_prop)
        )

        result = await _load_cached_property(merged, mock_storage)

        assert result.canonical.latitude == 51.0
        assert result.canonical.longitude == -0.1

    async def test_backfills_full_postcode_over_outcode(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should backfill full postcode from DB when canonical only has outcode."""
        merged = make_merged_property(
            latitude=51.5465, longitude=-0.0553, postcode="E8"
        )

        db_prop = merged.canonical.model_copy(update={"postcode": "E8 3RH"})

        mock_storage = AsyncMock()
        mock_storage.get_property_images_and_row = AsyncMock(
            return_value=([], db_prop)
        )

        result = await _load_cached_property(merged, mock_storage)

        assert result.canonical.postcode == "E8 3RH"

    async def test_handles_no_db_row(
        self, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should work when DB has no row for the property."""
        merged = make_merged_property(latitude=None, longitude=None)

        mock_storage = AsyncMock()
        mock_storage.get_property_images_and_row = AsyncMock(
            return_value=([], None)
        )

        result = await _load_cached_property(merged, mock_storage)

        assert result.canonical.latitude is None
        assert result.canonical.longitude is None


class TestFloorplanUrlRejectedLogging:
    """Test that rejected floorplan URLs are logged."""

    async def test_floorplan_url_rejected_logs_warning(
        self,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """When is_valid_image_url rejects a floorplan, verify warning is logged."""
        merged = make_merged_property()
        detail_data = DetailPageData(
            floorplan_url="https://example.com/floor.svg",
            gallery_urls=["https://example.com/img1.jpg"],
        )

        fetcher = DetailFetcher()
        with (
            patch.object(
                fetcher, "fetch_detail_page", new_callable=AsyncMock, return_value=detail_data
            ),
            patch("home_finder.filters.detail_enrichment.logger") as mock_logger,
        ):
            result = await enrich_merged_properties([merged], fetcher)

        # Floorplan should be rejected
        enriched = result.enriched[0]
        assert enriched.floorplan is None

        # Warning should be logged via structlog
        mock_logger.warning.assert_any_call(
            "floorplan_url_rejected",
            property_id=merged.unique_id,
            url="https://example.com/floor.svg",
            reason="failed_image_url_validation",
        )
