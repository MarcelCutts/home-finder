"""Location-based property filtering.

This filter validates that properties are actually located in the requested
search areas, catching any "leakage" from scrapers that return properties
outside the intended geographic boundaries.
"""

from home_finder.data.location_mappings import (
    BOROUGH_OUTCODES as BOROUGH_OUTCODES,
)
from home_finder.data.location_mappings import (
    normalize_area as normalize_area,
)
from home_finder.data.location_mappings import (
    resolve_area_outcodes,
)
from home_finder.logging import get_logger
from home_finder.models import Property
from home_finder.utils.address import extract_outcode

logger = get_logger(__name__)


class LocationFilter:
    """Filter properties by geographic location.

    This filter validates that scraped properties are actually in the
    requested search areas, preventing "location leakage" where scrapers
    return properties from outside the intended area.
    """

    def __init__(self, search_areas: list[str], strict: bool = True) -> None:
        """Initialize the location filter.

        Args:
            search_areas: List of borough names or outcodes to accept.
            strict: If True, reject properties without postcodes.
                   If False, allow properties without postcodes through.
        """
        self.search_areas = [normalize_area(a) for a in search_areas]
        self.strict = strict

        self.valid_outcodes = resolve_area_outcodes(self.search_areas)

        logger.debug(  # pragma: no mutate
            "location_filter_initialized",
            search_areas=self.search_areas,
            valid_outcodes=sorted(self.valid_outcodes),
        )

    def is_valid_location(self, prop: Property) -> bool:
        """Check if a property is in a valid location.

        Args:
            prop: Property to check.

        Returns:
            True if property is in a valid location, False otherwise.
        """
        outcode = extract_outcode(prop.postcode)

        if not outcode:
            # No postcode - allow through if not strict
            return not self.strict

        return outcode in self.valid_outcodes

    def filter_properties(self, properties: list[Property]) -> list[Property]:
        """Filter properties by location.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties in valid locations.
        """
        valid = []
        rejected = []

        for prop in properties:
            if self.is_valid_location(prop):
                valid.append(prop)
            else:
                rejected.append(prop)

        if rejected:  # pragma: no mutate (logging-only block)
            rejected_outcodes: dict[str, int] = {}
            for prop in rejected:
                outcode = extract_outcode(prop.postcode) or "NO_POSTCODE"
                rejected_outcodes[outcode] = rejected_outcodes.get(outcode, 0) + 1

            logger.info(
                "location_filter_rejected",
                total_rejected=len(rejected),
                rejected_outcodes=rejected_outcodes,
            )

        logger.info(  # pragma: no mutate
            "location_filter_complete",
            total_properties=len(properties),
            valid=len(valid),
            rejected=len(rejected),
            search_areas=self.search_areas,
        )

        return valid
