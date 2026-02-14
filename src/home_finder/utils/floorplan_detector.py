"""PIL-based heuristic floorplan detection for property images.

Floorplans are visually distinctive: white/light backgrounds, line drawings,
low color saturation, geometric shapes. This module uses cheap pixel statistics
to classify images — zero API cost, negligible latency.
"""

from __future__ import annotations

from io import BytesIO

from home_finder.logging import get_logger

logger = get_logger(__name__)

# Score above this threshold → classify as floorplan
CONFIDENCE_THRESHOLD: float = 0.65


def detect_floorplan(image_bytes: bytes) -> tuple[bool, float]:
    """Detect whether an image is likely a floorplan using pixel statistics.

    Args:
        image_bytes: Raw image file bytes (JPEG, PNG, etc.).

    Returns:
        Tuple of (is_likely_floorplan, confidence_score 0.0-1.0).
    """
    try:
        return _analyze(image_bytes)
    except Exception:
        return False, 0.0


def _analyze(image_bytes: bytes) -> tuple[bool, float]:
    """Core analysis — separated for cleaner error handling."""
    from PIL import Image, ImageFilter, ImageStat

    img: Image.Image = Image.open(BytesIO(image_bytes))
    # Thumbnail to 256x256 for speed — we only need statistics
    img.thumbnail((256, 256))
    img = img.convert("RGB")

    width, height = img.size
    total_pixels = width * height

    # ── Heuristic 1: Color saturation (weight 0.30) ──
    # Floorplans have very low saturation (mostly grayscale/white with thin colored lines)
    hsv = img.convert("HSV")
    hsv_stat = ImageStat.Stat(hsv)
    # hsv_stat.mean[1] is the mean saturation (0-255 scale)
    mean_saturation = hsv_stat.mean[1]
    # Low saturation (<20) → floorplan signal, high (>60) → photo
    if mean_saturation < 15:
        sat_score = 1.0
    elif mean_saturation < 30:
        sat_score = 1.0 - (mean_saturation - 15) / 15  # Linear falloff
    elif mean_saturation < 60:
        sat_score = 0.0
    else:
        sat_score = 0.0

    # ── Heuristic 2: Brightness / white pixel ratio (weight 0.25) ──
    # Floorplans have lots of white/near-white background
    grayscale = img.convert("L")
    pixel_data: tuple[int, ...] = grayscale.get_flattened_data()  # type: ignore[assignment]
    bright_pixels = sum(1 for p in pixel_data if p > 200)
    bright_ratio = bright_pixels / total_pixels
    # 60-90% bright pixels → floorplan; <30% → definitely photo
    if bright_ratio > 0.7:
        bright_score = 1.0
    elif bright_ratio > 0.4:
        bright_score = (bright_ratio - 0.4) / 0.3
    else:
        bright_score = 0.0

    # ── Heuristic 3: Color diversity (weight 0.25) ──
    # Floorplans use very few distinct colors; photos have many
    quantized = img.quantize(colors=16, method=Image.Quantize.FASTOCTREE)
    # Count how many of the 16 palette slots are actually used
    color_counts = quantized.getcolors(maxcolors=16) or []
    # Filter out colors with negligible presence (<1% of pixels)
    significant_colors = sum(1 for count, _ in color_counts if count > total_pixels * 0.01)
    # Few colors (<5) → floorplan signal
    if significant_colors <= 3:
        color_score = 1.0
    elif significant_colors <= 6:
        color_score = 1.0 - (significant_colors - 3) / 3
    else:
        color_score = 0.0

    # ── Heuristic 4: Edge density (weight 0.20) ──
    # Floorplans have moderate-high edge density (thin lines on white)
    edges = grayscale.filter(ImageFilter.FIND_EDGES)
    edge_data: tuple[int, ...] = edges.get_flattened_data()  # type: ignore[assignment]
    edge_pixels = sum(1 for p in edge_data if p > 30)
    edge_ratio = edge_pixels / total_pixels
    # Floorplans: ~5-25% edge pixels; photos: very variable
    if 0.03 <= edge_ratio <= 0.30:
        edge_score = 1.0 if edge_ratio <= 0.15 else 1.0 - (edge_ratio - 0.15) / 0.15
    else:
        edge_score = 0.0

    # ── Weighted average ──
    confidence = 0.30 * sat_score + 0.25 * bright_score + 0.25 * color_score + 0.20 * edge_score

    is_floorplan = confidence >= CONFIDENCE_THRESHOLD

    return is_floorplan, round(confidence, 3)
