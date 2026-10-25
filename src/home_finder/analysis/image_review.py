"""Keep partial photo reviews from asserting listing-wide omissions."""

from copy import deepcopy
from typing import Any, Final

from home_finder.models.analysis import ImageReviewCoverage
from home_finder.models.quality import PropertyLowlight, PropertyQualityAnalysis

SUPPLIED_PHOTO_LOWLIGHT_LABELS: Final[dict[str, str]] = {
    PropertyLowlight.NO_INTERIOR_PHOTOS: "No interior photos supplied",
    PropertyLowlight.NO_BATHROOM_PHOTOS: "No bathroom photos supplied",
    PropertyLowlight.MISSING_KEY_PHOTOS: "Key rooms not shown in supplied photos",
}


def reconcile_image_review(
    visual_data: dict[str, Any], coverage: ImageReviewCoverage
) -> tuple[dict[str, Any], ImageReviewCoverage]:
    """Qualify missing-room observations without mutating a paid checkpoint.

    Photo-completeness accusations are unknown for a partial review. Keep
    description concerns and structured room/condition observations intact.
    """
    if not coverage.is_partial:
        return visual_data, coverage
    data = deepcopy(visual_data)
    flags = data.get("listing_red_flags")
    if isinstance(flags, dict):
        rooms = tuple(flags.get("missing_room_photos", []))
        coverage = coverage.model_copy(update={"rooms_not_seen_in_review": rooms})
        if rooms or flags.get("too_few_photos") or flags.get("selective_angles"):
            # A summary can repeat the rejected accusation. Replace it with
            # truthful scope instead of trying to classify sentences by regex.
            data["summary"] = (
                f"Reviewed {coverage.reviewed_gallery_count} of "
                f"{coverage.available_gallery_count} available gallery photos. "
                "Other photos were not reviewed; listing-wide photo completeness is unknown."
            )
            for room in rooms:
                # Only explicitly named room sections; do not infer which room
                # an unseen photo depicts, or rewrite unrelated observations.
                section = room.strip().casefold()
                if section in {"bathroom", "kitchen", "bedroom"} and isinstance(
                    data.get(section), dict
                ):
                    data[section]["notes"] = (
                        f"{section.capitalize()} not seen in reviewed photos. "
                        "Other gallery photos were not reviewed; verify at viewing."
                    )
        # Do not forward non-schema photo-accusation prose (such as an extra
        # 'notes' field) to evaluation alongside the cleared structured flags.
        data["listing_red_flags"] = {
            "missing_room_photos": [],
            "too_few_photos": False,
            "selective_angles": False,
            "description_concerns": flags.get("description_concerns", []),
            "red_flag_count": len(flags.get("description_concerns", [])),
        }
    return data, coverage


def apply_image_review_coverage(
    analysis: PropertyQualityAnalysis, coverage: ImageReviewCoverage
) -> PropertyQualityAnalysis:
    """Attach trusted coverage and enforce the same rule on curated badges."""
    lowlights = analysis.lowlights
    if lowlights is not None:
        lowlights = [
            SUPPLIED_PHOTO_LOWLIGHT_LABELS.get(tag, tag)
            for tag in lowlights
            if not (coverage.is_partial and tag in SUPPLIED_PHOTO_LOWLIGHT_LABELS)
        ]
    return analysis.model_copy(update={"image_review_coverage": coverage, "lowlights": lowlights})
