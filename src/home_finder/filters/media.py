"""Admission based on floorplan provenance, source exemptions, or usable photos."""

from home_finder.models import MergedProperty, PropertySource

# Sources that don't support dedicated floorplan images — landlords upload
# photos into a single gallery with no floorplan distinction.
_FLOORPLAN_EXEMPT_SOURCES: frozenset[PropertySource] = frozenset({PropertySource.OPENRENT})


def is_floorplan_exempt(sources: tuple[PropertySource, ...] | set[PropertySource]) -> bool:
    """Check if the given sources are all floorplan-exempt (e.g. OpenRent-only)."""
    return set(sources).issubset(_FLOORPLAN_EXEMPT_SOURCES)


def filter_by_floorplan(
    properties: list[MergedProperty],
    *,
    min_gallery_for_photo_inference: int = 0,
) -> list[MergedProperty]:
    """Admit candidates with source floorplans, exempt sources, or sufficient photos.

    Properties pass through if any of:
    - They have a source-declared floorplan image (dimensions may still be unreadable)
    - They're exclusively from floorplan-exempt sources (e.g. OpenRent)
    - They have enough gallery images for photo-based layout inference
      (when min_gallery_for_photo_inference > 0)

    Args:
        properties: Enriched MergedProperty list.
        min_gallery_for_photo_inference: Minimum gallery images to bypass
            the floorplan gate. 0 disables this bypass (default).

    Returns:
        Properties that have a floorplan, are exempt, or have enough photos.
    """

    def _passes(p: MergedProperty) -> bool:
        if p.floorplan is not None and p.floorplan.is_source_floorplan:
            return True
        if set(p.sources).issubset(_FLOORPLAN_EXEMPT_SOURCES):
            return True
        return (
            min_gallery_for_photo_inference > 0 and len(p.images) >= min_gallery_for_photo_inference
        )

    return [p for p in properties if _passes(p)]
