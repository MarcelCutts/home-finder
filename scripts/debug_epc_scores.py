#!/usr/bin/env python3
"""Debug per-heuristic scores for specific images."""

import math
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image, ImageStat


def debug_image(path: Path) -> None:
    img_bytes = path.read_bytes()
    img = Image.open(BytesIO(img_bytes))
    img.thumbnail((256, 256))
    img = img.convert("RGB")
    width, height = img.size
    total_pixels = width * height

    # HSV analysis
    hsv = img.convert("HSV")
    hsv_pixels = list(hsv.getdata())
    green_count = red_count = yellow_count = 0
    high_sat_count = 0
    for h, s, v in hsv_pixels:
        h_deg = h * 360 / 255
        if s > 50 and v > 80:
            high_sat_count += 1
            if 80 <= h_deg <= 160:
                green_count += 1
            elif h_deg <= 30 or h_deg >= 340:
                red_count += 1
            elif 30 < h_deg < 80:
                yellow_count += 1

    # Entropy
    grayscale = img.convert("L")
    histogram = grayscale.histogram()
    total = sum(histogram)
    entropy = -sum(p / total * math.log2(p / total) for p in histogram if p > 0)

    # Color diversity
    quantized = img.quantize(colors=16, method=Image.Quantize.FASTOCTREE)
    color_counts = quantized.getcolors(maxcolors=16) or []
    significant = sum(1 for c, _ in color_counts if c > total_pixels * 0.01)

    # Brightness
    pixel_data = list(grayscale.getdata())
    bright_ratio = sum(1 for p in pixel_data if p > 200) / total_pixels

    # Mean saturation
    hsv_stat = ImageStat.Stat(hsv)
    mean_sat = hsv_stat.mean[1]

    print(f"\n{'=' * 60}")
    print(f"  {path.name}")
    print(f"  Size: {width}x{height}  Aspect: {width/height:.2f}")
    print(f"  --- HSV bands ---")
    print(f"  Green: {green_count/total_pixels*100:.1f}%  Red/orange: {red_count/total_pixels*100:.1f}%  Yellow: {yellow_count/total_pixels*100:.1f}%")
    print(f"  High-saturation pixels: {high_sat_count/total_pixels*100:.1f}%")
    print(f"  Mean saturation: {mean_sat:.1f}")
    print(f"  --- Texture ---")
    print(f"  Entropy: {entropy:.2f}")
    print(f"  Significant colors (of 16): {significant}")
    print(f"  Bright ratio (>200): {bright_ratio*100:.1f}%")


CACHE = Path(__file__).parent.parent / "data" / "image_cache"

# Real EPC charts
print("\n### REAL EPC CHARTS ###")
for p in [
    CACHE / "zoopla_72363920" / "gallery_000_6e1e08c0.png",
    CACHE / "zoopla_72378948" / "gallery_000_27ee14c5.png",
    CACHE / "zoopla_72369938" / "gallery_000_640cbc22.png",
]:
    if p.exists():
        debug_image(p)

# False positives (room photos incorrectly flagged)
print("\n\n### FALSE POSITIVES (room photos) ###")
for p in [
    CACHE / "onthemarket_11193694" / "gallery_007_b29b5432.jpg",
    CACHE / "onthemarket_12024990" / "gallery_000_111b5374.jpg",
    CACHE / "onthemarket_12290789" / "gallery_004_e031e215.png",
    CACHE / "onthemarket_13564466" / "gallery_000_37cfe83f.jpg",
]:
    if p.exists():
        debug_image(p)

# True negatives (normal photos)
print("\n\n### TRUE NEGATIVES (normal photos) ###")
for p in [
    CACHE / "zoopla_72363920" / "gallery_001_c1df66b4.jpg",
    CACHE / "zoopla_72363920" / "gallery_002_6dfcc06b.jpg",
]:
    if p.exists():
        debug_image(p)
