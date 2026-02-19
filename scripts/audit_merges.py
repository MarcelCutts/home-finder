#!/usr/bin/env python3
"""Detect and delete falsely merged multi-source properties.

Walks all multi-source properties in the DB, hashes their cached gallery
images per source, and flags any merge where two sources share zero
matching gallery images (with 3+ images each) as a false merge.

Deleted properties will re-appear on the next scrape as fresh listings
(unless the listing has expired). The image-evidence guard in the
deduplication pipeline prevents them from being re-merged.

Usage:
    uv run python scripts/audit_merges.py [--data-dir PATH] [--dry-run]
"""

import argparse
import json
import sqlite3
from itertools import combinations
from pathlib import Path

from home_finder.utils.image_cache import find_cached_file
from home_finder.utils.image_hash import count_gallery_hash_matches, hash_from_disk

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"

# Require at least this many hashable gallery images per source
# to make a confident same/different call.
MIN_GALLERY_IMAGES = 3


def _hash_gallery_for_source(
    data_dir: str,
    unique_id: str,
    urls: list[str],
) -> list[str]:
    """Hash cached gallery images for a single source's URL list."""
    hashes: list[str] = []
    for url in urls:
        path = find_cached_file(data_dir, unique_id, url, "gallery")
        if path is None:
            continue
        h = hash_from_disk(path)
        if h is not None:
            hashes.append(h)
    return hashes


def main(data_dir: str, *, dry_run: bool = False) -> None:
    db_path = Path(data_dir) / "properties.db"
    if not db_path.is_file():
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Find all multi-source properties
    rows = conn.execute(
        "SELECT unique_id, sources FROM properties WHERE sources IS NOT NULL"
    ).fetchall()

    multi_source = []
    for row in rows:
        sources = json.loads(row["sources"])
        if len(sources) >= 2:
            multi_source.append((row["unique_id"], sources))

    print(f"Found {len(multi_source)} multi-source properties to audit\n")

    false_merges: list[tuple[str, list[str], dict[tuple[str, str], int]]] = []

    for unique_id, sources in multi_source:
        # Get gallery images grouped by source
        image_rows = conn.execute(
            """
            SELECT source, url FROM property_images
            WHERE property_unique_id = ? AND image_type = 'gallery'
            ORDER BY source, id
            """,
            (unique_id,),
        ).fetchall()

        by_source: dict[str, list[str]] = {}
        for img in image_rows:
            by_source.setdefault(img["source"], []).append(img["url"])

        if len(by_source) < 2:
            continue

        # Hash gallery images per source
        hashes_by_source: dict[str, list[str]] = {}
        for source, urls in by_source.items():
            hashes_by_source[source] = _hash_gallery_for_source(
                data_dir, unique_id, urls
            )

        # Compare all source pairs
        pair_matches: dict[tuple[str, str], int] = {}
        is_false_merge = False

        for src_a, src_b in combinations(sorted(hashes_by_source.keys()), 2):
            hashes_a = hashes_by_source[src_a]
            hashes_b = hashes_by_source[src_b]
            matches = count_gallery_hash_matches(hashes_a, hashes_b)
            pair_matches[(src_a, src_b)] = matches

            # Only flag if both sources have enough images to be confident
            if (
                len(hashes_a) >= MIN_GALLERY_IMAGES
                and len(hashes_b) >= MIN_GALLERY_IMAGES
                and matches == 0
            ):
                is_false_merge = True

        # Report
        status = "FALSE MERGE" if is_false_merge else "OK"
        print(f"  {unique_id} [{', '.join(sources)}] -> {status}")
        for (src_a, src_b), matches in pair_matches.items():
            ha = len(hashes_by_source[src_a])
            hb = len(hashes_by_source[src_b])
            print(f"    {src_a}({ha}) vs {src_b}({hb}): {matches} matches")

        if is_false_merge:
            false_merges.append((unique_id, sources, pair_matches))

    print(f"\n{'='*60}")
    print(f"False merges found: {len(false_merges)}")

    if not false_merges:
        conn.close()
        return

    if dry_run:
        print("\nDry run — no changes made. Re-run without --dry-run to delete.")
        conn.close()
        return

    # Delete false merges
    print("\nDeleting false merges...")
    for unique_id, sources, _ in false_merges:
        conn.execute(
            "DELETE FROM property_images WHERE property_unique_id = ?",
            (unique_id,),
        )
        conn.execute(
            "DELETE FROM quality_analyses WHERE property_unique_id = ?",
            (unique_id,),
        )
        conn.execute(
            "DELETE FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        conn.commit()
        print(f"  Deleted: {unique_id} [{', '.join(sources)}]")

    print(f"\nDeleted {len(false_merges)} false merges.")
    print("Run 'uv run home-finder --dry-run' to re-scrape and restore as separate listings.")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect and delete falsely merged multi-source properties"
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report false merges without deleting",
    )
    args = parser.parse_args()
    main(args.data_dir, dry_run=args.dry_run)
