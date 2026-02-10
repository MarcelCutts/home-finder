"""Add gallery image hashes to an existing snapshot.

Computes pHash + wHash + crop-resistant hash for all gallery images and
stores them in each property's detail.gallery_hashes field. Skips images
that already have hashes (idempotent).

Usage:
    uv run python hash_snapshot.py data/snapshots/snapshot_20240101_120000.json
    uv run python hash_snapshot.py data/snapshots/snapshot_*.json  # Multiple snapshots
    uv run python hash_snapshot.py data/snapshots/snapshot_*.json --max-images 10
    uv run python hash_snapshot.py data/snapshots/snapshot_*.json --rehash
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from home_finder.logging import configure_logging, get_logger

from image_hashing import hash_gallery

logger = get_logger(__name__)


async def hash_snapshot(
    path: Path, *, max_images: int = 15, rehash: bool = False,
) -> None:
    """Add image hashes to all properties in a snapshot file.

    Args:
        path: Snapshot JSON file.
        max_images: Max gallery images to hash per property.
        rehash: If True, re-download and recompute ALL hashes (replaces existing).
                Useful for backfilling new hash types (e.g. crop_hash).
    """
    data = json.loads(path.read_text())
    properties = data["properties"]

    total_images = 0
    already_hashed = 0
    newly_hashed = 0
    failed = 0

    for i, prop in enumerate(properties):
        detail = prop.get("detail")
        if not detail:
            continue

        gallery_urls = detail.get("gallery_urls") or []
        if not gallery_urls:
            continue

        if rehash:
            # Re-hash all URLs from scratch
            urls_to_hash = gallery_urls[:max_images]
            existing_hashes = []
        else:
            # Check if already hashed — skip known URLs
            existing_hashes = detail.get("gallery_hashes") or []
            existing_urls = {h["url"] for h in existing_hashes}
            urls_to_hash = [
                u for u in gallery_urls[:max_images] if u not in existing_urls
            ]

        if not urls_to_hash:
            already_hashed += len(gallery_urls[:max_images])
            continue

        total_images += len(urls_to_hash)

        uid = f"{prop['source']}:{prop['source_id']}"
        logger.info(
            "hashing_gallery",
            unique_id=uid,
            images=len(urls_to_hash),
            progress=f"{i + 1}/{len(properties)}",
        )

        results = await hash_gallery(urls_to_hash, max_images=max_images)

        # Build new hash entries
        new_hashes = []
        for h in results:
            new_hashes.append({
                "url": h.url,
                "phash": h.phash,
                "whash": h.whash,
                "crop_hash": h.crop_hash,
            })
            newly_hashed += 1

        failed += len(urls_to_hash) - len(results)

        if rehash:
            detail["gallery_hashes"] = new_hashes
        else:
            detail["gallery_hashes"] = existing_hashes + new_hashes

    # Also hash the hero/listing image if present
    for prop in properties:
        image_url = prop.get("image_url")
        if not image_url or ".svg" in image_url or "/assets/" in image_url:
            continue

        # Skip if already hashed (unless rehashing)
        if prop.get("hero_hashes") and not rehash:
            continue

        results = await hash_gallery([image_url], max_images=1)
        if results:
            prop["hero_hashes"] = {
                "url": results[0].url,
                "phash": results[0].phash,
                "whash": results[0].whash,
                "crop_hash": results[0].crop_hash,
            }

    # Write back
    path.write_text(json.dumps(data, indent=2, default=str))

    logger.info(
        "hashing_complete",
        path=str(path),
        total_images=total_images,
        newly_hashed=newly_hashed,
        already_hashed=already_hashed,
        failed=failed,
    )

    print(f"Hashed {path.name}: {newly_hashed} new, {already_hashed} existing, {failed} failed")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Add image hashes to snapshot files")
    parser.add_argument("snapshots", nargs="+", type=Path, help="Snapshot JSON file(s)")
    parser.add_argument(
        "--max-images",
        type=int,
        default=15,
        help="Max gallery images to hash per property",
    )
    parser.add_argument(
        "--rehash",
        action="store_true",
        help="Re-download and recompute ALL hashes (backfill crop_hash)",
    )
    args = parser.parse_args()

    configure_logging(json_output=False, level=logging.INFO)

    for path in args.snapshots:
        if not path.exists():
            print(f"Error: {path} not found")
            continue
        await hash_snapshot(path, max_images=args.max_images, rehash=args.rehash)


if __name__ == "__main__":
    asyncio.run(main())
