"""The three short lists that lead a listing page: what fits, what doesn't, what's unknown."""

from typing import Final, TypedDict

from home_finder.analysis.image_review import SUPPLIED_PHOTO_LOWLIGHT_LABELS
from home_finder.filters.fit_score import FitDimension
from home_finder.models import PropertyLowlight, PropertyQualityAnalysis

# Photo-coverage tags say less than the red flags about which rooms are missing.
_PHOTO_LOWLIGHTS: Final = frozenset(
    {
        PropertyLowlight.NO_INTERIOR_PHOTOS,
        PropertyLowlight.NO_BATHROOM_PHOTOS,
        PropertyLowlight.MISSING_KEY_PHOTOS,
        *SUPPLIED_PHOTO_LOWLIGHT_LABELS.values(),
    }
)


class Verdict(TypedDict):
    fits: list[str]
    watch: list[str]
    find_out: list[str]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def build_verdict(
    analysis: PropertyQualityAnalysis, fit_breakdown: list[FitDimension] | None
) -> Verdict:
    """Sort the analysis into what fits the brief, what to watch and what is still unknown.

    Each list keeps the analysis's own order of importance: its top highlights and
    lowlights come first, then the longer condition and listing observations. A room
    or view the photos leave out is a gap in the evidence, not a fault in the home,
    so it is something to find out, ahead of the details the analysis has no reading of.
    """
    extraction = analysis.listing_extraction
    listing_epc = extraction is not None and extraction.epc_rating not in (None, "unknown")
    # An EPC highlight would repeat the band shown with the costs.
    fits = [
        tag for tag in analysis.highlights or [] if not (listing_epc and tag[:3].upper() == "EPC")
    ]

    flags = analysis.listing_red_flags
    # The flags list the photo gaps first, then the concerns the listing text raised.
    listing_concerns = list(flags.description_concerns) if flags is not None else []
    photo_gaps = (
        [item for item in flags.display_items if item not in listing_concerns]
        if flags is not None
        else []
    )
    lowlights = analysis.display_lowlights or []
    if not photo_gaps:
        # Any photo red flag replaces the photo tags, which say less about what is missing.
        photo_gaps = [tag for tag in lowlights if tag in _PHOTO_LOWLIGHTS]
    watch = [
        tag
        for tag in lowlights
        if tag != PropertyLowlight.SERVICE_CHARGE_UNSTATED and tag not in _PHOTO_LOWLIGHTS
    ]
    maintenance = analysis.condition.maintenance_concerns if analysis.condition_concerns else []
    if analysis.concern_severity == "serious":
        watch = [*maintenance, *watch]
    else:
        watch = [*watch, *maintenance]
    watch.extend(listing_concerns)

    # A service charge is the landlord's cost, so an unstated one is nothing for the tenant
    # to find out; the lowlight stays out of every list.
    find_out = list(photo_gaps)
    for dimension in fit_breakdown or []:
        find_out.extend(f["label"] for f in dimension["factors"] if f["state"] == "unknown")

    return Verdict(fits=_unique(fits), watch=_unique(watch), find_out=_unique(find_out))
