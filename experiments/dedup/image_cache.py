"""Local image cache for dedup experiments.

Downloads gallery images to data/images/{source}_{source_id}/000.jpg etc.
Enables re-running hash/embedding computation without re-downloading.
Uses curl_cffi for Zoopla/OTM (anti-bot), httpx for others. Idempotent.

Usage:
    uv run python image_cache.py data/snapshots/snapshot_*.json
    uv run python image_cache.py data/snapshots/snapshot_*.json --max-images 15
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx
from curl_cffi.requests import AsyncSession

from home_finder.logging import configure_logging, get_logger

logger = get_logger(__name__)

IMAGES_DIR = Path(__file__).parent / "data" / "images"

# Domains that need curl_cffi for anti-bot bypass
CURL_CFFI_DOMAINS = {"zoopla.co.uk", "onthemarket.com", "zoocdn.com", "otm-assets"}


def _needs_curl_cffi(url: str) -> bool:
    return any(domain in url for domain in CURL_CFFI_DOMAINS)


def _property_dir(source: str, source_id: str) -> Path:
    """Get cache directory for a property's images."""
    return IMAGES_DIR / f"{source}_{source_id}"


def get_cached_images(source: str, source_id: str) -> list[Path]:
    """Return sorted list of cached image paths for a property."""
    prop_dir = _property_dir(source, source_id)
    if not prop_dir.exists():
        return []
    return sorted(prop_dir.glob("*.jpg"))


async def _download_with_httpx(url: str, dest: Path, timeout: float = 15.0) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            if url.startswith("//"):
                url = "https:" + url
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return True
    except Exception as e:
        logger.debug("httpx_download_failed", url=url[:80], error=str(e))
        return False


async def _download_with_curl_cffi(url: str, dest: Path, timeout: float = 15.0) -> bool:
    try:
        async with AsyncSession() as session:
            if url.startswith("//"):
                url = "https:" + url
            resp = await session.get(url, impersonate="chrome", timeout=timeout)
            if resp.status_code == 200:
                dest.write_bytes(resp.content)
                return True
            logger.debug("curl_cffi_bad_status", url=url[:80], status=resp.status_code)
            return False
    except Exception as e:
        logger.debug("curl_cffi_download_failed", url=url[:80], error=str(e))
        return False


async def cache_property_images(
    source: str,
    source_id: str,
    gallery_urls: list[str],
    *,
    max_images: int = 15,
    max_concurrent: int = 5,
) -> list[Path]:
    """Download gallery images to local cache. Idempotent - skips existing files.

    Returns list of cached image paths (including previously cached).
    """
    prop_dir = _property_dir(source, source_id)
    prop_dir.mkdir(parents=True, exist_ok=True)

    urls = gallery_urls[:max_images]
    semaphore = asyncio.Semaphore(max_concurrent)
    cached: list[Path] = []

    async def _download(idx: int, url: str) -> Path | None:
        dest = prop_dir / f"{idx:03d}.jpg"
        if dest.exists() and dest.stat().st_size > 0:
            return dest

        async with semaphore:
            if _needs_curl_cffi(url):
                ok = await _download_with_curl_cffi(url, dest)
            else:
                ok = await _download_with_httpx(url, dest)
            await asyncio.sleep(0.3)  # Rate limit
            return dest if ok else None

    tasks = [_download(i, url) for i, url in enumerate(urls)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Path):
            cached.append(r)

    return cached


async def cache_snapshot(path: Path, *, max_images: int = 15) -> None:
    """Cache all gallery images from a snapshot file."""
    data = json.loads(path.read_text())
    properties = data["properties"]

    total_downloaded = 0
    total_skipped = 0

    for i, prop in enumerate(properties):
        detail = prop.get("detail")
        if not detail:
            continue

        gallery_urls = detail.get("gallery_urls") or []
        if not gallery_urls:
            continue

        source = prop["source"]
        source_id = prop["source_id"]

        # Check how many are already cached
        existing = get_cached_images(source, source_id)
        needed = min(max_images, len(gallery_urls))

        if len(existing) >= needed:
            total_skipped += needed
            continue

        uid = f"{source}:{source_id}"
        logger.info(
            "caching_images",
            unique_id=uid,
            images=needed,
            existing=len(existing),
            progress=f"{i + 1}/{len(properties)}",
        )

        cached = await cache_property_images(
            source, source_id, gallery_urls, max_images=max_images
        )
        total_downloaded += max(0, len(cached) - len(existing))
        total_skipped += len(existing)

    print(
        f"Cached {path.name}: {total_downloaded} downloaded, "
        f"{total_skipped} already cached"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cache gallery images locally")
    parser.add_argument("snapshots", nargs="+", type=Path, help="Snapshot JSON file(s)")
    parser.add_argument(
        "--max-images",
        type=int,
        default=15,
        help="Max gallery images to cache per property",
    )
    args = parser.parse_args()

    configure_logging(json_output=False, level=logging.INFO)

    for path in args.snapshots:
        if not path.exists():
            print(f"Error: {path} not found")
            continue
        await cache_snapshot(path, max_images=args.max_images)


if __name__ == "__main__":
    asyncio.run(main())
