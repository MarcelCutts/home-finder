"""Tests for PIL-based EPC chart detection."""

from io import BytesIO

from PIL import Image, ImageDraw

from home_finder.utils.epc_detector import CONFIDENCE_THRESHOLD, detect_epc


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


def _draw_epc_chart(img: Image.Image) -> None:
    """Draw a synthetic EPC chart: coloured horizontal bands on white background.

    Mimics the characteristic green -> yellow -> orange -> red banding
    with a white background and letter labels.
    """
    draw = ImageDraw.Draw(img)
    w, h = img.size

    # White background is already set by _make_image_bytes
    # Draw horizontal coloured bands (A-G rating scale)
    band_colors = [
        (0, 128, 0),      # A - dark green
        (50, 180, 50),    # B - green
        (140, 200, 60),   # C - yellow-green
        (255, 255, 0),    # D - yellow
        (255, 165, 0),    # E - orange
        (255, 100, 0),    # F - dark orange
        (255, 0, 0),      # G - red
    ]
    band_height = h // 10
    start_y = h // 6

    for i, color in enumerate(band_colors):
        y = start_y + i * (band_height + 4)
        # Each band gets progressively wider (like real EPC charts)
        band_width = int(w * 0.3) + int(w * 0.08 * i)
        draw.rectangle([40, y, 40 + band_width, y + band_height], fill=color)


def _draw_colorful_photo(img: Image.Image) -> None:
    """Draw a colorful scene resembling a room photo (with noise for high entropy)."""
    import random

    draw = ImageDraw.Draw(img)
    w, h = img.size
    draw.rectangle([0, 0, w, h // 3], fill=(135, 206, 235))
    draw.rectangle([0, 2 * h // 3, w, h], fill=(34, 139, 34))
    draw.rectangle([50, h // 3, 150, 2 * h // 3], fill=(139, 69, 19))
    draw.rectangle([200, h // 3 + 20, 250, h // 3 + 60], fill=(220, 20, 60))
    draw.ellipse([300, h // 3 - 20, 340, h // 3 + 20], fill=(255, 215, 0))
    # Add pixel noise to push entropy above 5.5 (like real photos)
    rng = random.Random(42)
    pixels = img.load()
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            n = rng.randint(-20, 20)
            pixels[x, y] = (
                max(0, min(255, r + n)),
                max(0, min(255, g + n)),
                max(0, min(255, b + n)),
            )


def _draw_floorplan(img: Image.Image) -> None:
    """Draw a simple floorplan: black lines on white background."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    draw.rectangle([20, 20, w - 20, h - 20], outline="black", width=3)
    draw.line([(w // 2, 20), (w // 2, h - 20)], fill="black", width=2)
    draw.line([(20, h // 2), (w // 2, h // 2)], fill="black", width=2)


class TestDetectEpc:
    """Tests for detect_epc()."""

    def test_epc_chart_detected(self) -> None:
        """Synthetic EPC chart with coloured bands should be detected."""
        img_bytes = _make_image_bytes(draw_fn=_draw_epc_chart)
        is_epc, confidence = detect_epc(img_bytes)
        assert is_epc is True
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_colorful_photo_not_detected(self) -> None:
        """A colorful room photo should NOT be detected as EPC."""
        img_bytes = _make_image_bytes(draw_fn=_draw_colorful_photo)
        is_epc, _confidence = detect_epc(img_bytes)
        assert is_epc is False

    def test_floorplan_not_detected(self) -> None:
        """A floorplan (low saturation, no green/red bands) should NOT be detected."""
        img_bytes = _make_image_bytes(draw_fn=_draw_floorplan)
        is_epc, _confidence = detect_epc(img_bytes)
        assert is_epc is False

    def test_pure_white_image_not_detected(self) -> None:
        """Pure white image has low entropy but no coloured bands."""
        img_bytes = _make_image_bytes(bg_color=(255, 255, 255))
        is_epc, confidence = detect_epc(img_bytes)
        assert is_epc is False
        assert confidence < CONFIDENCE_THRESHOLD

    def test_corrupt_bytes_returns_false(self) -> None:
        """Corrupt/non-image bytes should return (False, 0.0)."""
        is_epc, confidence = detect_epc(b"not an image at all")
        assert is_epc is False
        assert confidence == 0.0

    def test_empty_bytes_returns_false(self) -> None:
        """Empty bytes should return (False, 0.0)."""
        is_epc, confidence = detect_epc(b"")
        assert is_epc is False
        assert confidence == 0.0

    def test_png_format(self) -> None:
        """Should work with PNG images too."""
        img_bytes = _make_image_bytes(draw_fn=_draw_epc_chart, fmt="PNG")
        is_epc, confidence = detect_epc(img_bytes)
        assert is_epc is True
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_confidence_is_bounded(self) -> None:
        """Confidence should always be between 0 and 1."""
        for draw_fn in [_draw_epc_chart, _draw_colorful_photo, _draw_floorplan, None]:
            _, confidence = detect_epc(_make_image_bytes(draw_fn=draw_fn))
            assert 0.0 <= confidence <= 1.0

    def test_dark_image_not_detected(self) -> None:
        """A dark image should not be detected as an EPC chart."""
        img_bytes = _make_image_bytes(bg_color=(30, 30, 30))
        is_epc, _confidence = detect_epc(img_bytes)
        assert is_epc is False
