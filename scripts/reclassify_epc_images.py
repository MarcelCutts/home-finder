#!/usr/bin/env python3
"""Reclassify EPC chart images: undo disk renames and update DB classification.

Phase 1 — Undo disk renames:
    Glob ``*/epc_*`` in image_cache and rename back to ``gallery_*``.
    This reverses the earlier script that renamed files on disk, which
    created a split-brain with the DB (DB says ``image_type='gallery'``
    but file is named ``epc_*``).

Phase 2 — DB reclassify:
    Query ``property_images WHERE image_type = 'gallery'``, find the
    cached file via ``find_cached_file``, run ``detect_epc()``, and
    UPDATE detected rows to ``image_type = 'epc'``.

After this script, files stay as ``gallery_*`` on disk and classification
lives solely in the DB.

Usage:
    uv run python scripts/reclassify_epc_images.py [--data-dir PATH] [--db PATH] [--dry-run]
"""

import argparse
import sqlite3
from pathlib import Path

from home_finder.utils.epc_detector import detect_epc
from home_finder.utils.image_cache import find_cached_file

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"


def _undo_disk_renames(cache_root: Path, *, dry_run: bool) -> int:
    """Phase 1: rename epc_* files back to gallery_* on disk."""
    reverted = 0
    for epc_file in sorted(cache_root.glob("*/epc_*")):
        if not epc_file.is_file():
            continue
        new_name = epc_file.name.replace("epc_", "gallery_", 1)
        new_path = epc_file.parent / new_name
        prop_dir = epc_file.parent.name
        print(f"  REVERT: {prop_dir}/{epc_file.name} -> {new_name}")
        if not dry_run:
            epc_file.rename(new_path)
        reverted += 1
    return reverted


def _reclassify_in_db(
    db_path: Path, data_dir: str, *, dry_run: bool
) -> tuple[int, int, int]:
    """Phase 2: scan gallery images in DB, detect EPCs, update image_type.

    Returns:
        Tuple of (scanned, reclassified, errors).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT id, property_unique_id, url FROM property_images"
        " WHERE image_type = 'gallery'"
    )
    rows = cursor.fetchall()

    scanned = 0
    reclassified = 0
    errors = 0

    for row in rows:
        scanned += 1
        url = row["url"]
        prop_id = row["property_unique_id"]
        row_id = row["id"]

        cached = find_cached_file(data_dir, prop_id, url, "gallery")
        if cached is None:
            continue

        try:
            image_bytes = cached.read_bytes()
            is_epc, confidence = detect_epc(image_bytes)
        except Exception as exc:
            print(f"  ERROR: {prop_id} {url} — {exc}")
            errors += 1
            continue

        if is_epc:
            epc_name = cached.name.replace("gallery_", "epc_", 1)
            print(
                f"  EPC: {prop_id} {cached.name} -> {epc_name}  (confidence={confidence:.3f})"
            )
            if not dry_run:
                conn.execute(
                    "UPDATE property_images SET image_type = 'epc' WHERE id = ?",
                    (row_id,),
                )
                epc_path = cached.parent / epc_name
                cached.rename(epc_path)
            reclassified += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return scanned, reclassified, errors


def main(data_dir: str, db_path: str | None = None, *, dry_run: bool = False) -> None:
    cache_root = Path(data_dir) / "image_cache"
    if not cache_root.is_dir():
        print(f"Cache directory not found: {cache_root}")
        return

    # Phase 1: undo disk renames
    print("Phase 1: Undoing disk renames (epc_* -> gallery_*)")
    reverted = _undo_disk_renames(cache_root, dry_run=dry_run)
    print(f"  Reverted: {reverted}{' (dry run)' if dry_run else ''}\n")

    # Phase 2: DB reclassification
    resolved_db = Path(db_path) if db_path else Path(data_dir) / "properties.db"
    if not resolved_db.is_file():
        print(f"Database not found: {resolved_db} — skipping DB reclassification")
        return

    print("Phase 2: Reclassifying gallery images in DB")
    scanned, reclassified, errors = _reclassify_in_db(
        resolved_db, data_dir, dry_run=dry_run
    )
    print(f"\n  Scanned:       {scanned}")
    print(f"  Reclassified:  {reclassified}{' (dry run)' if dry_run else ''}")
    print(f"  Errors:        {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Reclassify EPC images: undo disk renames + update DB"
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument(
        "--db", default=None, help="Path to SQLite DB (default: {data-dir}/properties.db)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying anything",
    )
    args = parser.parse_args()
    main(args.data_dir, db_path=args.db, dry_run=args.dry_run)
