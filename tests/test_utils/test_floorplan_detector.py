"""Tests for PIL-based floorplan detection."""

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from home_finder.utils.floorplan_detector import CONFIDENCE_THRESHOLD, detect_floorplan


def _make_image_bytes(
    *,
    size: tuple[int, int] = (400, 300),
    bg_color: tuple[int, int, int] = (255, 255, 255),
    draw_fn: object = None,
    fmt: str = "JPEG",
) -> bytes:
    """Create a test image and return bytes."""
    img = Image.new("RGB", size, bg_color)
    if draw_fn is not None:
        draw_fn(img)
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _draw_floorplan(img: Image.Image) -> None:
    """Draw a simple floorplan: black lines on white background."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    # Outer walls
    draw.rectangle([20, 20, w - 20, h - 20], outline="black", width=3)
    # Room dividers
    draw.line([(w // 2, 20), (w // 2, h - 20)], fill="black", width=2)
    draw.line([(20, h // 2), (w // 2, h // 2)], fill="black", width=2)
    # Door arcs (simple lines)
    draw.line([(w // 2, h // 3), (w // 2 + 30, h // 3 + 10)], fill="black", width=1)
    # Room labels (small text placeholders as rectangles)
    draw.rectangle([40, 40, 80, 50], fill="gray")
    draw.rectangle([w // 2 + 30, 40, w // 2 + 70, 50], fill="gray")


def _draw_colorful_photo(img: Image.Image) -> None:
    """Draw a colorful scene resembling a room photo."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    # Blue sky / ceiling
    draw.rectangle([0, 0, w, h // 3], fill=(135, 206, 235))
    # Green carpet
    draw.rectangle([0, 2 * h // 3, w, h], fill=(34, 139, 34))
    # Brown furniture
    draw.rectangle([50, h // 3, 150, 2 * h // 3], fill=(139, 69, 19))
    # Red accent
    draw.rectangle([200, h // 3 + 20, 250, h // 3 + 60], fill=(220, 20, 60))
    # Yellow lamp
    draw.ellipse([300, h // 3 - 20, 340, h // 3 + 20], fill=(255, 215, 0))


class TestDetectFloorplan:
    """Tests for detect_floorplan()."""

    def test_white_background_with_lines_detected(self) -> None:
        """Classic floorplan (black lines on white) should be detected."""
        img_bytes = _make_image_bytes(draw_fn=_draw_floorplan)
        is_fp, confidence = detect_floorplan(img_bytes)
        assert is_fp is True
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_colorful_photo_not_detected(self) -> None:
        """A colorful room photo should NOT be detected as floorplan."""
        img_bytes = _make_image_bytes(draw_fn=_draw_colorful_photo)
        is_fp, confidence = detect_floorplan(img_bytes)
        assert is_fp is False
        assert confidence < CONFIDENCE_THRESHOLD

    def test_pure_white_image(self) -> None:
        """Pure white image has high brightness/low saturation but no edges."""
        img_bytes = _make_image_bytes(bg_color=(255, 255, 255))
        is_fp, confidence = detect_floorplan(img_bytes)
        # May or may not be detected — the important thing is it doesn't crash
        # and confidence is reasonable (high brightness + low saturation but no edges)
        assert 0.0 <= confidence <= 1.0

    def test_dark_image_not_detected(self) -> None:
        """A dark image should not be detected as a floorplan."""
        img_bytes = _make_image_bytes(bg_color=(30, 30, 30))
        is_fp, confidence = detect_floorplan(img_bytes)
        assert is_fp is False

    def test_corrupt_bytes_returns_false(self) -> None:
        """Corrupt/non-image bytes should return (False, 0.0)."""
        is_fp, confidence = detect_floorplan(b"not an image at all")
        assert is_fp is False
        assert confidence == 0.0

    def test_empty_bytes_returns_false(self) -> None:
        """Empty bytes should return (False, 0.0)."""
        is_fp, confidence = detect_floorplan(b"")
        assert is_fp is False
        assert confidence == 0.0

    def test_png_format(self) -> None:
        """Should work with PNG images too."""
        img_bytes = _make_image_bytes(draw_fn=_draw_floorplan, fmt="PNG")
        is_fp, confidence = detect_floorplan(img_bytes)
        assert is_fp is True
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_confidence_is_bounded(self) -> None:
        """Confidence should always be between 0 and 1."""
        for draw_fn in [_draw_floorplan, _draw_colorful_photo, None]:
            _, confidence = detect_floorplan(_make_image_bytes(draw_fn=draw_fn))
            assert 0.0 <= confidence <= 1.0

    def test_grayscale_floorplan(self) -> None:
        """A grayscale floorplan with thin lines should be detected."""

        def draw(img: Image.Image) -> None:
            draw = ImageDraw.Draw(img)
            w, h = img.size
            # Light gray background with dark gray lines
            draw.rectangle([0, 0, w, h], fill=(240, 240, 240))
            draw.rectangle([30, 30, w - 30, h - 30], outline=(60, 60, 60), width=2)
            draw.line([(w // 3, 30), (w // 3, h - 30)], fill=(60, 60, 60), width=2)
            draw.line([(2 * w // 3, 30), (2 * w // 3, h - 30)], fill=(60, 60, 60), width=2)

        img_bytes = _make_image_bytes(draw_fn=draw)
        is_fp, confidence = detect_floorplan(img_bytes)
        assert is_fp is True
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_3d_render_with_colors_not_detected(self) -> None:
        """A 3D-rendered floorplan with colors should ideally not be detected.

        3D renders have high saturation and color diversity, more like photos.
        """

        def draw_3d(img: Image.Image) -> None:
            draw = ImageDraw.Draw(img)
            w, h = img.size
            # Simulate 3D render: colored floor, colored walls, furniture
            draw.rectangle([0, 0, w, h], fill=(200, 180, 160))  # warm beige floor
            draw.rectangle([10, 10, w // 2, h // 2], fill=(180, 140, 100))  # brown wall
            draw.rectangle([w // 2 + 10, 10, w - 10, h // 2], fill=(100, 150, 180))  # blue wall
            draw.rectangle([50, h // 2 + 10, 150, h - 10], fill=(80, 120, 80))  # green sofa
            draw.rectangle([200, h // 2 + 10, 300, h - 10], fill=(200, 60, 60))  # red table

        img_bytes = _make_image_bytes(draw_fn=draw_3d)
        is_fp, confidence = detect_floorplan(img_bytes)
        assert is_fp is False
