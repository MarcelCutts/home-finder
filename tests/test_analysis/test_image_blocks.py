"""Cache-only image blocks for the visual request (PropertyQualityAnalyzer._build_image_block).

Detail enrichment caches every image; analysis reads that cache and never downloads.
"""

from pathlib import Path

import pytest
from PIL import Image

from home_finder.analysis.analyzer import PropertyQualityAnalyzer


def _cached_jpeg(path: Path) -> Path:
    Image.new("RGB", (10, 10), color="red").save(path, format="JPEG")
    return path


class TestBuildImageBlockCacheOnly:
    """Tests for cache-only image loading in _build_image_block."""

    async def test_returns_none_when_cached_path_is_none(self) -> None:
        """Should return None and not attempt download when no cached path."""
        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        result = await analyzer._build_image_block(
            "https://lid.zoocdn.com/u/1024/768/abc.jpg", cached_path=None
        )
        assert result is None

    async def test_returns_none_when_cached_image_corrupt(self, tmp_path: Path) -> None:
        """Should return None when cached image file is corrupt."""
        corrupt_file = tmp_path / "corrupt.jpg"
        corrupt_file.write_bytes(b"not-a-real-image")

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        result = await analyzer._build_image_block(
            "https://example.com/img.jpg", cached_path=corrupt_file
        )
        assert result is None

    async def test_returns_image_block_from_valid_cache(self, tmp_path: Path) -> None:
        """Should return ImageBlockParam when cached image is valid."""
        img_path = _cached_jpeg(tmp_path / "valid.jpg")

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        result = await analyzer._build_image_block(
            "https://example.com/photo.jpg", cached_path=img_path
        )
        assert result is not None
        assert result["type"] == "image"
        assert result["source"]["type"] == "base64"

    @pytest.mark.parametrize(
        "url",
        [
            "https://youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://vimeo.com/123456",
        ],
    )
    async def test_skips_video_urls(self, tmp_path: Path, url: str) -> None:
        """A video URL is rejected even when a decodable image is cached for it."""
        img_path = _cached_jpeg(tmp_path / "video-thumbnail.jpg")

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        assert await analyzer._build_image_block(url, cached_path=img_path) is None
        # The same cached file is accepted for an image URL.
        assert (
            await analyzer._build_image_block("https://example.com/photo.jpg", cached_path=img_path)
            is not None
        )

    async def test_jpeg_bytes_with_png_url_produces_jpeg_media_type(self, tmp_path: Path) -> None:
        """JPEG bytes served from a .png URL should be detected as image/jpeg."""
        # JPEG bytes under a .png filename
        img_path = _cached_jpeg(tmp_path / "misleading.png")

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        result = await analyzer._build_image_block(
            "https://example.com/photo.png", cached_path=img_path
        )
        assert result is not None
        assert result["source"]["media_type"] == "image/jpeg"
