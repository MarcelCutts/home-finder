"""Complete cache consolidation using accepted, durable source memberships."""

import asyncio

from home_finder.db import PropertyStorage
from home_finder.logging import get_logger
from home_finder.utils.image_cache import copy_cached_images

logger = get_logger(__name__)


async def repair_merged_image_caches(
    storage: PropertyStorage, data_dir: str, property_ids: list[str] | None = None
) -> int:
    """Repeat missing copies safely after acceptance, including after a restart."""
    if not data_dir:
        return 0
    memberships = await storage.get_merged_cache_memberships(property_ids)
    copied = 0
    for source_id, anchor_id in memberships:
        copied += copy_cached_images(data_dir, source_id, anchor_id)
        # Keep the event loop responsive between directories; no background
        # filesystem worker may outlive the exclusive runner's ownership.
        await asyncio.sleep(0)
    if copied:
        logger.info("merged_image_cache_repaired", memberships=len(memberships), copied=copied)
    return copied
