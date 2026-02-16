"""Tests for image hashing utilities."""

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import imagehash
import pytest
from PIL import Image
from pydantic import HttpUrl

from home_finder.models import Property, PropertySource
from home_finder.utils.image_hash import (
    HASH_DISTANCE_THRESHOLD,
    count_gallery_hash_matches,
    fetch_and_hash_image,
    fetch_image_hashes_batch,
    hash_cached_gallery,
    hash_from_disk,
    hashes_match,
)


class TestHashesMatch:
    """Tests for hashes_match function."""

    def test_identical_hashes_match(self) -> None:
        h = str(imagehash.hex_to_hash("a" * 16))
        assert hashes_match(h, h)

    def test_none_inputs_do_not_match(self) -> None:
        assert not hashes_match(None, None)
        assert not hashes_match("a" * 16, None)
        assert not hashes_match(None, "a" * 16)

    def test_empty_strings_do_not_match(self) -> None:
        assert not hashes_match("", "a" * 16)
        assert not hashes_match("a" * 16, "")

    def test_similar_hashes_within_threshold(self) -> None:
        # Create two hashes that differ by exactly 1 bit
        h1 = imagehash.hex_to_hash("0000000000000000")
        h2 = imagehash.hex_to_hash("0000000000000001")
        assert (h1 - h2) == 1
        assert hashes_match(str(h1), str(h2))

    def test_different_hashes_beyond_threshold(self) -> None:
        # Create two hashes that differ by many bits
        h1 = imagehash.hex_to_hash("0000000000000000")
        h2 = imagehash.hex_to_hash("ffffffffffffffff")
        assert (h1 - h2) > HASH_DISTANCE_THRESHOLD
        assert not hashes_match(str(h1), str(h2))

    def test_hashes_at_exact_threshold(self) -> None:
        # Distance exactly at threshold should match
        h1 = imagehash.hex_to_hash("0000000000000000")
        # Flip exactly HASH_DISTANCE_THRESHOLD bits
        bits = 0
        for i in range(HASH_DISTANCE_THRESHOLD):
            bits |= 1 << i
        h2 = imagehash.hex_to_hash(f"{bits:016x}")
        assert (h1 - h2) == HASH_DISTANCE_THRESHOLD
        assert hashes_match(str(h1), str(h2))

    def test_hashes_one_beyond_threshold(self) -> None:
        # Distance one beyond threshold should not match
        h1 = imagehash.hex_to_hash("0000000000000000")
        bits = 0
        for i in range(HASH_DISTANCE_THRESHOLD + 1):
            bits |= 1 << i
        h2 = imagehash.hex_to_hash(f"{bits:016x}")
        assert (h1 - h2) == HASH_DISTANCE_THRESHOLD + 1
        assert not hashes_match(str(h1), str(h2))

    def test_invalid_hash_string_returns_false(self) -> None:
        assert not hashes_match("not_a_hash", "also_not")


class TestFetchAndHashImage:
    """Tests for fetch_and_hash_image with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        # Create a minimal valid image (1x1 red pixel PNG)
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 8), color="red").save(buf, format="PNG")
        png_bytes = buf.getvalue()

        httpx_mock.add_response(url="https://example.com/img.jpg", content=png_bytes)

        result = await fetch_and_hash_image("https://example.com/img.jpg")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) == 16  # 64-bit pHash = 16 hex chars

    @pytest.mark.asyncio
    async def test_protocol_relative_url(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 8), color="blue").save(buf, format="PNG")
        png_bytes = buf.getvalue()

        httpx_mock.add_response(url="https://cdn.example.com/img.jpg", content=png_bytes)

        result = await fetch_and_hash_image("//cdn.example.com/img.jpg")
        assert result is not None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        httpx_mock.add_response(url="https://example.com/missing.jpg", status_code=404)

        result = await fetch_and_hash_image("https://example.com/missing.jpg")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_image_returns_none(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        httpx_mock.add_response(url="https://example.com/bad.jpg", content=b"not an image")

        result = await fetch_and_hash_image("https://example.com/bad.jpg")
        assert result is None


class TestFetchImageHashesBatch:
    """Tests for fetch_image_hashes_batch with mocked HTTP."""

    def _make_property(self, uid: str, image_url: str | None = None) -> Property:
        return Property(
            source=PropertySource.OPENRENT,
            source_id=uid,
            url=HttpUrl(f"https://openrent.com/{uid}"),
            title=f"Property {uid}",
            price_pcm=1500,
            bedrooms=1,
            address="123 Test St",
            image_url=HttpUrl(image_url) if image_url else None,
        )

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self) -> None:
        result = await fetch_image_hashes_batch([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_properties_without_images_return_empty(self) -> None:
        props = [self._make_property("1"), self._make_property("2")]
        result = await fetch_image_hashes_batch(props)
        assert result == {}

    @pytest.mark.asyncio
    async def test_successful_hash_returned(self) -> None:
        props = [self._make_property("1", "https://example.com/img1.jpg")]

        with patch(
            "home_finder.utils.image_hash.fetch_and_hash_image",
            new_callable=AsyncMock,
            return_value="abcdef0123456789",
        ):
            result = await fetch_image_hashes_batch(props)

        assert result == {"openrent:1": "abcdef0123456789"}

    @pytest.mark.asyncio
    async def test_failed_hashes_excluded(self) -> None:
        props = [
            self._make_property("1", "https://example.com/img1.jpg"),
            self._make_property("2", "https://example.com/img2.jpg"),
        ]

        async def mock_fetch(url: str, **kwargs) -> str | None:  # type: ignore[no-untyped-def]
            if "img1" in url:
                return "abcdef0123456789"
            return None

        with patch(
            "home_finder.utils.image_hash.fetch_and_hash_image",
            side_effect=mock_fetch,
        ):
            result = await fetch_image_hashes_batch(props)

        assert "openrent:1" in result
        assert "openrent:2" not in result

    @pytest.mark.asyncio
    async def test_exception_in_fetch_excluded(self) -> None:
        props = [self._make_property("1", "https://example.com/img1.jpg")]

        with patch(
            "home_finder.utils.image_hash.fetch_and_hash_image",
            new_callable=AsyncMock,
            side_effect=Exception("network error"),
        ):
            result = await fetch_image_hashes_batch(props)

        assert result == {}


def _make_test_image(color: str = "red", size: tuple[int, int] = (8, 8)) -> bytes:
    """Create minimal PNG bytes for testing."""
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


class TestHashFromDisk:
    """Tests for hash_from_disk."""

    def test_valid_jpeg(self, tmp_path: Path) -> None:
        img_path = tmp_path / "gallery_000_abc12345.jpg"
        img_path.write_bytes(_make_test_image("red"))
        result = hash_from_disk(img_path)
        assert result is not None
        assert len(result) == 16  # 64-bit pHash = 16 hex chars

    def test_svg_extension_skipped(self, tmp_path: Path) -> None:
        img_path = tmp_path / "floorplan.svg"
        img_path.write_text("<svg>...</svg>")
        assert hash_from_disk(img_path) is None

    def test_svg_content_xml_prefix_skipped(self, tmp_path: Path) -> None:
        img_path = tmp_path / "gallery_000_abc12345.jpg"
        img_path.write_bytes(b"<?xml version='1.0'?><svg>...</svg>")
        assert hash_from_disk(img_path) is None

    def test_svg_content_svg_prefix_skipped(self, tmp_path: Path) -> None:
        img_path = tmp_path / "gallery_000_abc12345.jpg"
        img_path.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'>...</svg>")
        assert hash_from_disk(img_path) is None

    def test_corrupted_file_returns_none(self, tmp_path: Path) -> None:
        img_path = tmp_path / "gallery_000_abc12345.jpg"
        img_path.write_bytes(b"not an image at all")
        assert hash_from_disk(img_path) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        img_path = tmp_path / "nonexistent.jpg"
        assert hash_from_disk(img_path) is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        img_path = tmp_path / "gallery_000_abc12345.jpg"
        img_path.write_bytes(b"")
        assert hash_from_disk(img_path) is None

    def test_two_different_images_get_different_hashes(self, tmp_path: Path) -> None:
        path1 = tmp_path / "img1.jpg"
        path2 = tmp_path / "img2.jpg"
        # Create images with distinct patterns (solid colours produce identical pHash)
        img1 = Image.new("RGB", (64, 64), color="white")
        for x in range(32):
            for y in range(64):
                img1.putpixel((x, y), (0, 0, 0))
        img2 = Image.new("RGB", (64, 64), color="white")
        for x in range(64):
            for y in range(32):
                img2.putpixel((x, y), (0, 0, 0))
        buf1, buf2 = io.BytesIO(), io.BytesIO()
        img1.save(buf1, format="PNG")
        img2.save(buf2, format="PNG")
        path1.write_bytes(buf1.getvalue())
        path2.write_bytes(buf2.getvalue())

        h1 = hash_from_disk(path1)
        h2 = hash_from_disk(path2)
        assert h1 is not None
        assert h2 is not None
        assert h1 != h2


class TestHashCachedGallery:
    """Tests for hash_cached_gallery."""

    @staticmethod
    def _setup_gallery(tmp_path: Path, unique_id: str, files: dict[str, bytes]) -> None:
        """Create cached gallery files using the same path logic as production."""
        from home_finder.utils.image_cache import get_cache_dir

        cache_dir = get_cache_dir(str(tmp_path), unique_id)
        cache_dir.mkdir(parents=True, exist_ok=True)
        for filename, data in files.items():
            (cache_dir / filename).write_bytes(data)

    @pytest.mark.asyncio
    async def test_full_gallery_hashing(self, tmp_path: Path) -> None:
        self._setup_gallery(tmp_path, "openrent:123", {
            "gallery_000_abc12345.jpg": _make_test_image("red"),
            "gallery_001_def67890.jpg": _make_test_image("green"),
        })

        result = await hash_cached_gallery(["openrent:123"], str(tmp_path))
        assert "openrent:123" in result
        assert len(result["openrent:123"]) == 2

    @pytest.mark.asyncio
    async def test_missing_cache_dir(self, tmp_path: Path) -> None:
        result = await hash_cached_gallery(["openrent:999"], str(tmp_path))
        assert result == {}

    @pytest.mark.asyncio
    async def test_svg_files_skipped(self, tmp_path: Path) -> None:
        self._setup_gallery(tmp_path, "rightmove:456", {
            # SVG content in a .jpg file (Rightmove placeholder scenario)
            "gallery_000_abc12345.jpg": b"<?xml version='1.0'?><svg>placeholder</svg>",
            "gallery_001_def67890.jpg": _make_test_image("blue"),
        })

        result = await hash_cached_gallery(["rightmove:456"], str(tmp_path))
        assert "rightmove:456" in result
        assert len(result["rightmove:456"]) == 1  # SVG skipped

    @pytest.mark.asyncio
    async def test_all_svgs_excluded(self, tmp_path: Path) -> None:
        self._setup_gallery(tmp_path, "rightmove:789", {
            "gallery_000_abc12345.jpg": b"<?xml version='1.0'?><svg>a</svg>",
            "gallery_001_def67890.jpg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        })

        result = await hash_cached_gallery(["rightmove:789"], str(tmp_path))
        assert "rightmove:789" not in result

    @pytest.mark.asyncio
    async def test_empty_gallery_not_included(self, tmp_path: Path) -> None:
        self._setup_gallery(tmp_path, "openrent:123", {
            # Only non-gallery files
            "floorplan_000_abc12345.jpg": _make_test_image("red"),
        })

        result = await hash_cached_gallery(["openrent:123"], str(tmp_path))
        assert "openrent:123" not in result

    @pytest.mark.asyncio
    async def test_multiple_properties(self, tmp_path: Path) -> None:
        self._setup_gallery(tmp_path, "openrent:1", {
            "gallery_000_abc12345.jpg": _make_test_image("red"),
        })
        self._setup_gallery(tmp_path, "zoopla:2", {
            "gallery_000_abc12345.jpg": _make_test_image("blue"),
        })

        result = await hash_cached_gallery(["openrent:1", "zoopla:2"], str(tmp_path))
        assert "openrent:1" in result
        assert "zoopla:2" in result


class TestCountGalleryHashMatches:
    """Tests for count_gallery_hash_matches."""

    def test_matching_galleries(self) -> None:
        h = "a" * 16
        assert count_gallery_hash_matches([h], [h]) == 1

    def test_multiple_matches(self) -> None:
        h1 = "a" * 16
        h2 = "b" * 16
        assert count_gallery_hash_matches([h1, h2], [h2, h1]) == 2

    def test_no_overlap(self) -> None:
        h1 = "0000000000000000"
        h2 = "ffffffffffffffff"
        assert count_gallery_hash_matches([h1], [h2]) == 0

    def test_none_inputs(self) -> None:
        assert count_gallery_hash_matches(None, None) == 0
        assert count_gallery_hash_matches(["a" * 16], None) == 0
        assert count_gallery_hash_matches(None, ["a" * 16]) == 0

    def test_empty_lists(self) -> None:
        assert count_gallery_hash_matches([], ["a" * 16]) == 0
        assert count_gallery_hash_matches(["a" * 16], []) == 0

    def test_partial_overlap(self) -> None:
        h1 = "a" * 16
        h3 = "0000000000000000"
        h4 = "ffffffffffffffff"
        # h1 matches h1, h3 doesn't match h4
        assert count_gallery_hash_matches([h1, h3], [h1, h4]) == 1

    def test_no_double_counting(self) -> None:
        """Same hash in gallery1 twice should only match once against gallery2."""
        h = "a" * 16
        assert count_gallery_hash_matches([h, h], [h]) == 1
