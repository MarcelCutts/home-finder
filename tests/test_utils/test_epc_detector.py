"""Tests for PIL-based EPC chart detection."""

from collections.abc import Callable
from io import BytesIO

from PIL import Image, ImageDraw

from home_finder.utils.epc_detector import CONFIDENCE_THRESHOLD, detect_epc


def _make_image_bytes(
    *,
    size: tuple[int, int] = (400, 300),
    bg_color: tuple[int, int, int] = (255, 255, 255),
    draw_fn: Callable[[Image.Image], None] | None = None,
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


def _draw_env_impact_chart(img: Image.Image) -> None:
    """Draw a synthetic Environmental Impact (CO₂) chart.

    Grey graduated bands (A-G) on white background with a blue header band
    at the top — mimics the monochrome Environmental Impact Rating charts
    found on EPC certificates.
    """
    draw = ImageDraw.Draw(img)
    w, h = img.size

    # Blue header band at the top (~15% of height)
    header_h = h // 6
    draw.rectangle([0, 0, w, header_h], fill=(41, 100, 180))

    # Grey graduated bands (A-G) — lightest to darkest
    band_greys = [
        (200, 200, 200),  # A - lightest grey
        (180, 180, 180),  # B
        (160, 160, 160),  # C
        (140, 140, 140),  # D
        (120, 120, 120),  # E
        (100, 100, 100),  # F
        (80, 80, 80),     # G - darkest grey
    ]
    band_height = h // 10
    start_y = header_h + 10

    for i, color in enumerate(band_greys):
        y = start_y + i * (band_height + 4)
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
    assert pixels is not None
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]  # type: ignore[misc]
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
        for draw_fn in [
            _draw_epc_chart, _draw_env_impact_chart, _draw_colorful_photo,
            _draw_floorplan, None,
        ]:
            _, confidence = detect_epc(_make_image_bytes(draw_fn=draw_fn))
            assert 0.0 <= confidence <= 1.0

    def test_dual_format_epc_detected(self) -> None:
        """Dual-format EPC chart (Energy + CO₂ side-by-side) should be detected.

        These have higher entropy (~5.9) than single charts but the distinctive
        green/red bands and white background are still present.
        """
        import random

        def _draw_dual_chart(img: Image.Image) -> None:
            draw = ImageDraw.Draw(img)
            w, h = img.size

            band_colors = [
                (0, 128, 0), (50, 180, 50), (140, 200, 60),
                (255, 255, 0), (255, 165, 0), (255, 100, 0), (255, 0, 0),
            ]
            band_height = h // 12

            # Left chart (Energy Efficiency)
            for i, color in enumerate(band_colors):
                y = h // 8 + i * (band_height + 3)
                bw = int(w * 0.12) + int(w * 0.04 * i)
                draw.rectangle([20, y, 20 + bw, y + band_height], fill=color)

            # Right chart (Environmental Impact)
            offset_x = w // 2 + 10
            for i, color in enumerate(band_colors):
                y = h // 8 + i * (band_height + 3)
                bw = int(w * 0.12) + int(w * 0.04 * i)
                draw.rectangle([offset_x, y, offset_x + bw, y + band_height], fill=color)

            # Add slight noise to raise entropy (simulates text/labels)
            rng = random.Random(99)
            pixels = img.load()
            assert pixels is not None
            for y in range(h):
                for x in range(w):
                    r, g, b = pixels[x, y]  # type: ignore[misc]
                    if r == 255 and g == 255 and b == 255:
                        n = rng.randint(-8, 8)
                        pixels[x, y] = (
                            max(0, min(255, r + n)),
                            max(0, min(255, g + n)),
                            max(0, min(255, b + n)),
                        )

        img_bytes = _make_image_bytes(size=(800, 400), draw_fn=_draw_dual_chart)
        is_epc, confidence = detect_epc(img_bytes)
        assert is_epc is True
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_env_impact_chart_detected(self) -> None:
        """Environmental Impact (CO₂) chart with grey bands + blue header should be detected."""
        img_bytes = _make_image_bytes(draw_fn=_draw_env_impact_chart)
        is_epc, confidence = detect_epc(img_bytes)
        assert is_epc is True
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_vivid_room_with_green_and_red_not_detected(self) -> None:
        """A vivid room with green plants and red/orange brick should NOT be detected.

        Targets colour-path false positives: high saturation + green + red/orange
        co-presence + bright walls can reach 0.624-0.650 without the entropy gate.
        The entropy early-return blocks these high-entropy photos.
        """
        import random

        def _draw_vivid_room(img: Image.Image) -> None:
            draw = ImageDraw.Draw(img)
            w, h = img.size
            # Varied wall tones (upper portion) — gradient effect
            draw.rectangle([0, 0, w // 2, h // 3], fill=(245, 240, 235))
            draw.rectangle([w // 2, 0, w, h // 3], fill=(220, 215, 205))
            # Red/orange brick wall section
            draw.rectangle([0, h // 2, w, h], fill=(180, 80, 40))
            # Mid-tone transition
            draw.rectangle([0, h // 3, w, h // 2], fill=(200, 180, 160))
            # Green plant areas (saturated)
            draw.rectangle([10, h // 4, 80, h // 2], fill=(30, 150, 50))
            draw.rectangle([w - 100, h // 4, w - 20, h // 2 - 20], fill=(40, 160, 60))
            draw.rectangle([w // 3, 10, w // 3 + 60, h // 4], fill=(50, 140, 40))
            # Saturated red cushion/art
            draw.rectangle([w // 2 - 30, h // 2 - 40, w // 2 + 30, h // 2], fill=(200, 30, 30))
            # Dark wood furniture
            draw.rectangle([120, h // 2 + 20, 220, h - 20], fill=(60, 35, 20))
            # Window with sky
            draw.rectangle([280, 20, 380, 100], fill=(135, 190, 230))
            # Heavy per-channel pixel noise for realistic photo entropy (>6.5).
            # Real photos have independent RGB noise across many tonal regions.
            rng = random.Random(55)
            pixels = img.load()
            assert pixels is not None
            for y in range(h):
                for x in range(w):
                    r, g, b = pixels[x, y]  # type: ignore[misc]
                    pixels[x, y] = (
                        max(0, min(255, r + rng.randint(-55, 55))),
                        max(0, min(255, g + rng.randint(-55, 55))),
                        max(0, min(255, b + rng.randint(-55, 55))),
                    )

        img_bytes = _make_image_bytes(draw_fn=_draw_vivid_room)
        is_epc, confidence = detect_epc(img_bytes)
        assert is_epc is False, f"Vivid room falsely detected as EPC (confidence={confidence})"

    def test_blue_tinted_room_not_detected(self) -> None:
        """A room with blue walls/sky should NOT be detected as EPC."""
        import random

        def _draw_blue_room(img: Image.Image) -> None:
            draw = ImageDraw.Draw(img)
            w, h = img.size
            # Blue sky and walls
            draw.rectangle([0, 0, w, h // 2], fill=(100, 150, 220))
            # Wood floor
            draw.rectangle([0, h // 2, w, h], fill=(160, 120, 80))
            # Window
            draw.rectangle([50, 30, 150, 120], fill=(200, 220, 255))
            # Furniture
            draw.rectangle([200, h // 2 - 50, 350, h // 2], fill=(80, 60, 40))
            # Add pixel noise for high entropy
            rng = random.Random(77)
            pixels = img.load()
            assert pixels is not None
            for y in range(h):
                for x in range(w):
                    r, g, b = pixels[x, y]  # type: ignore[misc]
                    n = rng.randint(-25, 25)
                    pixels[x, y] = (
                        max(0, min(255, r + n)),
                        max(0, min(255, g + n)),
                        max(0, min(255, b + n)),
                    )

        img_bytes = _make_image_bytes(draw_fn=_draw_blue_room)
        is_epc, _confidence = detect_epc(img_bytes)
        assert is_epc is False

    def test_blue_annotated_floorplan_not_detected(self) -> None:
        """A floorplan with blue dimension labels should NOT be detected as EPC.

        Blue-annotated floorplans have low entropy, white background, and ~10%
        saturated blue pixels — similar profile to Environmental Impact charts.
        The monochrome path must not false-positive on these.
        """

        def _draw_blue_annotated_floorplan(img: Image.Image) -> None:
            draw = ImageDraw.Draw(img)
            w, h = img.size
            # Black room outlines on white background
            draw.rectangle([30, 30, w // 2 - 10, h // 2 - 10], outline="black", width=3)
            draw.rectangle([w // 2 + 10, 30, w - 30, h // 2 - 10], outline="black", width=3)
            draw.rectangle([30, h // 2 + 10, w - 30, h - 30], outline="black", width=3)
            # Door arcs
            draw.arc([w // 2 - 30, h // 2 - 30, w // 2 + 30, h // 2 + 30], 0, 90, fill="black", width=2)
            # Blue dimension labels — saturated blue text/lines (~10% coverage)
            blue = (0, 100, 220)
            # Horizontal dimension lines
            draw.line([(40, 20), (w // 2 - 20, 20)], fill=blue, width=2)
            draw.line([(w // 2 + 20, 20), (w - 40, 20)], fill=blue, width=2)
            # Vertical dimension lines
            draw.line([(20, 40), (20, h // 2 - 20)], fill=blue, width=2)
            draw.line([(20, h // 2 + 20), (20, h - 40)], fill=blue, width=2)
            # Blue room labels (filled rectangles to simulate text blocks)
            draw.rectangle([60, 60, 140, 80], fill=blue)
            draw.rectangle([w // 2 + 40, 60, w // 2 + 120, 80], fill=blue)
            draw.rectangle([60, h // 2 + 40, 140, h // 2 + 60], fill=blue)
            # Additional blue markers to push blue coverage to ~10%
            for y_off in range(100, h - 50, 60):
                draw.rectangle([w - 80, y_off, w - 40, y_off + 15], fill=blue)

        img_bytes = _make_image_bytes(size=(400, 300), draw_fn=_draw_blue_annotated_floorplan)
        is_epc, confidence = detect_epc(img_bytes)
        assert is_epc is False, f"Blue-annotated floorplan falsely detected as EPC (confidence={confidence})"

    def test_dark_image_not_detected(self) -> None:
        """A dark image should not be detected as an EPC chart."""
        img_bytes = _make_image_bytes(bg_color=(30, 30, 30))
        is_epc, _confidence = detect_epc(img_bytes)
        assert is_epc is False
