"""Add SSCD embeddings to an existing snapshot using locally cached images.

Reads gallery images from the local cache (data/images/), computes 512-dim
SSCD embeddings, and stores them as base64-encoded float32 arrays in each
property's detail.gallery_embeddings field. Idempotent.

Requires: torch, torchvision (install with `uv sync --extra sscd`)
Requires: images cached locally (run image_cache.py first)

Usage:
    uv run python embed_snapshot.py data/snapshots/snapshot_*.json
    uv run python embed_snapshot.py data/snapshots/snapshot_*.json --max-images 15
"""

import argparse
import base64
import json
import logging
from pathlib import Path

import numpy as np

from home_finder.logging import configure_logging, get_logger

logger = get_logger(__name__)


def embedding_to_base64(embedding: np.ndarray) -> str:
    """Encode a float32 numpy array as base64 string."""
    return base64.b64encode(embedding.astype(np.float32).tobytes()).decode("ascii")


def base64_to_embedding(b64: str) -> np.ndarray:
    """Decode a base64 string back to float32 numpy array."""
    return np.frombuffer(base64.b64decode(b64), dtype=np.float32).copy()


def embed_snapshot(path: Path, *, max_images: int = 15) -> None:
    """Add SSCD embeddings to all properties in a snapshot file.

    Reads images from the local cache (data/images/{source}_{source_id}/).
    Properties without cached images are skipped.
    """
    from image_cache import get_cached_images
    from sscd_embeddings import SSCDEncoder

    data = json.loads(path.read_text())
    properties = data["properties"]

    encoder = SSCDEncoder()

    total_embedded = 0
    total_skipped = 0
    total_already = 0

    for i, prop in enumerate(properties):
        detail = prop.get("detail")
        if not detail:
            continue

        gallery_urls = detail.get("gallery_urls") or []
        if not gallery_urls:
            continue

        # Check if already embedded
        existing = detail.get("gallery_embeddings") or []
        needed = min(max_images, len(gallery_urls))
        if len(existing) >= needed:
            total_already += 1
            continue

        source = prop["source"]
        source_id = prop["source_id"]
        uid = f"{source}:{source_id}"

        # Get cached image files
        cached_paths = get_cached_images(source, source_id)
        if not cached_paths:
            logger.debug("no_cached_images", unique_id=uid)
            total_skipped += 1
            continue

        cached_paths = cached_paths[:max_images]

        logger.info(
            "embedding_gallery",
            unique_id=uid,
            images=len(cached_paths),
            progress=f"{i + 1}/{len(properties)}",
        )

        # Compute embeddings in batch
        embeddings = encoder.encode_batch(cached_paths)

        # Store as list of base64-encoded embeddings with index mapping
        gallery_embeddings = []
        for j, emb in enumerate(embeddings):
            if np.any(emb != 0):  # Skip zero vectors (failed images)
                gallery_embeddings.append({
                    "index": j,
                    "url": gallery_urls[j] if j < len(gallery_urls) else "",
                    "embedding": embedding_to_base64(emb),
                })

        detail["gallery_embeddings"] = gallery_embeddings
        total_embedded += 1

    # Write back
    path.write_text(json.dumps(data, indent=2, default=str))

    logger.info(
        "embedding_complete",
        path=str(path),
        embedded=total_embedded,
        skipped=total_skipped,
        already=total_already,
    )

    print(
        f"Embedded {path.name}: {total_embedded} properties, "
        f"{total_skipped} skipped (no cache), {total_already} already done"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Add SSCD embeddings to snapshot files")
    parser.add_argument("snapshots", nargs="+", type=Path, help="Snapshot JSON file(s)")
    parser.add_argument(
        "--max-images",
        type=int,
        default=15,
        help="Max gallery images to embed per property",
    )
    args = parser.parse_args()

    configure_logging(json_output=False, level=logging.INFO)

    for path in args.snapshots:
        if not path.exists():
            print(f"Error: {path} not found")
            continue
        embed_snapshot(path, max_images=args.max_images)


if __name__ == "__main__":
    main()
