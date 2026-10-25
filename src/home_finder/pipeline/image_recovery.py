"""Restore analysis media from stored URLs without fetching detail pages again."""

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import get_logger
from home_finder.models import PropertyAggregate
from home_finder.pipeline.cache_recovery import repair_merged_image_caches
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.utils.image_cache import (
    find_cached_file,
    gallery_cache_coverage,
    get_cached_image_path,
    is_valid_image_url,
    save_image_bytes,
)

logger = get_logger(__name__)


async def restore_missing_analysis_images(
    queue: list[PropertyAggregate],
    settings: Settings,
    storage: PropertyStorage,
) -> list[PropertyAggregate]:
    """Attempt to restore missing cached images for pending analysis work.

    Checks each property's gallery images and floorplan against the disk cache. For any
    with gaps, downloads the missing files directly from the image URLs
    already stored on the PropertyAggregate (no detail-page re-scraping).

    Returns the queue unchanged — the same PropertyAggregate objects, but now
    with any successfully restored cache files. Analysis still checks readiness.
    """
    if not settings.data_dir:
        return queue

    await repair_merged_image_caches(storage, settings.data_dir, [m.unique_id for m in queue])
    # Find properties with incomplete caches
    incomplete: list[PropertyAggregate] = []
    for merged in queue:
        gallery_urls = [str(img.url) for img in merged.images if img.image_type == "gallery"]
        has_fp = merged.floorplan is not None and is_valid_image_url(str(merged.floorplan.url))
        cached, expected = gallery_cache_coverage(
            settings.data_dir,
            merged.unique_id,
            gallery_urls,
            has_valid_floorplan=has_fp,
            max_images=settings.quality_filter_max_images,
        )
        missing_floorplan = (
            has_fp
            and merged.floorplan is not None
            and find_cached_file(
                settings.data_dir, merged.unique_id, str(merged.floorplan.url), "floorplan"
            )
            is None
        )
        if cached < expected or missing_floorplan:
            incomplete.append(merged)

    if not incomplete:
        return queue

    logger.info("downloading_missing_cache_images", count=len(incomplete))

    async with DetailFetcher(
        max_gallery_images=settings.quality_filter_max_images,
        proxy_url=settings.proxy_url,
    ) as detail_fetcher:
        total_downloaded = 0
        for merged in incomplete:
            downloaded = await download_missing_images(
                merged,
                detail_fetcher,
                settings.data_dir,
                settings.quality_filter_max_images,
            )
            total_downloaded += downloaded

    logger.info(
        "missing_image_downloads_complete",
        properties=len(incomplete),
        images_downloaded=total_downloaded,
    )

    return queue


async def download_missing_images(
    merged: PropertyAggregate,
    detail_fetcher: DetailFetcher,
    data_dir: str,
    max_images: int,
) -> int:
    """Download gallery images and the floorplan missing from disk cache.

    Uses URLs already on the PropertyAggregate (loaded from DB by the reanalysis
    queue) — no detail-page fetching required.

    Returns:
        Number of images successfully downloaded.
    """
    has_fp = merged.floorplan is not None and is_valid_image_url(str(merged.floorplan.url))
    effective_max = max_images - (1 if has_fp else 0)
    gallery = [img for img in merged.images if img.image_type == "gallery"]
    to_check = gallery[:effective_max]
    if has_fp and merged.floorplan is not None:
        to_check.append(merged.floorplan)

    downloaded = 0
    for idx, img in enumerate(to_check):
        url_str = str(img.url)
        image_type = img.image_type
        if find_cached_file(data_dir, merged.unique_id, url_str, image_type):
            continue
        img_bytes = await detail_fetcher.download_image_bytes(url_str)
        if img_bytes:
            path = get_cached_image_path(data_dir, merged.unique_id, url_str, image_type, idx)
            save_image_bytes(path, img_bytes)
            downloaded += 1
        else:
            logger.warning(
                "missing_image_download_failed",
                property_id=merged.unique_id,
                url=url_str,
            )
    return downloaded
