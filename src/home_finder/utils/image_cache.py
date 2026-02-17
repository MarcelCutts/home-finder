"""Disk-based image cache for property images."""

import hashlib
import re
import shutil
from pathlib import Path
from typing import Final

from home_finder.logging import get_logger

logger = get_logger(__name__)

_IMAGE_CACHE_DIR: Final = "image_cache"

VALID_IMAGE_EXTENSIONS: Final = (".jpg", ".jpeg", ".png", ".gif", ".webp")

_REJECTED_EXTENSIONS: Final = (".pdf", ".svg", ".html", ".js", ".css", ".json", ".xml")


def is_valid_image_url(url: str) -> bool:
    """Check if URL points to a supported image format.

    Rejects known non-image extensions (.pdf, .svg). Extension-less URLs
    pass through since CDNs commonly serve images without extensions
    (e.g. ``https://lc.zoocdn.com/u/floor/abc123``).

    Args:
        url: Image URL to check.

    Returns:
        True if the URL is not a known non-image format.
    """
    path = url.split("?")[0].lower()
    return not path.endswith(_REJECTED_EXTENSIONS)


def safe_dir_name(unique_id: str) -> str:
    """Convert a property unique_id to a filesystem-safe directory name.

    E.g. "openrent:12345" -> "openrent_12345"
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", unique_id)


def get_cache_dir(data_dir: str, unique_id: str) -> Path:
    """Return the cache directory for a property's images."""
    return Path(data_dir) / _IMAGE_CACHE_DIR / safe_dir_name(unique_id)


def url_to_filename(url: str, image_type: str, index: int) -> str:
    """Deterministic filename from URL using MD5 hash prefix.

    E.g. "gallery_003_a1b2c3d4.jpg"
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    # Guess extension from URL
    path = url.split("?")[0].lower()
    ext = "jpg"
    for candidate in (".png", ".webp", ".gif", ".jpeg", ".jpg"):
        if path.endswith(candidate):
            ext = candidate.lstrip(".")
            break
    return f"{image_type}_{index:03d}_{url_hash}.{ext}"


def find_cached_file(
    data_dir: str, unique_id: str, url: str, image_type: str
) -> Path | None:
    """Find a cached image file by URL hash, regardless of index.

    Extracts the URL hash and globs for any file matching the pattern
    ``{image_type}_*_{url_hash}.*``, so the result is independent of the
    index that was used when the file was originally saved.

    Returns:
        Path to the cached file, or None if not found.
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    cache_dir = get_cache_dir(data_dir, unique_id)
    if not cache_dir.is_dir():
        return None
    matches = list(cache_dir.glob(f"{image_type}_*_{url_hash}.*"))
    return matches[0] if matches else None


def copy_cached_images(data_dir: str, from_id: str, to_id: str) -> int:
    """Copy all cached image files from one property to another.

    Skips files that already exist in the target directory (by filename).

    Args:
        data_dir: Base data directory.
        from_id: Source property unique_id.
        to_id: Target property unique_id.

    Returns:
        Number of files copied.
    """
    src_dir = get_cache_dir(data_dir, from_id)
    if not src_dir.is_dir():
        return 0
    dst_dir = get_cache_dir(data_dir, to_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src_file in src_dir.iterdir():
        if not src_file.is_file():
            continue
        dst_file = dst_dir / src_file.name
        if dst_file.exists():
            continue
        shutil.copy2(src_file, dst_file)
        copied += 1
    if copied:
        logger.debug(
            "cached_images_copied", from_id=from_id, to_id=to_id, count=copied
        )
    return copied


def is_property_cached(data_dir: str, unique_id: str) -> bool:
    """Check if a property has cached images on disk."""
    cache_dir = get_cache_dir(data_dir, unique_id)
    if not cache_dir.is_dir():
        return False
    return any(cache_dir.iterdir())


def get_cached_image_path(
    data_dir: str, unique_id: str, url: str, image_type: str, index: int
) -> Path:
    """Return where a specific image would live on disk."""
    return get_cache_dir(data_dir, unique_id) / url_to_filename(url, image_type, index)


def save_image_bytes(path: Path, data: bytes) -> None:
    """Write image bytes to disk, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def clear_image_cache(data_dir: str, unique_id: str) -> None:
    """Remove all cached images for a property.

    Used before re-enrichment to clear partial downloads so
    is_property_cached() returns False.
    """
    cache_dir = get_cache_dir(data_dir, unique_id)
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir)
        logger.debug("image_cache_cleared", unique_id=unique_id)


def read_image_bytes(path: Path) -> bytes | None:
    """Read image bytes from disk, or None if not found."""
    if path.is_file():
        return path.read_bytes()
    return None
