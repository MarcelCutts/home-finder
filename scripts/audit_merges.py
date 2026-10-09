#!/usr/bin/env python3
"""Report multi-source properties that need a review of their cached photographs.

Walks all multi-source properties in the DB, hashes their cached gallery
images per source, and flags groups with no shared gallery photographs (with
3+ images each) for review. Different photographs do not establish different
homes. This command always opens SQLite read-only and retains all cached files.
Reviewed membership decisions belong in PropertyStorage.memberships.

Usage:
    uv run python scripts/audit_merges.py [--data-dir PATH] [--dry-run]
"""

import argparse
import json
import sqlite3
from contextlib import closing
from itertools import combinations
from pathlib import Path

from home_finder.utils.image_cache import find_cached_file
from home_finder.utils.image_hash import count_gallery_hash_matches, hash_from_disk

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"

# Minimum coverage for flagging a disjoint pair for human review, not an identity verdict.
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
    """Always inspect without writes; retain dry_run for existing callers."""
    db_path = Path(data_dir) / "properties.db"
    if not db_path.is_file():
        print(f"Database not found: {db_path}")
        return

    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        _audit(conn, data_dir)


def _audit(conn: sqlite3.Connection, data_dir: str) -> None:
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

    review_count = 0

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
            print(f"  {unique_id} -> INSUFFICIENT COVERAGE")
            continue

        # Hash gallery images per source
        hashes_by_source: dict[str, list[str]] = {}
        for source, urls in by_source.items():
            hashes_by_source[source] = _hash_gallery_for_source(data_dir, unique_id, urls)

        # Compare all source pairs
        pair_matches: dict[tuple[str, str], int] = {}
        needs_review = False
        insufficient = False

        for src_a, src_b in combinations(sorted(hashes_by_source.keys()), 2):
            hashes_a = hashes_by_source[src_a]
            hashes_b = hashes_by_source[src_b]
            matches = count_gallery_hash_matches(hashes_a, hashes_b)
            pair_matches[(src_a, src_b)] = matches

            insufficient |= min(len(hashes_a), len(hashes_b)) < MIN_GALLERY_IMAGES
            # Comparable coverage still does not establish different homes.
            if (
                len(hashes_a) >= MIN_GALLERY_IMAGES
                and len(hashes_b) >= MIN_GALLERY_IMAGES
                and matches == 0
            ):
                needs_review = True

        # Report
        status = (
            "REVIEW NEEDED"
            if needs_review
            else "INSUFFICIENT COVERAGE"
            if insufficient
            else "SHARED PHOTOGRAPHS"
        )
        print(f"  {unique_id} [{', '.join(sources)}] -> {status}")
        for (src_a, src_b), matches in pair_matches.items():
            ha = len(hashes_by_source[src_a])
            hb = len(hashes_by_source[src_b])
            print(f"    {src_a}({ha}) vs {src_b}({hb}): {matches} matches")

        review_count += int(needs_review)

    print(f"\n{'=' * 60}")
    print(f"Groups needing review: {review_count}")
    print("Read-only report. No property, history or cached image was changed.")
    print(
        "No shared photographs is not proof of different homes; "
        "inspect unit evidence before splitting."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read-only report of cached photographs for membership review"
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compatibility option; this command is always read-only",
    )
    args = parser.parse_args()
    main(args.data_dir, dry_run=args.dry_run)
