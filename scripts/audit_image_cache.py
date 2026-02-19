#!/usr/bin/env python3
"""Audit property_images DB rows against the disk image cache.

Phase 1 — DB→Disk check:
    For every ``property_images`` row, verify the file exists on disk via
    ``find_cached_file()``.  Reports orphaned rows (DB record, no file)
    broken down by image_type and property.

Phase 2 — Disk→DB check (informational):
    Walks ``{data_dir}/image_cache/*/`` and checks each file against the DB.
    Reports files with no DB record, categorising ``epc_*`` files as expected
    (renamed by the EPC detector and stored with image_type='epc' in DB).

Purge disk (``--purge-disk``):
    Deletes stale files from disk that have no matching DB record.
    Two categories:
    - Orphan directories: cache dirs whose property no longer exists in DB
    - Stale files: individual files in DB-backed dirs with no matching image row

Usage:
    uv run python scripts/audit_image_cache.py                        # Audit only
    uv run python scripts/audit_image_cache.py --fix                  # Delete orphaned DB rows
    uv run python scripts/audit_image_cache.py --fix --dry-run        # Preview DB deletions
    uv run python scripts/audit_image_cache.py --purge-disk           # Delete stale disk files
    uv run python scripts/audit_image_cache.py --purge-disk --dry-run # Preview disk deletions
"""

import argparse
import hashlib
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

from home_finder.utils.image_cache import find_cached_file, safe_dir_name

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"

# Matches filenames produced by url_to_filename(): {type}_{index}_{hash}.{ext}
_FILENAME_RE = re.compile(r"^([a-z]+)_\d{3}_([0-9a-f]{8})\.\w+$")


def _url_hash(url: str) -> str:
    """Reproduce the 8-char MD5 prefix used by url_to_filename()."""
    return hashlib.md5(url.encode()).hexdigest()[:8]


def phase1_db_to_disk(
    conn: sqlite3.Connection,
    data_dir: str,
    *,
    fix: bool = False,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Check every property_images row for a matching file on disk.

    Returns:
        Tuple of (total_rows, found_on_disk, missing_from_disk).
    """
    cursor = conn.execute(
        "SELECT id, property_unique_id, url, image_type FROM property_images ORDER BY id"
    )

    total = 0
    found = 0
    missing = 0
    missing_by_type: dict[str, int] = defaultdict(int)
    # property_unique_id -> list of (id, image_type)
    orphans_by_property: dict[str, list[tuple[int, str]]] = defaultdict(list)
    total_by_property: dict[str, int] = defaultdict(int)

    for row in cursor:
        total += 1
        row_id = row["id"]
        unique_id = row["property_unique_id"]
        url = row["url"]
        image_type = row["image_type"]
        total_by_property[unique_id] += 1

        cached = find_cached_file(data_dir, unique_id, url, image_type)
        if cached is not None:
            found += 1
        else:
            missing += 1
            missing_by_type[image_type] += 1
            orphans_by_property[unique_id].append((row_id, image_type))

    # --- Report ---
    print("Phase 1: DB rows vs disk cache")
    print(f"  Total property_images rows: {total:,}")
    if total:
        pct_found = found / total * 100
        pct_missing = missing / total * 100
        print(f"  Found on disk:              {found:,} ({pct_found:.1f}%)")
        print(f"  Missing from disk:          {missing:,} ({pct_missing:.1f}%)")
    else:
        print("  Found on disk:              0")
        print("  Missing from disk:          0")
    if missing_by_type:
        for img_type, count in sorted(missing_by_type.items()):
            print(f"    {img_type}:  {count}")

    if orphans_by_property:
        print(f"\n  Properties with orphaned images: {len(orphans_by_property)}")
        for uid, orphans in sorted(orphans_by_property.items()):
            type_counts: dict[str, int] = defaultdict(int)
            for _, img_type in orphans:
                type_counts[img_type] += 1
            parts = ", ".join(f"{c} {t}" for t, c in sorted(type_counts.items()))
            print(f"    {uid:<30} — {len(orphans)} missing ({parts})")

        # Flag properties that would lose ALL images
        losing_all = [
            uid
            for uid, orphans in orphans_by_property.items()
            if len(orphans) == total_by_property[uid]
        ]
        if losing_all:
            print(f"\n  Properties losing ALL images after cleanup: {len(losing_all)}")
            for uid in sorted(losing_all):
                orphan_count = len(orphans_by_property[uid])
                print(f"    {uid:<30} — {orphan_count} orphaned (0 remaining)")

    # --- Fix ---
    if fix and missing > 0:
        ids_to_delete = [
            row_id
            for orphans in orphans_by_property.values()
            for row_id, _ in orphans
        ]
        if dry_run:
            print(f"\n  Dry run — would delete {len(ids_to_delete)} orphaned rows")
        else:
            try:
                conn.executemany(
                    "DELETE FROM property_images WHERE id = ?",
                    [(row_id,) for row_id in ids_to_delete],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            print(f"\n  Deleted {len(ids_to_delete)} orphaned rows")

    return total, found, missing


def phase2_disk_to_db(
    conn: sqlite3.Connection,
    data_dir: str,
) -> tuple[int, int, int, int, int]:
    """Check disk cache files against DB records.

    Returns:
        Tuple of (total_files, with_db_record, without_db_record,
        epc_expected, unrecognized).
    """
    cache_root = Path(data_dir) / "image_cache"
    if not cache_root.is_dir():
        print("\nPhase 2: Disk files without DB records (informational)")
        print("  Cache directory not found — skipping")
        return 0, 0, 0, 0, 0

    # Build lookup indexed by (safe_dir_name, url_hash) -> set of image_types.
    # All DB rows are needed in memory for the index, so fetchall() is correct.
    all_rows = conn.execute(
        "SELECT property_unique_id, url, image_type FROM property_images"
    ).fetchall()
    dir_lookup: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in all_rows:
        h = _url_hash(row["url"])
        dir_name = safe_dir_name(row["property_unique_id"])
        dir_lookup[(dir_name, h)].add(row["image_type"])

    total_files = 0
    with_record = 0
    without_record = 0
    epc_expected = 0
    unrecognized = 0

    for prop_dir in sorted(cache_root.iterdir()):
        if not prop_dir.is_dir():
            continue
        dir_name = prop_dir.name

        for f in sorted(prop_dir.iterdir()):
            if not f.is_file():
                continue
            total_files += 1
            m = _FILENAME_RE.match(f.name)
            if not m:
                unrecognized += 1
                continue

            file_type = m.group(1)
            file_hash = m.group(2)

            # O(1) lookup by (dir_name, url_hash)
            types = dir_lookup.get((dir_name, file_hash), set())
            if file_type in types:
                with_record += 1
            elif file_type == "epc":
                # epc_ files are created by the EPC detector rename — expected
                epc_expected += 1
                without_record += 1
            else:
                without_record += 1

    print("\nPhase 2: Disk files without DB records (informational)")
    print(f"  Total cache files:   {total_files:,}")
    print(f"  With DB record:      {with_record:,}")
    print(f"  Without DB record:   {without_record:,}")
    if epc_expected:
        print(f"    epc_* (expected):  {epc_expected}")
    other = without_record - epc_expected
    if other:
        print(f"    Other:             {other}")
    if unrecognized:
        print(f"  Unrecognized names:  {unrecognized}")

    return total_files, with_record, without_record, epc_expected, unrecognized


def purge_disk_files(
    conn: sqlite3.Connection,
    data_dir: str,
    *,
    dry_run: bool = False,
) -> tuple[int, int, int, int]:
    """Delete stale disk files with no matching DB record.

    Category 1: Orphan directories — cache dirs whose property no longer
    exists in the ``properties`` table.  Removed with ``shutil.rmtree()``.

    Category 2+3: Stale files in DB-backed dirs — individual files whose
    URL hash doesn't match any ``property_images`` row for that property.

    Returns:
        Tuple of (orphan_dirs_removed, orphan_files_in_dirs,
        stale_files_removed, total_files_removed).
    """
    cache_root = Path(data_dir) / "image_cache"
    if not cache_root.is_dir():
        print("\nPurge disk: Cache directory not found — skipping")
        return 0, 0, 0, 0

    # Build set of all property unique_ids in DB (via safe_dir_name mapping)
    prop_rows = conn.execute("SELECT unique_id FROM properties").fetchall()
    db_dir_names: set[str] = {safe_dir_name(row["unique_id"]) for row in prop_rows}

    # Build lookup for file matching: (safe_dir_name, url_hash) -> set of image_types
    all_image_rows = conn.execute(
        "SELECT property_unique_id, url, image_type FROM property_images"
    ).fetchall()
    dir_lookup: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in all_image_rows:
        h = _url_hash(row["url"])
        dir_name = safe_dir_name(row["property_unique_id"])
        dir_lookup[(dir_name, h)].add(row["image_type"])

    orphan_dirs = 0
    orphan_dir_files = 0
    stale_files = 0

    for prop_dir in sorted(cache_root.iterdir()):
        if not prop_dir.is_dir():
            continue
        dir_name = prop_dir.name

        # Category 1: orphan directory (no matching property in DB)
        if dir_name not in db_dir_names:
            file_count = sum(1 for f in prop_dir.iterdir() if f.is_file())
            if dry_run:
                print(f"  Would remove orphan dir: {dir_name}/ ({file_count} files)")
            else:
                shutil.rmtree(prop_dir)
                print(f"  Removed orphan dir: {dir_name}/ ({file_count} files)")
            orphan_dirs += 1
            orphan_dir_files += file_count
            continue

        # Category 2+3: stale files in DB-backed directory
        for f in sorted(prop_dir.iterdir()):
            if not f.is_file():
                continue
            m = _FILENAME_RE.match(f.name)
            if not m:
                continue  # skip unrecognized filenames
            file_type = m.group(1)
            file_hash = m.group(2)
            types = dir_lookup.get((dir_name, file_hash), set())
            if file_type not in types:
                if dry_run:
                    print(f"  Would remove stale file: {dir_name}/{f.name}")
                else:
                    f.unlink()
                    print(f"  Removed stale file: {dir_name}/{f.name}")
                stale_files += 1

    total = orphan_dir_files + stale_files
    print("\nPurge disk summary:")
    print(f"  Orphan directories removed: {orphan_dirs} ({orphan_dir_files} files)")
    print(f"  Stale files removed:        {stale_files}")
    print(f"  Total files removed:         {total}")
    if dry_run:
        print("  (dry run — no files were actually deleted)")

    return orphan_dirs, orphan_dir_files, stale_files, total


def main(
    data_dir: str,
    *,
    fix: bool = False,
    dry_run: bool = False,
    purge_disk: bool = False,
) -> None:
    db_path = Path(data_dir) / "properties.db"
    if not db_path.is_file():
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        phase1_db_to_disk(conn, data_dir, fix=fix, dry_run=dry_run)
        phase2_disk_to_db(conn, data_dir)
        if purge_disk:
            purge_disk_files(conn, data_dir, dry_run=dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Audit property_images DB rows against the disk image cache"
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Delete orphaned DB rows (rows with no matching disk file)",
    )
    parser.add_argument(
        "--purge-disk",
        action="store_true",
        help="Delete stale disk files (files with no matching DB record)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletions without making changes (requires --fix or --purge-disk)",
    )
    args = parser.parse_args()
    if args.dry_run and not args.fix and not args.purge_disk:
        parser.error("--dry-run only makes sense with --fix or --purge-disk")
    main(args.data_dir, fix=args.fix, dry_run=args.dry_run, purge_disk=args.purge_disk)
