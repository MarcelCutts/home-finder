"""Base scraper interface."""

from abc import ABC, abstractmethod

from home_finder.models import FurnishType, Property, PropertySource


class BaseScraper(ABC):
    """Abstract base class for property scrapers."""

    @property
    @abstractmethod
    def source(self) -> PropertySource:
        """Return the property source this scraper handles."""
        ...

    @abstractmethod
    async def scrape(
        self,
        *,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
        area: str,
        furnish_types: tuple[FurnishType, ...] = (),
        min_bathrooms: int = 0,
        include_let_agreed: bool = True,
        max_results: int | None = None,
    ) -> list[Property]:
        """Scrape properties matching the given criteria.

        Args:
            min_price: Minimum monthly rent in GBP.
            max_price: Maximum monthly rent in GBP.
            min_bedrooms: Minimum number of bedrooms.
            max_bedrooms: Maximum number of bedrooms.
            area: Area/location to search (e.g., "hackney", "islington").
            furnish_types: Furnishing types to include (empty = no filter).
            min_bathrooms: Minimum number of bathrooms (0 = no filter).
            include_let_agreed: Whether to include already-let properties.
            max_results: Maximum number of results to return (None for unlimited).

        Returns:
            List of Property objects found.
        """
        ...
