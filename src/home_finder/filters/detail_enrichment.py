"""Detail enrichment pipeline step: fetch detail pages and populate images."""

from pydantic import HttpUrl

from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, PropertyImage
from home_finder.scrapers.detail_fetcher import DetailFetcher

logger = get_logger(__name__)

VALID_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _is_valid_image_url(url: str) -> bool:
    """Check if URL points to a supported image format (not PDF)."""
    path = url.split("?")[0].lower()
    return path.endswith(VALID_IMAGE_EXTENSIONS)


async def enrich_merged_properties(
    merged_properties: list[MergedProperty],
    detail_fetcher: DetailFetcher,
) -> list[MergedProperty]:
    """Fetch detail pages for all sources and populate images, floorplan, descriptions.

    Args:
        merged_properties: Properties to enrich.
        detail_fetcher: DetailFetcher instance for HTTP requests.

    Returns:
        List of MergedProperty with images, floorplan, and descriptions populated.
    """
    results: list[MergedProperty] = []

    for merged in merged_properties:
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
                    for img_url in detail_data.gallery_urls:
                        all_images.append(
                            PropertyImage(
                                url=HttpUrl(img_url),
                                source=source,
                                image_type="gallery",
                            )
                        )

                if (
                    detail_data.floorplan_url
                    and not floorplan_image
                    and _is_valid_image_url(detail_data.floorplan_url)
                ):
                    floorplan_image = PropertyImage(
                        url=HttpUrl(detail_data.floorplan_url),
                        source=source,
                        image_type="floorplan",
                    )

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

        results.append(updated)

    return results


def filter_by_floorplan(properties: list[MergedProperty]) -> list[MergedProperty]:
    """Drop properties that have no valid image-format floorplan.

    Args:
        properties: Enriched MergedProperty list.

    Returns:
        Properties that have a floorplan.
    """
    return [p for p in properties if p.floorplan is not None]
