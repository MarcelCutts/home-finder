"""Keep newly assembled display badges consistent with structured evidence."""

from home_finder.logging import get_logger
from home_finder.models import PropertyHighlight, PropertyLowlight, PropertyQualityAnalysis

logger = get_logger(__name__)


def reconcile_analysis_badges(
    analysis: PropertyQualityAnalysis, *, property_id: str | None = None
) -> PropertyQualityAnalysis:
    """Remove unsupported badges after canonical listing-fact corrections.

    This only checks direct structured counterparts; it does not infer facts,
    generate replacement badges or edit prose. Call on new assemblies, never
    while loading historical assessments. Bills must already have passed the
    explicit listing-text evidence check.
    """
    rejected: dict[str, tuple[str, str]] = {}

    def require(
        badge: PropertyHighlight | PropertyLowlight,
        field: str,
        value: str | None,
        allowed: tuple[str, ...],
    ) -> None:
        if value not in allowed:
            reason = (
                "missing_structured_support"
                if value in (None, "unknown")
                else "unsupported_structured_value"
            )
            rejected[badge] = (field, reason)

    hosting = analysis.space.hosting_layout if analysis.space else None
    require(
        PropertyHighlight.GREAT_HOSTING_LAYOUT,
        "space.hosting_layout",
        hosting,
        ("excellent", "good"),
    )
    require(PropertyLowlight.POOR_HOSTING_LAYOUT, "space.hosting_layout", hosting, ("poor",))
    require(
        PropertyHighlight.BILLS_INCLUDED,
        "listing_extraction.bills_included",
        analysis.listing_extraction.bills_included if analysis.listing_extraction else None,
        ("yes",),
    )
    kitchen = analysis.kitchen
    storage = analysis.storage
    for positive, negative, field, value in (
        (
            PropertyHighlight.DISHWASHER,
            PropertyLowlight.NO_DISHWASHER,
            "kitchen.has_dishwasher",
            kitchen.has_dishwasher if kitchen else None,
        ),
        (
            PropertyHighlight.WASHING_MACHINE,
            PropertyLowlight.NO_WASHING_MACHINE,
            "kitchen.has_washing_machine",
            kitchen.has_washing_machine if kitchen else None,
        ),
        (
            PropertyHighlight.BUILT_IN_WARDROBES,
            PropertyLowlight.NO_BUILT_IN_WARDROBES,
            "storage.has_built_in_wardrobes",
            storage.has_built_in_wardrobes if storage else None,
        ),
    ):
        require(positive, field, value, ("yes",))
        require(negative, field, value, ("no",))
    require(
        PropertyHighlight.DOUBLE_GLAZING,
        "flooring_noise.has_double_glazing",
        analysis.flooring_noise.has_double_glazing if analysis.flooring_noise else None,
        ("yes",),
    )
    outdoor = analysis.outdoor_space
    outdoor_state = outdoor.effective_availability if outdoor else "unknown"
    require(
        PropertyLowlight.NO_OUTDOOR_SPACE,
        "outdoor_space.availability",
        outdoor_state,
        ("no",),
    )
    # False type flags remain unobserved; known positive flags establish access.
    # Do not use unknown type flags to reject positive outdoor badges.
    if outdoor_state == "no":
        for badge in (
            PropertyHighlight.PRIVATE_BALCONY,
            PropertyHighlight.PRIVATE_GARDEN,
            PropertyHighlight.PRIVATE_TERRACE,
            PropertyHighlight.SHARED_GARDEN,
            PropertyHighlight.COMMUNAL_GARDENS,
            PropertyHighlight.ROOF_TERRACE,
        ):
            require(badge, "outdoor_space.availability", outdoor_state, ("yes",))

    def retain(badges: list[str] | None) -> list[str] | None:
        if badges is None:
            return None
        kept = []
        for badge in badges:
            if badge in rejected:
                field, reason = rejected[badge]
                # Correlate concurrent analyses without logging listing text or URLs.
                logger.info(
                    "analysis_badge_dropped",
                    property_id=property_id,
                    badge=badge,
                    field=field,
                    reason=reason,
                )
            else:
                kept.append(badge)
        return kept

    highlights, lowlights = retain(analysis.highlights), retain(analysis.lowlights)
    if highlights == analysis.highlights and lowlights == analysis.lowlights:
        return analysis
    return analysis.model_copy(update={"highlights": highlights, "lowlights": lowlights})
