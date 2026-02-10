"""Async gallery image hashing with pHash + wHash + crop-resistant hash.

Uses curl_cffi for Zoopla/OnTheMarket CDN images (TLS fingerprinting),
httpx for everything else. Computes pHash, wHash, and crop-resistant hash
per image for complementary robustness:
- pHash: compression artifacts
- wHash: watermarks
- crop_hash: up to 50% crop (vs ~5% for standard pHash)
"""

import asyncio
import io
from dataclasses import dataclass

import httpx
import imagehash
from curl_cffi.requests import AsyncSession
from PIL import Image

from home_finder.logging import get_logger

logger = get_logger(__name__)

# Hamming distance thresholds (64-bit hashes)
PHASH_THRESHOLD = 10  # pHash: good at compression, looser than production's 8
WHASH_THRESHOLD = 6   # wHash: tighter — modern interiors cause false positives at 10
CROP_HASH_THRESHOLD = 6  # crop_resistant_hash: handles up to ~50% crop

# Domains that need curl_cffi for anti-bot bypass
CURL_CFFI_DOMAINS = {"zoopla.co.uk", "onthemarket.com", "zoocdn.com", "otm-assets"}


@dataclass
class ImageHashes:
    """Triple hash result for a single image."""

    url: str
    phash: str | None = None
    whash: str | None = None
    crop_hash: str | None = None


def _needs_curl_cffi(url: str) -> bool:
    """Check if URL needs curl_cffi (anti-bot sites)."""
    return any(domain in url for domain in CURL_CFFI_DOMAINS)


def _compute_hashes(image_bytes: bytes) -> tuple[str | None, str | None, str | None]:
    """Compute pHash, wHash, and crop-resistant hash from image bytes."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        ph = str(imagehash.phash(image))
        wh = str(imagehash.whash(image))
        try:
            ch = str(imagehash.crop_resistant_hash(image))
        except Exception:
            ch = None
        return ph, wh, ch
    except Exception as e:
        logger.debug("hash_compute_failed", error=str(e))
        return None, None, None


async def _fetch_with_httpx(url: str, timeout: float = 15.0) -> bytes | None:
    """Fetch image bytes using httpx."""
    try:
        async with httpx.AsyncClient() as client:
            if url.startswith("//"):
                url = "https:" + url
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.debug("httpx_fetch_failed", url=url[:80], error=str(e))
        return None


async def _fetch_with_curl_cffi(url: str, timeout: float = 15.0) -> bytes | None:
    """Fetch image bytes using curl_cffi (for anti-bot sites)."""
    try:
        async with AsyncSession() as session:
            if url.startswith("//"):
                url = "https:" + url
            resp = await session.get(
                url,
                impersonate="chrome",
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.content
            logger.debug("curl_cffi_bad_status", url=url[:80], status=resp.status_code)
            return None
    except Exception as e:
        logger.debug("curl_cffi_fetch_failed", url=url[:80], error=str(e))
        return None


async def hash_image(url: str) -> ImageHashes:
    """Download and hash a single image with appropriate client."""
    if _needs_curl_cffi(url):
        data = await _fetch_with_curl_cffi(url)
    else:
        data = await _fetch_with_httpx(url)

    if not data:
        return ImageHashes(url=url)

    ph, wh, ch = _compute_hashes(data)
    return ImageHashes(url=url, phash=ph, whash=wh, crop_hash=ch)


async def hash_gallery(
    urls: list[str],
    *,
    max_concurrent: int = 5,
    max_images: int = 15,
) -> list[ImageHashes]:
    """Hash multiple gallery images with concurrency control.

    Args:
        urls: Gallery image URLs.
        max_concurrent: Max simultaneous downloads.
        max_images: Max images to hash (skip the rest).

    Returns:
        List of ImageHashes (only those with at least one hash).
    """
    urls = urls[:max_images]
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _bounded_hash(url: str) -> ImageHashes:
        async with semaphore:
            result = await hash_image(url)
            await asyncio.sleep(0.3)  # Rate limit
            return result

    tasks = [_bounded_hash(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    hashes = []
    for r in results:
        if isinstance(r, ImageHashes) and (r.phash or r.whash or r.crop_hash):
            hashes.append(r)

    return hashes


def hashes_match(a: ImageHashes, b: ImageHashes) -> bool:
    """Check if two image hashes match using pHash + wHash + crop_hash.

    Match if ANY hash is within threshold — they're complementary:
    pHash handles compression, wHash handles watermarks,
    crop_hash handles up to ~50% crop.
    """
    phash_match = False
    whash_match = False
    crop_match = False

    if a.phash and b.phash:
        try:
            dist = imagehash.hex_to_hash(a.phash) - imagehash.hex_to_hash(b.phash)
            phash_match = dist <= PHASH_THRESHOLD
        except Exception:
            pass

    if a.whash and b.whash:
        try:
            dist = imagehash.hex_to_hash(a.whash) - imagehash.hex_to_hash(b.whash)
            whash_match = dist <= WHASH_THRESHOLD
        except Exception:
            pass

    if a.crop_hash and b.crop_hash:
        try:
            dist = imagehash.hex_to_hash(a.crop_hash) - imagehash.hex_to_hash(b.crop_hash)
            crop_match = dist <= CROP_HASH_THRESHOLD
        except Exception:
            pass

    return phash_match or whash_match or crop_match


def count_gallery_matches(
    gallery_a: list[ImageHashes],
    gallery_b: list[ImageHashes],
) -> tuple[int, list[tuple[str, str]]]:
    """Count matching images between two galleries.

    Returns (match_count, list of (url_a, url_b) matched pairs).
    """
    if not gallery_a or not gallery_b:
        return 0, []

    matched_b: set[int] = set()
    pairs: list[tuple[str, str]] = []

    for a in gallery_a:
        for j, b in enumerate(gallery_b):
            if j in matched_b:
                continue
            if hashes_match(a, b):
                matched_b.add(j)
                pairs.append((a.url, b.url))
                break  # Each image in A matches at most one in B

    return len(pairs), pairs
