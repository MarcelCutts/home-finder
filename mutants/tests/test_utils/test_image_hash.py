"""Tests for image hashing utilities."""

from unittest.mock import AsyncMock, patch

import imagehash
import pytest
from pydantic import HttpUrl

from home_finder.models import Property, PropertySource
from home_finder.utils.image_hash import (
    HASH_DISTANCE_THRESHOLD,
    fetch_and_hash_image,
    fetch_image_hashes_batch,
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
