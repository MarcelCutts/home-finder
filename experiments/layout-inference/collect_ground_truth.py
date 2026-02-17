"""Step 1: Extract ground truth from the production database.

Finds properties that have ALL of:
- A floorplan image (image_type = 'floorplan' in property_images)
- Quality analysis with living_room_sqm IS NOT NULL
- 6+ gallery images in property_images
- Image cache still on disk

Also prints a data audit report showing the "opportunity" for photo-based inference.

Usage:
    uv run python collect_ground_truth.py
    uv run python collect_ground_truth.py --db path/to/properties.db
    uv run python collect_ground_truth.py --min-gallery 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import aiosqlite

from home_finder.utils.image_cache import get_cache_dir

DATA_DIR = Path(__file__).parent / "data"


async def run(db_path: str, min_gallery: int) -> None:
    data_dir = str(Path(db_path).parent)

    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row

    # ------------------------------------------------------------------
    # Audit report: what's in the DB?
    # ------------------------------------------------------------------
    print("=" * 60)
    print("DATA AUDIT REPORT")
    print("=" * 60)

    # Total enriched properties
    row = await (await conn.execute("SELECT COUNT(*) AS n FROM properties")).fetchone()
    total_props = row["n"]
    print(f"\nTotal properties in DB: {total_props}")

    # Properties with quality analysis
    row = await (
        await conn.execute("SELECT COUNT(*) AS n FROM quality_analyses")
    ).fetchone()
    total_analyzed = row["n"]
    print(f"Properties with quality analysis: {total_analyzed}")

    # Properties with floorplans vs without
    cursor = await conn.execute("""
        SELECT
            p.unique_id,
            p.source,
            (SELECT COUNT(*) FROM property_images pi
             WHERE pi.property_unique_id = p.unique_id AND pi.image_type = 'floorplan') AS fp_count,
            (SELECT COUNT(*) FROM property_images pi
             WHERE pi.property_unique_id = p.unique_id AND pi.image_type = 'gallery') AS gallery_count
        FROM properties p
        WHERE EXISTS (SELECT 1 FROM quality_analyses q WHERE q.property_unique_id = p.unique_id)
    """)
    rows = await cursor.fetchall()

    with_fp = [r for r in rows if r["fp_count"] > 0]
    without_fp = [r for r in rows if r["fp_count"] == 0]

    print(f"\nAnalyzed properties WITH floorplan: {len(with_fp)}")
    print(f"Analyzed properties WITHOUT floorplan: {len(without_fp)}")

    # By source breakdown
    print("\nBy source (with floorplan):")
    source_counts = Counter(r["source"] for r in with_fp)
    for source, count in source_counts.most_common():
        print(f"  {source}: {count}")

    print("\nBy source (without floorplan):")
    source_counts = Counter(r["source"] for r in without_fp)
    for source, count in source_counts.most_common():
        print(f"  {source}: {count}")

    # Gallery count distribution for floorplan-less properties (the "opportunity")
    print("\n--- Opportunity: floorplan-less properties by gallery count ---")
    gallery_counts = sorted([r["gallery_count"] for r in without_fp])
    if gallery_counts:
        thresholds = [6, 8, 10, 12, 15, 20]
        for t in thresholds:
            n = sum(1 for g in gallery_counts if g >= t)
            print(f"  {t}+ gallery images: {n} properties")

        print(f"\n  Gallery count distribution:")
        buckets = Counter()
        for g in gallery_counts:
            if g == 0:
                buckets["0"] += 1
            elif g <= 3:
                buckets["1-3"] += 1
            elif g <= 5:
                buckets["4-5"] += 1
            elif g <= 8:
                buckets["6-8"] += 1
            elif g <= 12:
                buckets["9-12"] += 1
            else:
                buckets["13+"] += 1
        for bucket in ["0", "1-3", "4-5", "6-8", "9-12", "13+"]:
            print(f"    {bucket:>5s}: {buckets.get(bucket, 0)}")
    else:
        print("  (no floorplan-less properties found)")

    # ------------------------------------------------------------------
    # Collect ground truth
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("GROUND TRUTH COLLECTION")
    print("=" * 60)

    cursor = await conn.execute("""
        SELECT
            p.unique_id,
            p.source,
            p.bedrooms,
            p.price_pcm,
            p.postcode,
            p.title,
            q.analysis_json,
            (SELECT COUNT(*) FROM property_images pi
             WHERE pi.property_unique_id = p.unique_id AND pi.image_type = 'gallery') AS gallery_count,
            (SELECT COUNT(*) FROM property_images pi
             WHERE pi.property_unique_id = p.unique_id AND pi.image_type = 'floorplan') AS fp_count
        FROM properties p
        JOIN quality_analyses q ON q.property_unique_id = p.unique_id
        WHERE
            -- Has floorplan
            (SELECT COUNT(*) FROM property_images pi
             WHERE pi.property_unique_id = p.unique_id AND pi.image_type = 'floorplan') > 0
            -- Has enough gallery images
            AND (SELECT COUNT(*) FROM property_images pi
                 WHERE pi.property_unique_id = p.unique_id AND pi.image_type = 'gallery') >= ?
        ORDER BY p.first_seen DESC
    """, (min_gallery,))
    candidates = await cursor.fetchall()

    print(f"\nCandidates with floorplan + {min_gallery}+ gallery + analysis: {len(candidates)}")

    ground_truth = []
    skipped_no_sqm = 0
    skipped_no_cache = 0

    for row in candidates:
        unique_id = row["unique_id"]

        # Check living_room_sqm in analysis
        try:
            analysis = json.loads(row["analysis_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        space = analysis.get("space", {})
        living_room_sqm = space.get("living_room_sqm")
        if living_room_sqm is None:
            skipped_no_sqm += 1
            continue

        # Check image cache exists on disk
        cache_dir = get_cache_dir(data_dir, unique_id)
        if not cache_dir.is_dir() or not any(cache_dir.iterdir()):
            skipped_no_cache += 1
            continue

        entry = {
            "unique_id": unique_id,
            "source": row["source"],
            "bedrooms": row["bedrooms"],
            "price_pcm": row["price_pcm"],
            "postcode": row["postcode"],
            "title": row["title"],
            "gallery_count": row["gallery_count"],
            "floorplan_count": row["fp_count"],
            "cache_dir": str(cache_dir),
            "ground_truth": {
                "living_room_sqm": living_room_sqm,
                "is_spacious_enough": space.get("is_spacious_enough"),
                "hosting_layout": space.get("hosting_layout"),
                "confidence": space.get("confidence"),
                "office_separation": analysis.get("bedroom", {}).get("office_separation"),
            },
        }
        ground_truth.append(entry)

    print(f"Skipped (no living_room_sqm): {skipped_no_sqm}")
    print(f"Skipped (no image cache on disk): {skipped_no_cache}")
    print(f"Final ground truth set: {len(ground_truth)}")

    if ground_truth:
        print("\nGround truth entries:")
        for i, entry in enumerate(ground_truth, 1):
            gt = entry["ground_truth"]
            print(
                f"  {i:2d}. [{entry['source']:12s}] {entry['unique_id']}"
                f"  {entry['bedrooms']}bed £{entry['price_pcm']}"
                f"  gallery={entry['gallery_count']}"
                f"  sqm={gt['living_room_sqm']}"
                f"  spacious={gt['is_spacious_enough']}"
                f"  hosting={gt['hosting_layout']}"
                f"  office={gt['office_separation']}"
            )

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "ground_truth.json"
    out_path.write_text(json.dumps(ground_truth, indent=2))
    print(f"\nSaved to {out_path}")

    if len(ground_truth) < 15:
        print(
            f"\nWARNING: Only {len(ground_truth)} ground truth entries."
            " Target is 15+. Run `uv run home-finder --dry-run` to build up the dataset."
        )

    await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect ground truth for layout inference experiment")
    parser.add_argument(
        "--db",
        default="../../data/properties.db",
        help="Path to production SQLite database (default: ../../data/properties.db)",
    )
    parser.add_argument(
        "--min-gallery",
        type=int,
        default=6,
        help="Minimum gallery images required (default: 6)",
    )
    args = parser.parse_args()

    # Resolve relative to script directory
    db_path = str(Path(args.db).resolve()) if not Path(args.db).is_absolute() else args.db
    if not Path(db_path).exists():
        # Try relative to script location
        alt = Path(__file__).parent / args.db
        if alt.exists():
            db_path = str(alt.resolve())
        else:
            print(f"Error: Database not found at {db_path}", file=sys.stderr)
            print(f"  Also tried: {alt}", file=sys.stderr)
            raise SystemExit(1)

    asyncio.run(run(db_path, args.min_gallery))


if __name__ == "__main__":
    main()
