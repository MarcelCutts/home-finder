"""Image hashing utilities for property deduplication."""

import asyncio
import io
from typing import TYPE_CHECKING

import httpx
import imagehash
from PIL import Image

from home_finder.logging import get_logger

if TYPE_CHECKING:
    from home_finder.models import Property

logger = get_logger(__name__)

# Limit decompression bomb threshold for untrusted image bytes.
Image.MAX_IMAGE_PIXELS = 50_000_000

# Hamming distance threshold for considering images "same"
# Start conservative (8), tune up based on false negative rate.
# Research suggests 10-12 for 64-bit pHash, but real estate images
# have watermarks/crops that increase variance.
HASH_DISTANCE_THRESHOLD = 8


async def fetch_and_hash_image(url: str, timeout: float = 10.0) -> str | None:
    """Fetch image from URL and compute perceptual hash.

    Args:
        url: Image URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        Hex string of perceptual hash, or None if failed.
    """
    try:
        async with httpx.AsyncClient() as client:
            # Handle protocol-relative URLs
            if url.startswith("//"):
                url = "https:" + url

            response = await client.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()

            # Load image and compute hash
            image = Image.open(io.BytesIO(response.content))
            phash = imagehash.phash(image)
            return str(phash)

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

    async def fetch_one(prop: "Property") -> tuple[str, str | None]:
        async with semaphore:
            if prop.image_url:
                hash_val = await fetch_and_hash_image(str(prop.image_url))
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
