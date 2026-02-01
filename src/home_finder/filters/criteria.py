"""Property criteria filtering."""

from home_finder.logging import get_logger
from home_finder.models import Property, SearchCriteria

logger = get_logger(__name__)


class CriteriaFilter:
    """Filter properties by search criteria (price, bedrooms)."""

    def __init__(self, criteria: SearchCriteria) -> None:
        """Initialize the criteria filter.

        Args:
            criteria: Search criteria to filter by.
        """
        self.criteria = criteria

    def filter_properties(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching
