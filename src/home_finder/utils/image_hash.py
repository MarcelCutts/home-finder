"""Image hashing utilities for property deduplication."""

import asyncio
import io
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import imagehash
from PIL import Image

import home_finder.utils.image_processing  # noqa: F401  (sets MAX_IMAGE_PIXELS)
from home_finder.logging import get_logger

if TYPE_CHECKING:
    from home_finder.models import Property

logger = get_logger(__name__)

# Hamming distance threshold for considering images "same"
# Start conservative (8), tune up based on false negative rate.
# Research suggests 10-12 for 64-bit pHash, but real estate images
# have watermarks/crops that increase variance.
HASH_DISTANCE_THRESHOLD = 8

# File prefixes/content markers for SVGs (can't be perceptually hashed)
_SVG_EXTENSIONS = (".svg",)
_SVG_CONTENT_PREFIXES = (b"<?xml", b"<svg")


async def fetch_and_hash_image(
    url: str, timeout: float = 10.0, *, client: httpx.AsyncClient | None = None
) -> str | None:
    """Fetch image from URL and compute perceptual hash.

    Args:
        url: Image URL to fetch.
        timeout: Request timeout in seconds.
        client: Optional shared HTTP client.

    Returns:
        Hex string of perceptual hash, or None if failed.
    """
    try:
        # Handle protocol-relative URLs
        if url.startswith("//"):
            url = "https:" + url

        if client is not None:
            response = await client.get(url, timeout=timeout, follow_redirects=True)
        else:
            async with httpx.AsyncClient() as c:
                response = await c.get(url, timeout=timeout, follow_redirects=True)

        response.raise_for_status()

        def _compute_hash(data: bytes) -> str:
            image = Image.open(io.BytesIO(data))
            return str(imagehash.phash(image))

        return await asyncio.to_thread(_compute_hash, response.content)

    except Exception as e:
        logger.debug("image_hash_failed", url=url, error=str(e))
        return None


def hashes_match(hash1: str | None, hash2: str | None) -> bool:
    """Check if two image hashes are similar enough to be the same image.

    Args:
        hash1: First hash (hex string).
        hash2: Second hash (hex string).

    Returns:
        True if hashes are within threshold distance.
    """
    if not hash1 or not hash2:
        return False

    try:
        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)
        distance = h1 - h2  # Hamming distance
        return distance <= HASH_DISTANCE_THRESHOLD
    except Exception:
        logger.debug("hash_comparison_failed", hash1=hash1, hash2=hash2, exc_info=True)
        return False


async def fetch_image_hashes_batch(
    properties: list["Property"],
    max_concurrent: int = 10,
) -> dict[str, str]:
    """Fetch image hashes for multiple properties with controlled concurrency.

    Args:
        properties: Properties to fetch hashes for.
        max_concurrent: Maximum concurrent HTTP requests.

    Returns:
        Dict mapping property unique_id to hash string.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async with httpx.AsyncClient() as client:

        async def fetch_one(prop: "Property") -> tuple[str, str | None]:
            async with semaphore:
                if prop.image_url:
                    hash_val = await fetch_and_hash_image(
                        str(prop.image_url), client=client
                    )
                    return (prop.unique_id, hash_val)
                return (prop.unique_id, None)

        tasks = [fetch_one(p) for p in properties]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    hashes = {}
    for result in results:
        if isinstance(result, tuple) and result[1] is not None:
            hashes[result[0]] = result[1]

    logger.info(
        "image_hashes_fetched",
        total=len(properties),
        successful=len(hashes),
    )

    return hashes


def hash_from_disk(path: Path) -> str | None:
    """Read an image file from disk and compute its perceptual hash.

    Skips SVGs (by extension and content prefix). Returns None on any error.

    Args:
        path: Path to the image file.

    Returns:
        Hex string of perceptual hash, or None if failed/skipped.
    """
    try:
        if path.suffix.lower() in _SVG_EXTENSIONS:
            return None

        data = path.read_bytes()
        if not data:
            return None

        # Check for SVG content even if extension doesn't say so
        # Use 64 bytes to handle BOM (\xef\xbb\xbf) or leading whitespace
        content_start = data[:64].lstrip()
        if content_start.startswith(_SVG_CONTENT_PREFIXES):
            return None

        image = Image.open(io.BytesIO(data))
        return str(imagehash.phash(image))
    except Exception as e:
        logger.debug("hash_from_disk_failed", path=str(path), error=str(e))
        return None


async def hash_cached_gallery(
    unique_ids: list[str],
    data_dir: str,
) -> dict[str, list[str]]:
    """Hash all cached gallery images for the given property IDs.

    Reads gallery_* files from the image cache directory for each property
    and computes perceptual hashes. Runs in a thread pool since pHash
    computation is CPU-bound and file I/O is blocking.

    Args:
        unique_ids: Property unique_ids to hash galleries for.
        data_dir: Base data directory containing image_cache/.

    Returns:
        Dict mapping unique_id to list of hash hex strings.
    """
    from home_finder.utils.image_cache import get_cache_dir

    def _hash_all() -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for uid in unique_ids:
            cache_dir = get_cache_dir(data_dir, uid)
            if not cache_dir.is_dir():
                continue
            hashes: list[str] = []
            for img_path in sorted(cache_dir.glob("gallery_*")):
                h = hash_from_disk(img_path)
                if h is not None:
                    hashes.append(h)
            if hashes:
                result[uid] = hashes
        return result

    return await asyncio.to_thread(_hash_all)


def count_gallery_hash_matches(
    hashes1: list[str] | None,
    hashes2: list[str] | None,
) -> int:
    """Count how many images from gallery 1 match images from gallery 2.

    For each hash in hashes1, checks if it matches any hash in hashes2
    (within Hamming distance threshold). Each hash in hashes2 can only
    be matched once to avoid double-counting.

    Args:
        hashes1: List of hash hex strings for first property's gallery.
        hashes2: List of hash hex strings for second property's gallery.

    Returns:
        Number of distinct matching image pairs across the two galleries.
    """
    if not hashes1 or not hashes2:
        return 0

    matched_indices: set[int] = set()
    count = 0

    for h1 in hashes1:
        for j, h2 in enumerate(hashes2):
            if j not in matched_indices and hashes_match(h1, h2):
                count += 1
                matched_indices.add(j)
                break

    return count
