"""Detail enrichment pipeline step: fetch detail pages and populate images."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import HttpUrl

from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, PropertyImage
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.utils.image_cache import (
    get_cached_image_path,
    is_property_cached,
    is_valid_image_url,
    save_image_bytes,
)

if TYPE_CHECKING:
    from home_finder.db import PropertyStorage

logger = get_logger(__name__)


_ENRICHMENT_CONCURRENCY = 5


async def _enrich_single(
    merged: MergedProperty,
    detail_fetcher: DetailFetcher,
    semaphore: asyncio.Semaphore,
    data_dir: str | None = None,
) -> MergedProperty:
    """Enrich a single merged property with detail page data."""
    async with semaphore:
        prop = merged.canonical
        all_images: list[PropertyImage] = []
        floorplan_image: PropertyImage | None = None
        best_description: str | None = None
        best_features: list[str] | None = None

        for source, url in merged.source_urls.items():
            temp_prop = Property(
                source=source,
                source_id=prop.source_id,
                url=url,
                title=prop.title,
                price_pcm=prop.price_pcm,
                bedrooms=prop.bedrooms,
                address=prop.address,
                postcode=prop.postcode,
                latitude=prop.latitude,
                longitude=prop.longitude,
            )

            detail_data = await detail_fetcher.fetch_detail_page(temp_prop)

            if detail_data:
                if detail_data.gallery_urls:
                    for idx, img_url in enumerate(detail_data.gallery_urls):
                        all_images.append(
                            PropertyImage(
                                url=HttpUrl(img_url),
                                source=source,
                                image_type="gallery",
                            )
                        )
                        # Download and cache image bytes
                        if data_dir:
                            cache_path = get_cached_image_path(
                                data_dir, merged.unique_id, img_url, "gallery", idx
                            )
                            if not cache_path.is_file():
                                img_bytes = await detail_fetcher.download_image_bytes(img_url)
                                if img_bytes:
                                    save_image_bytes(cache_path, img_bytes)

                if (
                    detail_data.floorplan_url
                    and not floorplan_image
                    and is_valid_image_url(detail_data.floorplan_url)
                ):
                    floorplan_image = PropertyImage(
                        url=HttpUrl(detail_data.floorplan_url),
                        source=source,
                        image_type="floorplan",
                    )
                    # Download and cache floorplan
                    if data_dir:
                        cache_path = get_cached_image_path(
                            data_dir,
                            merged.unique_id,
                            detail_data.floorplan_url,
                            "floorplan",
                            0,
                        )
                        if not cache_path.is_file():
                            fp_bytes = await detail_fetcher.download_image_bytes(
                                detail_data.floorplan_url
                            )
                            if fp_bytes:
                                save_image_bytes(cache_path, fp_bytes)

                if detail_data.description and (
                    not best_description or len(detail_data.description) > len(best_description)
                ):
                    best_description = detail_data.description

                if detail_data.features and (
                    not best_features or len(detail_data.features) > len(best_features)
                ):
                    best_features = detail_data.features

        updated = MergedProperty(
            canonical=merged.canonical,
            sources=merged.sources,
            source_urls=merged.source_urls,
            images=tuple(all_images),
            floorplan=floorplan_image,
            min_price=merged.min_price,
            max_price=merged.max_price,
            descriptions=merged.descriptions,
        )

        logger.info(
            "enriched_property",
            property_id=merged.unique_id,
            sources=[s.value for s in merged.sources],
            gallery_count=len(all_images),
            has_floorplan=floorplan_image is not None,
        )

        return updated


async def _load_cached_property(
    merged: MergedProperty,
    storage: PropertyStorage,
) -> MergedProperty:
    """Reconstruct a MergedProperty's images from the DB for a cached property.

    This allows downstream steps (floorplan gate, quality analysis) to work
    on properties that were skipped during enrichment.
    """
    images = await storage.get_property_images(merged.unique_id)
    gallery = tuple(img for img in images if img.image_type == "gallery")
    floorplan = next((img for img in images if img.image_type == "floorplan"), None)

    return MergedProperty(
        canonical=merged.canonical,
        sources=merged.sources,
        source_urls=merged.source_urls,
        images=gallery,
        floorplan=floorplan,
        min_price=merged.min_price,
        max_price=merged.max_price,
        descriptions=merged.descriptions,
    )


async def enrich_merged_properties(
    merged_properties: list[MergedProperty],
    detail_fetcher: DetailFetcher,
    *,
    data_dir: str | None = None,
    storage: PropertyStorage | None = None,
) -> list[MergedProperty]:
    """Fetch detail pages for all sources and populate images, floorplan, descriptions.

    Fetches up to _ENRICHMENT_CONCURRENCY properties in parallel.
    Properties with cached images on disk are skipped (no HTTP requests).

    Args:
        merged_properties: Properties to enrich.
        detail_fetcher: DetailFetcher instance for HTTP requests.
        data_dir: Data directory for image cache. None disables caching.
        storage: DB storage for loading cached property images. Required when
            data_dir is set to reconstruct images for skipped properties.

    Returns:
        List of MergedProperty with images, floorplan, and descriptions populated.
    """
    to_enrich: list[MergedProperty] = []
    cached_results: list[MergedProperty] = []

    for merged in merged_properties:
        if data_dir and storage and is_property_cached(data_dir, merged.unique_id):
            logger.info("skipping_enriched_property", property_id=merged.unique_id)
            loaded = await _load_cached_property(merged, storage)
            cached_results.append(loaded)
        else:
            to_enrich.append(merged)

    semaphore = asyncio.Semaphore(_ENRICHMENT_CONCURRENCY)
    tasks = [_enrich_single(merged, detail_fetcher, semaphore, data_dir) for merged in to_enrich]
    enriched = list(await asyncio.gather(*tasks))

    return cached_results + enriched


def filter_by_floorplan(properties: list[MergedProperty]) -> list[MergedProperty]:
    """Drop properties that have no valid image-format floorplan.

    Args:
        properties: Enriched MergedProperty list.

    Returns:
        Properties that have a floorplan.
    """
    return [p for p in properties if p.floorplan is not None]
