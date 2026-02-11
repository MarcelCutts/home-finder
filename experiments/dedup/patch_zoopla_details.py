"""Patch Zoopla properties in an existing snapshot with updated detail data.

Re-fetches detail pages for Zoopla properties that have empty gallery_urls,
using the fixed DetailFetcher which now correctly extracts RSC gallery data.

Also clears stale gallery_hashes and gallery_embeddings so the downstream
pipeline (hash_snapshot → image_cache → embed_snapshot) re-processes them.

Usage:
    uv run python patch_zoopla_details.py data/snapshots/snapshot_20260210_135830.json
    uv run python patch_zoopla_details.py data/snapshots/snapshot_*.json --dry-run
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from home_finder.logging import configure_logging, get_logger
from home_finder.models import Property, PropertySource
from home_finder.scrapers.detail_fetcher import DetailFetcher

logger = get_logger(__name__)


def _needs_patch(record: dict) -> bool:
    """Check if a Zoopla property needs its gallery re-fetched."""
    if record.get("source") != PropertySource.ZOOPLA.value:
        return False
    detail = record.get("detail")
    if not detail:
        return True
    gallery = detail.get("gallery_urls")
    return not gallery


def _record_to_property(record: dict) -> Property:
    """Reconstruct a Property from a snapshot record (ignoring detail/hashes)."""
    fields = {k: record[k] for k in Property.model_fields if k in record and record[k] is not None}
    return Property(**fields)


async def patch_snapshot(path: Path, *, dry_run: bool = False) -> None:
    snapshot = json.loads(path.read_text())
    properties = snapshot["properties"]

    to_patch = [(i, r) for i, r in enumerate(properties) if _needs_patch(r)]

    if not to_patch:
        print(f"No Zoopla properties need patching in {path.name}")
        return

    print(f"Found {len(to_patch)} Zoopla properties to patch in {path.name}")

    if dry_run:
        for _, r in to_patch:
            print(f"  would patch: {r.get('source')}:{r.get('source_id')} — {r.get('url')}")
        return

    fetcher = DetailFetcher(max_gallery_images=15)
    patched = 0
    failed = 0

    try:
        for count, (idx, record) in enumerate(to_patch):
            uid = f"{record['source']}:{record['source_id']}"
            try:
                prop = _record_to_property(record)
                data = await fetcher.fetch_detail_page(prop)

                if data and data.gallery_urls:
                    detail = record.setdefault("detail", {})
                    detail["gallery_urls"] = data.gallery_urls
                    if data.description and not detail.get("description"):
                        detail["description"] = data.description
                    if data.features and not detail.get("features"):
                        detail["features"] = data.features
                    if data.floorplan_url and not detail.get("floorplan_url"):
                        detail["floorplan_url"] = data.floorplan_url

                    # Clear stale downstream data so it gets recomputed
                    detail.pop("gallery_hashes", None)
                    detail.pop("gallery_embeddings", None)

                    patched += 1
                    logger.info(
                        "patched",
                        unique_id=uid,
                        gallery_count=len(data.gallery_urls),
                    )
                else:
                    failed += 1
                    logger.warning("no_gallery_data", unique_id=uid)

            except Exception as e:
                failed += 1
                logger.warning("patch_failed", unique_id=uid, error=str(e))

            if (count + 1) % 20 == 0:
                logger.info("progress", completed=count + 1, total=len(to_patch))

            await asyncio.sleep(0.5)
    finally:
        await fetcher.close()

    # Write back
    snapshot["properties"] = properties
    path.write_text(json.dumps(snapshot, indent=2, default=str))

    print(f"\nDone — patched {patched}, failed {failed} (of {len(to_patch)} candidates)")
    print(f"Snapshot updated: {path}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch Zoopla gallery URLs in an existing snapshot",
    )
    parser.add_argument("snapshots", nargs="+", type=Path, help="Snapshot JSON file(s)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be patched")
    args = parser.parse_args()

    configure_logging(json_output=False, level=logging.INFO)

    for path in args.snapshots:
        if not path.exists():
            print(f"Skipping {path} — file not found")
            continue
        await patch_snapshot(path, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
