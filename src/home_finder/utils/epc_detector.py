"""PIL-based heuristic EPC chart detection for property images.

EPC (Energy Performance Certificate) charts are visually distinctive:
coloured horizontal bands (green -> yellow -> orange -> red) on a white
background with high mean saturation and very low entropy.  This module
uses cheap pixel statistics to classify images — zero API cost, negligible
latency.

Follows the same pattern as floorplan_detector.py.

Calibrated against 10,758 real cached gallery images (978 properties).
Key separators vs room photos (validated on real data):
  - Entropy:         EPC 3.7-4.0  vs  photos 6.2-6.9
  - Mean saturation: EPC 73-76    vs  photos 18-39
  - Green ratio:     EPC 3.8-13%  vs  photos 0-0.2%
"""

from __future__ import annotations

import math
from io import BytesIO

from home_finder.logging import get_logger

logger = get_logger(__name__)

# Score above this threshold -> classify as EPC chart
CONFIDENCE_THRESHOLD: float = 0.60


def detect_epc(image_bytes: bytes) -> tuple[bool, float]:
    """Detect whether an image is likely an EPC chart using pixel statistics.

    Args:
        image_bytes: Raw image file bytes (JPEG, PNG, etc.).

    Returns:
        Tuple of (is_likely_epc, confidence_score 0.0-1.0).
    """
    try:
        return _analyze(image_bytes)
    except Exception:
        logger.debug("epc_detection_failed", exc_info=True)
        return False, 0.0


def _analyze(image_bytes: bytes) -> tuple[bool, float]:
    """Core analysis — separated for cleaner error handling."""
    from PIL import Image, ImageStat

    import home_finder.utils.image_processing  # noqa: F401  (sets MAX_IMAGE_PIXELS)

    img: Image.Image = Image.open(BytesIO(image_bytes))
    # Thumbnail to 256x256 for speed — we only need statistics
    img.thumbnail((256, 256))
    img = img.convert("RGB")

    width, height = img.size
    total_pixels = width * height

    # ── Heuristic 1: Entropy (weight 0.35) ──
    # Strongest separator.  EPC charts have large flat coloured regions ->
    # very low grayscale entropy (3.7-4.0).  Room photos have textures,
    # gradients, shadows -> high entropy (6.2-7.5).
    grayscale = img.convert("L")
    histogram = grayscale.histogram()  # 256 bins
    total = sum(histogram)
    entropy = 0.0
    for count in histogram:
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)

    # Hard gate: real photos always have entropy > 6.0.  EPC charts max at ~4.0.
    # The gap is huge (2+ points), so a hard cutoff at 5.5 is safe.
    if entropy > 5.5:
        return False, 0.0

    if entropy < 4.5:
        entropy_score = 1.0
    elif entropy < 5.5:
        entropy_score = 1.0 - (entropy - 4.5)
    else:
        entropy_score = 0.0

    # ── Heuristic 2: Mean saturation (weight 0.30) ──
    # EPC charts have vivid coloured bands -> high mean saturation (73-76).
    # Room photos have muted, natural tones -> low mean saturation (18-39).
    hsv = img.convert("HSV")
    hsv_stat = ImageStat.Stat(hsv)
    mean_saturation = hsv_stat.mean[1]

    if mean_saturation >= 60:
        sat_score = 1.0
    elif mean_saturation >= 45:
        sat_score = (mean_saturation - 45) / 15
    else:
        sat_score = 0.0

    # ── Heuristic 3: Green + red/orange co-presence (weight 0.20) ──
    # EPC charts always contain both green AND red/orange bands.  Room photos
    # may have one (wood -> orange, plants -> green) but rarely both at
    # significant levels with high saturation.
    hsv_pixels: list[tuple[int, ...]] = list(hsv.get_flattened_data())  # type: ignore[arg-type]
    green_count = 0
    red_orange_count = 0
    for h, s, v in hsv_pixels:
        h_deg = h * 360 / 255
        if s > 60 and v > 80:  # Require clear saturation
            if 80 <= h_deg <= 160:
                green_count += 1
            elif h_deg <= 30 or h_deg >= 340:
                red_orange_count += 1

    green_ratio = green_count / total_pixels
    red_ratio = red_orange_count / total_pixels

    # Require BOTH green and red/orange above threshold
    has_green = green_ratio >= 0.03
    has_red = red_ratio >= 0.03
    if has_green and has_red:
        green_red_score = 1.0
    elif has_green or has_red:
        # Only one present — weak signal
        green_red_score = 0.2
    else:
        green_red_score = 0.0

    # ── Heuristic 4: Brightness / white ratio (weight 0.15) ──
    # EPC charts have substantial white background (60-67%).
    pixel_data: tuple[int, ...] = grayscale.get_flattened_data()  # type: ignore[assignment]
    bright_pixels = sum(1 for p in pixel_data if p > 200)
    bright_ratio = bright_pixels / total_pixels

    if 0.40 <= bright_ratio <= 0.80:
        bright_score = 1.0
    elif bright_ratio > 0.80:
        bright_score = 0.5
    elif bright_ratio > 0.25:
        bright_score = (bright_ratio - 0.25) / 0.15
    else:
        bright_score = 0.0

    # ── Weighted average ──
    confidence = (
        0.35 * entropy_score
        + 0.30 * sat_score
        + 0.20 * green_red_score
        + 0.15 * bright_score
    )

    is_epc = confidence >= CONFIDENCE_THRESHOLD

    return is_epc, round(confidence, 3)
