#!/usr/bin/env python3
"""Validate EPC detector against the real image cache.

Scans every cached gallery image, runs the EPC detector, and copies
detected images into a review folder for visual inspection.

Usage:
    uv run python scripts/validate_epc_detector.py
"""

import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from home_finder.utils.epc_detector import CONFIDENCE_THRESHOLD, detect_epc

CACHE_DIR = Path(__file__).parent.parent / "data" / "image_cache"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "epc_review"


def main() -> None:
    if not CACHE_DIR.exists():
        print(f"Image cache not found at {CACHE_DIR}")
        sys.exit(1)

    # Clean and recreate output dir
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    detected: list[tuple[str, str, float]] = []  # (property_id, filename, confidence)
    false_negative_candidates: list[tuple[str, str, float]] = []  # near misses
    scanned = 0

    property_dirs = sorted(CACHE_DIR.iterdir())
    for prop_dir in property_dirs:
        if not prop_dir.is_dir():
            continue

        property_id = prop_dir.name

        for img_file in sorted(prop_dir.iterdir()):
            if not img_file.is_file():
                continue
            # Only scan gallery images (not floorplans)
            if not img_file.name.startswith("gallery_"):
                continue

            scanned += 1
            img_bytes = img_file.read_bytes()
            is_epc, confidence = detect_epc(img_bytes)

            if is_epc:
                detected.append((property_id, img_file.name, confidence))
                # Copy to review folder: property_id__filename
                dest = OUTPUT_DIR / f"{property_id}__{img_file.name}"
                shutil.copy2(img_file, dest)
            elif confidence >= CONFIDENCE_THRESHOLD - 0.15:
                # Near misses for review
                false_negative_candidates.append((property_id, img_file.name, confidence))

    # Summary
    print(f"\nScanned {scanned} gallery images across {len(property_dirs)} properties")
    print(f"Detected {len(detected)} EPC charts (threshold: {CONFIDENCE_THRESHOLD})")
    print(f"Near misses: {len(false_negative_candidates)}")
    print()

    if detected:
        print("=== DETECTED (copied to data/epc_review/) ===")
        for prop_id, filename, conf in detected:
            print(f"  {prop_id}/{filename}  confidence={conf:.3f}")
        print()

    if false_negative_candidates:
        print("=== NEAR MISSES (not copied, for tuning) ===")
        for prop_id, filename, conf in false_negative_candidates:
            print(f"  {prop_id}/{filename}  confidence={conf:.3f}")
        print()

    # Also show the known EPC leakers for quick reference
    known_leakers = ["zoopla_72363920", "zoopla_72378948", "zoopla_72369938"]
    print("=== KNOWN EPC LEAKERS ===")
    for leaker in known_leakers:
        hits = [d for d in detected if d[0] == leaker]
        if hits:
            for _, filename, conf in hits:
                print(f"  {leaker}/{filename}  DETECTED  confidence={conf:.3f}")
        else:
            print(f"  {leaker}  NOT DETECTED")

    print(f"\nReview folder: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
