"""Tests for image processing utilities."""

from io import BytesIO

import pytest
from PIL import Image

from home_finder.utils.image_processing import (
    MAX_IMAGE_DIMENSION,
    VALID_MEDIA_TYPES,
    detect_media_type_from_bytes,
    is_valid_media_type,
    resize_image_bytes,
)
from tests.support.images import transparent_plan


class TestTransparentFloorplans:
    @pytest.mark.parametrize("mode", ["RGBA", "LA", "P", "RGB"])
    @pytest.mark.parametrize("size", [(100, 100), (3000, 2000)])
    def test_white_background_and_black_ink_before_and_after_resize(self, mode, size):
        result = resize_image_bytes(transparent_plan(mode, size))
        with Image.open(BytesIO(result)) as img:
            assert img.format == "PNG"  # Retain lossless line art.
            assert img.mode == "RGB"
            if size == (100, 100):
                assert img.size == size
            else:
                assert MAX_IMAGE_DIMENSION - 1 <= max(img.size) <= MAX_IMAGE_DIMENSION
                assert img.width / img.height == pytest.approx(size[0] / size[1], abs=0.002)
            assert img.getpixel((5, 5)) == (0, 0, 0)
            assert img.getpixel((img.width - 5, 5)) == (255, 255, 255)
            assert "transparency" not in img.info
        assert resize_image_bytes(result) == result

    @pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
    def test_partial_alpha_is_composited_not_discarded(self, mode):
        if mode == "P":
            img = Image.new("P", (10, 10), 0)
            img.putpalette([0, 0, 0] * 256)
            img.info["transparency"] = bytes([128] * 256)
        else:
            img = Image.new(mode, (10, 10), (0, 128) if mode == "LA" else (0, 0, 0, 128))
        buf = BytesIO()
        img.save(buf, format="PNG")
        with Image.open(BytesIO(resize_image_bytes(buf.getvalue()))) as result:
            assert result.mode == "RGB"
            assert result.getpixel((0, 0)) == (127, 127, 127)

    def test_fully_opaque_alpha_keeps_original_bytes(self):
        buf = BytesIO()
        Image.new("RGBA", (100, 100), (200, 10, 10, 255)).save(buf, format="PNG")
        assert resize_image_bytes(buf.getvalue()) == buf.getvalue()

    @pytest.mark.parametrize("fmt", ["GIF", "WEBP"])
    def test_static_transparent_formats_keep_mime_and_ink(self, fmt):
        img = Image.open(BytesIO(transparent_plan("RGBA")))
        buf = BytesIO()
        img.save(buf, format=fmt, lossless=True)
        data = resize_image_bytes(buf.getvalue())
        assert detect_media_type_from_bytes(data) == f"image/{fmt.lower()}"
        with Image.open(BytesIO(data)) as result:
            assert "transparency" not in result.info and "A" not in result.getbands()
            rgb = result.convert("RGB")
            assert rgb.getpixel((10, 50)) == (0, 0, 0)
            assert rgb.getpixel((90, 50)) == (255, 255, 255)

    @pytest.mark.parametrize("fmt", ["PNG", "GIF", "WEBP"])
    def test_animated_input_stays_intact_for_admission(self, fmt):
        frames = [Image.new("RGBA", (100, 100), color) for color in ("black", "white")]
        buf = BytesIO()
        frames[0].save(buf, format=fmt, save_all=True, append_images=frames[1:], duration=100)
        data = buf.getvalue()
        assert Image.open(BytesIO(data)).n_frames == 2
        assert resize_image_bytes(data, max_dim=50) == data


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

    def test_extreme_aspect_ratio_still_resizes(self) -> None:
        data = self._make_image(3000, 1, fmt="PNG")
        with Image.open(BytesIO(resize_image_bytes(data))) as img:
            assert MAX_IMAGE_DIMENSION - 1 <= img.width <= MAX_IMAGE_DIMENSION
            assert img.height == 1


class TestDetectMediaTypeFromBytes:
    def test_jpeg_detected(self) -> None:
        # JPEG magic bytes: FF D8 FF
        data = b"\xff\xd8\xff\xe0" + b"\x00" * 8
        assert detect_media_type_from_bytes(data) == "image/jpeg"

    def test_png_detected(self) -> None:
        # PNG magic bytes: 89 50 4E 47
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4
        assert detect_media_type_from_bytes(data) == "image/png"

    def test_gif_detected(self) -> None:
        # GIF magic bytes: GIF8
        data = b"GIF89a" + b"\x00" * 6
        assert detect_media_type_from_bytes(data) == "image/gif"

    def test_webp_detected(self) -> None:
        # WebP magic bytes: RIFF....WEBP
        data = b"RIFF\x00\x00\x00\x00WEBP"
        assert detect_media_type_from_bytes(data) == "image/webp"

    def test_unknown_bytes_returns_none(self) -> None:
        data = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b"
        assert detect_media_type_from_bytes(data) is None

    def test_short_bytes_returns_none(self) -> None:
        data = b"\xff\xd8"
        assert detect_media_type_from_bytes(data) is None

    def test_empty_bytes_returns_none(self) -> None:
        assert detect_media_type_from_bytes(b"") is None

    def test_real_jpeg_bytes(self) -> None:
        """Detect MIME from real JPEG bytes generated by PIL."""
        buf = BytesIO()
        Image.new("RGB", (10, 10), color="red").save(buf, format="JPEG")
        assert detect_media_type_from_bytes(buf.getvalue()) == "image/jpeg"

    def test_real_png_bytes(self) -> None:
        """Detect MIME from real PNG bytes generated by PIL."""
        buf = BytesIO()
        Image.new("RGB", (10, 10), color="red").save(buf, format="PNG")
        assert detect_media_type_from_bytes(buf.getvalue()) == "image/png"


class TestConstants:
    def test_max_dimension_is_reasonable(self) -> None:
        assert 1000 <= MAX_IMAGE_DIMENSION <= 2000

    def test_valid_media_types_has_four(self) -> None:
        assert len(VALID_MEDIA_TYPES) == 4
