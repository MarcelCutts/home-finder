"""Base scraper interface."""

from abc import ABC, abstractmethod

from home_finder.models import Property, PropertySource


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
    ) -> list[Property]:
        """Scrape properties matching the given criteria.

        Args:
            min_price: Minimum monthly rent in GBP.
            max_price: Maximum monthly rent in GBP.
            min_bedrooms: Minimum number of bedrooms.
            max_bedrooms: Maximum number of bedrooms.
            area: Area/location to search (e.g., "hackney", "islington").

        Returns:
            List of Property objects found.
        """
        ...
