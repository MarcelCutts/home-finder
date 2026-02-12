"""Tests for image processing utilities."""

from io import BytesIO

import pytest
from PIL import Image

from home_finder.utils.image_processing import (
    MAX_IMAGE_DIMENSION,
    VALID_MEDIA_TYPES,
    is_valid_media_type,
    resize_image_bytes,
)


class TestIsValidMediaType:
    def test_jpeg_is_valid(self) -> None:
        assert is_valid_media_type("image/jpeg") is True

    def test_png_is_valid(self) -> None:
        assert is_valid_media_type("image/png") is True

    def test_gif_is_valid(self) -> None:
        assert is_valid_media_type("image/gif") is True

    def test_webp_is_valid(self) -> None:
        assert is_valid_media_type("image/webp") is True

    def test_svg_is_not_valid(self) -> None:
        assert is_valid_media_type("image/svg+xml") is False

    def test_text_is_not_valid(self) -> None:
        assert is_valid_media_type("text/html") is False

    def test_empty_is_not_valid(self) -> None:
        assert is_valid_media_type("") is False


class TestResizeImageBytes:
    def _make_image(self, width: int, height: int, fmt: str = "JPEG") -> bytes:
        img = Image.new("RGB", (width, height), color="red")
        buf = BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_small_image_unchanged(self) -> None:
        data = self._make_image(100, 100)
        result = resize_image_bytes(data)
        assert result == data

    def test_large_image_resized(self) -> None:
        data = self._make_image(3000, 2000)
        result = resize_image_bytes(data)
        assert result != data
        # Verify dimensions
        img = Image.open(BytesIO(result))
        assert max(img.size) <= MAX_IMAGE_DIMENSION

    def test_preserves_aspect_ratio(self) -> None:
        data = self._make_image(4000, 2000)
        result = resize_image_bytes(data)
        img = Image.open(BytesIO(result))
        w, h = img.size
        # Original ratio is 2:1
        assert abs(w / h - 2.0) < 0.05

    def test_exact_boundary_not_resized(self) -> None:
        data = self._make_image(MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION)
        result = resize_image_bytes(data)
        assert result == data

    def test_one_pixel_over_resized(self) -> None:
        data = self._make_image(MAX_IMAGE_DIMENSION + 1, 100)
        result = resize_image_bytes(data)
        img = Image.open(BytesIO(result))
        assert max(img.size) <= MAX_IMAGE_DIMENSION

    def test_png_format_preserved(self) -> None:
        data = self._make_image(3000, 2000, fmt="PNG")
        result = resize_image_bytes(data)
        img = Image.open(BytesIO(result))
        assert img.format == "PNG"

    def test_invalid_bytes_returns_original(self) -> None:
        bad_data = b"not an image"
        result = resize_image_bytes(bad_data)
        assert result == bad_data

    def test_custom_max_dim(self) -> None:
        data = self._make_image(500, 500)
        result = resize_image_bytes(data, max_dim=200)
        img = Image.open(BytesIO(result))
        assert max(img.size) <= 200


class TestConstants:
    def test_max_dimension_is_reasonable(self) -> None:
        assert 1000 <= MAX_IMAGE_DIMENSION <= 2000

    def test_valid_media_types_has_four(self) -> None:
        assert len(VALID_MEDIA_TYPES) == 4
