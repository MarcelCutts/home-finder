"""What the tool says about a home in short: the listing page's three lists (what fits,
what doesn't, what's unknown) and the queue card's glance icons and highlight tags.

What fits, the card's tags and the card's icons are held to the brief (``web/brief.py``).
The analysis and the scorer read wider signals than the brief accepts as evidence, so
left alone a tag or an icon can show a need as met where the brief says it needs
checking. The other two lists are not held to it: they report what the analysis
found against the home and what it could not see.
"""

from collections.abc import Sequence
from typing import Final, TypedDict

from home_finder.analysis.image_review import SUPPLIED_PHOTO_LOWLIGHT_LABELS
from home_finder.filters.fit_score import FitDimension, LifestyleIcon
from home_finder.models import PropertyHighlight, PropertyLowlight, PropertyQualityAnalysis
from home_finder.web.brief import (
    DIMENSION_NEEDS,
    BriefCheck,
    BriefItem,
    Claim,
    describes_missing_evidence,
)

# Photo-coverage tags say less than the red flags about which rooms are missing.
_PHOTO_LOWLIGHTS: Final = frozenset(
    {
        PropertyLowlight.NO_INTERIOR_PHOTOS,
        PropertyLowlight.NO_BATHROOM_PHOTOS,
        PropertyLowlight.MISSING_KEY_PHOTOS,
        *SUPPLIED_PHOTO_LOWLIGHT_LABELS.values(),
    }
)


# Highlights that assert a need outright. The analysis can tag them from the advert's
# own words (the brief's evidence rules say why), so each stands only where the brief
# finds direct evidence for its need.
_HIGHLIGHT_NEEDS: Final[dict[str, str]] = {
    PropertyHighlight.SPACIOUS_LIVING: "hosting",
    PropertyHighlight.EXCELLENT_CONDITION: "condition",
    PropertyHighlight.RECENTLY_REFURBISHED: "condition",
}

# How a card icon shows each claim.
_ICON_STATE: Final[dict[Claim, str]] = {
    "supported": "good",
    "conflict": "concern",
    "needs_checking": "neutral",
}


class Verdict(TypedDict):
    fits: list[str]
    watch: list[str]
    find_out: list[str]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _brief_lines(check: BriefCheck) -> dict[str, BriefItem]:
    """The brief's line for each fit dimension it covers, by the dimension's key."""
    by_label = {item["label"]: item for item in check["items"]}
    return {key: by_label[label] for key, label in DIMENSION_NEEDS.items() if label in by_label}


def highlights_within_brief(highlights: Sequence[str], check: BriefCheck | None) -> list[str]:
    """Leave out the highlights that assert a need the brief doesn't support.

    "Spacious living room" beside a brief line saying the living room was never
    measured is the page contradicting itself. Without a brief nothing is dropped.
    """
    if check is None:
        return list(highlights)
    lines = _brief_lines(check)

    def stands(tag: str) -> bool:
        line = lines.get(_HIGHLIGHT_NEEDS.get(tag, ""))
        return line is None or line["claim"] == "supported"

    return [tag for tag in highlights if stands(tag)]


def icons_from_brief(
    icons: dict[str, LifestyleIcon] | None, check: BriefCheck
) -> dict[str, LifestyleIcon] | None:
    """Make each card icon show the brief's claim for its need, in the brief's words.

    The scorer draws its icons from wider signals than the brief takes as evidence:
    a general sense of space makes its hosting icon good where the brief, with no
    living-room measurement, says room to host needs checking. An icon whose need has
    a line in the brief takes that line's claim and evidence; the rest (overall
    space, internet) keep the scorer's.
    """
    if icons is None:
        return None
    lines = _brief_lines(check)
    held = dict(icons)
    for key in icons:
        line = lines.get(key)
        if line is not None:
            held[key] = LifestyleIcon(
                state=_ICON_STATE[line["claim"]], tooltip=f"{line['label']}: {line['found']}"
            )
    return held


def build_verdict(
    analysis: PropertyQualityAnalysis,
    fit_breakdown: list[FitDimension] | None,
    brief_check: BriefCheck | None = None,
) -> Verdict:
    """Sort the analysis into what fits the brief, what to watch and what is still unknown.

    Each list keeps the analysis's own order of importance: its top highlights and
    lowlights come first, then the longer condition and listing observations. A room
    or view the photos leave out is a gap in the evidence, not a fault in the home,
    so it is something to find out, ahead of the details the analysis has no reading of.
    That holds whether the analysis flagged the gap or wrote it as a concern. With the
    brief to hand, what fits leaves out the highlights the brief doesn't support.
    """
    extraction = analysis.listing_extraction
    listing_epc = extraction is not None and extraction.epc_rating not in (None, "unknown")
    # An EPC highlight would repeat the band shown with the costs.
    fits = [
        tag for tag in analysis.highlights or [] if not (listing_epc and tag[:3].upper() == "EPC")
    ]
    fits = highlights_within_brief(fits, brief_check)

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
    written_gaps = [
        text for text in [*maintenance, *listing_concerns] if describes_missing_evidence(text)
    ]
    maintenance = [text for text in maintenance if text not in written_gaps]
    if analysis.concern_severity == "serious":
        watch = [*maintenance, *watch]
    else:
        watch = [*watch, *maintenance]
    watch.extend(text for text in listing_concerns if text not in written_gaps)

    # A service charge is the landlord's cost, so an unstated one is nothing for the tenant
    # to find out; the lowlight stays out of every list.
    find_out = [*photo_gaps, *written_gaps]
    for dimension in fit_breakdown or []:
        find_out.extend(f["label"] for f in dimension["factors"] if f["state"] == "unknown")

    return Verdict(fits=_unique(fits), watch=_unique(watch), find_out=_unique(find_out))
