"""Tests for _download_missing_images() and _re_enrich_incomplete() in main.py."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.models import (
    MergedProperty,
    PropertyImage,
    PropertySource,
)
from home_finder.pipeline.analysis import _download_missing_images, _re_enrich_incomplete
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.utils.image_cache import (
    find_cached_file,
    get_cache_dir,
    get_cached_image_path,
    save_image_bytes,
    url_to_filename,
)


class TestDownloadMissingImages:
    """Tests for _download_missing_images()."""

    async def test_downloads_only_missing(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should only download images not already on disk."""
        data_dir = str(tmp_path)
        url1 = "https://example.com/img1.jpg"
        url2 = "https://example.com/img2.jpg"

        merged = make_merged_property(
            images=(
                PropertyImage(
                    url=HttpUrl(url1),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
                PropertyImage(
                    url=HttpUrl(url2),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )

        # Pre-cache img1 only
        normalized_url1 = str(HttpUrl(url1))
        path1 = get_cached_image_path(
            data_dir, merged.unique_id, normalized_url1, "gallery", 0
        )
        save_image_bytes(path1, b"fake")

        fetcher = DetailFetcher()
        mock_download = AsyncMock(return_value=b"fake")
        with patch.object(fetcher, "download_image_bytes", mock_download):
            downloaded = await _download_missing_images(
                merged, fetcher, data_dir, max_images=20
            )

        # Only img2 should be downloaded
        assert downloaded == 1
        assert mock_download.call_count == 1
        assert str(HttpUrl(url2)) in mock_download.call_args[0][0]

        # img2 should now be on disk
        assert find_cached_file(
            data_dir, merged.unique_id, str(HttpUrl(url2)), "gallery"
        ) is not None

    async def test_noop_when_all_cached(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should not download anything when all images are cached."""
        data_dir = str(tmp_path)
        url1 = "https://example.com/img1.jpg"

        merged = make_merged_property(
            images=(
                PropertyImage(
                    url=HttpUrl(url1),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )

        # Pre-cache img1
        normalized_url1 = str(HttpUrl(url1))
        path1 = get_cached_image_path(
            data_dir, merged.unique_id, normalized_url1, "gallery", 0
        )
        save_image_bytes(path1, b"fake")

        fetcher = DetailFetcher()
        mock_download = AsyncMock(return_value=b"fake")
        with patch.object(fetcher, "download_image_bytes", mock_download):
            downloaded = await _download_missing_images(
                merged, fetcher, data_dir, max_images=20
            )

        assert downloaded == 0
        mock_download.assert_not_called()

    async def test_failed_download_returns_zero(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Failed downloads should not count and not create files."""
        data_dir = str(tmp_path)
        url1 = "https://example.com/img1.jpg"

        merged = make_merged_property(
            images=(
                PropertyImage(
                    url=HttpUrl(url1),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )

        fetcher = DetailFetcher()
        mock_download = AsyncMock(return_value=None)  # download fails
        with patch.object(fetcher, "download_image_bytes", mock_download):
            downloaded = await _download_missing_images(
                merged, fetcher, data_dir, max_images=20
            )

        assert downloaded == 0
        assert find_cached_file(
            data_dir, merged.unique_id, str(HttpUrl(url1)), "gallery"
        ) is None

    async def test_respects_max_images_with_floorplan(
        self, tmp_path: Path, make_merged_property: Callable[..., MergedProperty]
    ) -> None:
        """Should reduce effective max by 1 when floorplan exists."""
        data_dir = str(tmp_path)
        urls = [f"https://example.com/img{i}.jpg" for i in range(5)]

        merged = make_merged_property(
            images=tuple(
                PropertyImage(
                    url=HttpUrl(u),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                )
                for u in urls
            ),
            floorplan=PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=PropertySource.OPENRENT,
                image_type="floorplan",
            ),
        )

        fetcher = DetailFetcher()
        mock_download = AsyncMock(return_value=b"fake")
        with patch.object(fetcher, "download_image_bytes", mock_download):
            # max_images=3, floorplan=True → effective_max=2, only first 2 checked
            downloaded = await _download_missing_images(
                merged, fetcher, data_dir, max_images=3
            )

        assert downloaded == 2
        assert mock_download.call_count == 2


class TestReEnrichIncomplete:
    """Tests for _re_enrich_incomplete()."""

    async def test_noop_when_all_caches_complete(
        self,
        tmp_path: Path,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Returns queue unchanged when all gallery images are cached."""
        data_dir = str(tmp_path)
        url1 = "https://example.com/img1.jpg"

        merged = make_merged_property(
            images=(
                PropertyImage(
                    url=HttpUrl(url1),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )

        # Cache the image
        normalized = str(HttpUrl(url1))
        cache_dir = get_cache_dir(data_dir, merged.unique_id)
        save_image_bytes(
            cache_dir / url_to_filename(normalized, "gallery", 0), b"fake"
        )

        settings = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id="123",
            database_path=str(tmp_path / "properties.db"),
            quality_filter_max_images=20,
        )
        mock_storage = AsyncMock()

        result = await _re_enrich_incomplete([merged], settings, mock_storage)

        assert len(result) == 1
        assert result[0] is merged  # same object — no processing

    async def test_downloads_missing_images(
        self,
        tmp_path: Path,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Should download missing images without re-scraping detail pages."""
        url1 = "https://example.com/img1.jpg"

        merged = make_merged_property(
            images=(
                PropertyImage(
                    url=HttpUrl(url1),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )

        # No images cached — incomplete
        settings = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id="123",
            database_path=str(tmp_path / "properties.db"),
            quality_filter_max_images=20,
        )
        mock_storage = AsyncMock()

        with patch("home_finder.pipeline.analysis.DetailFetcher") as mock_cls:
            mock_fetcher = AsyncMock()
            mock_fetcher.download_image_bytes = AsyncMock(return_value=b"fake")
            mock_fetcher.close = AsyncMock()

            async def _fetcher_aenter(*a):
                return mock_fetcher

            async def _fetcher_aexit(*a):
                await mock_fetcher.close()

            mock_fetcher.__aenter__ = _fetcher_aenter
            mock_fetcher.__aexit__ = _fetcher_aexit
            mock_cls.return_value = mock_fetcher

            result = await _re_enrich_incomplete(
                [merged], settings, mock_storage
            )

        # Queue returned unchanged (same list)
        assert len(result) == 1
        assert result[0] is merged

        # download_image_bytes called (not fetch_detail_page)
        mock_fetcher.download_image_bytes.assert_called_once()
        assert not hasattr(mock_fetcher, "fetch_detail_page") or (
            not mock_fetcher.fetch_detail_page.called
        )

        # Fetcher closed
        mock_fetcher.close.assert_awaited_once()

    async def test_only_downloads_for_incomplete_properties(
        self,
        tmp_path: Path,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """With mixed cache states, only incomplete properties trigger downloads."""
        url_a = "https://example.com/a.jpg"
        url_b = "https://example.com/b.jpg"

        complete = make_merged_property(
            source_id="complete-1",
            images=(
                PropertyImage(
                    url=HttpUrl(url_a),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )
        incomplete = make_merged_property(
            source_id="incomplete-1",
            images=(
                PropertyImage(
                    url=HttpUrl(url_b),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )

        # Cache only the first property's image
        normalized_a = str(HttpUrl(url_a))
        path_a = get_cached_image_path(
            str(tmp_path), complete.unique_id, normalized_a, "gallery", 0
        )
        save_image_bytes(path_a, b"fake")

        settings = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id="123",
            database_path=str(tmp_path / "properties.db"),
            quality_filter_max_images=20,
        )
        mock_storage = AsyncMock()

        with patch("home_finder.pipeline.analysis.DetailFetcher") as mock_cls:
            mock_fetcher = AsyncMock()
            mock_fetcher.download_image_bytes = AsyncMock(return_value=b"fake")
            mock_fetcher.close = AsyncMock()

            async def _fetcher_aenter(*a):
                return mock_fetcher

            async def _fetcher_aexit(*a):
                await mock_fetcher.close()

            mock_fetcher.__aenter__ = _fetcher_aenter
            mock_fetcher.__aexit__ = _fetcher_aexit
            mock_cls.return_value = mock_fetcher

            result = await _re_enrich_incomplete(
                [complete, incomplete], settings, mock_storage
            )

        # Both properties returned, same objects
        assert len(result) == 2
        assert result[0] is complete
        assert result[1] is incomplete

        # Only the incomplete property's image was downloaded
        mock_fetcher.download_image_bytes.assert_called_once()
        call_url = mock_fetcher.download_image_bytes.call_args[0][0]
        assert str(HttpUrl(url_b)) in call_url

        # Incomplete property's image now on disk
        assert find_cached_file(
            str(tmp_path), incomplete.unique_id, str(HttpUrl(url_b)), "gallery"
        ) is not None

    async def test_no_data_dir_returns_unchanged(
        self,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Without data_dir, returns queue unchanged."""
        merged = make_merged_property(
            images=(
                PropertyImage(
                    url=HttpUrl("https://example.com/img1.jpg"),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ),
        )

        # Mock settings with empty data_dir (can't happen with real Settings
        # since data_dir is derived from database_path, but tests the guard)
        mock_settings = AsyncMock()
        mock_settings.data_dir = ""
        mock_storage = AsyncMock()

        result = await _re_enrich_incomplete([merged], mock_settings, mock_storage)

        assert len(result) == 1
        assert result[0] is merged
