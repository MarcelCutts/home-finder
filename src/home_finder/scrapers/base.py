"""Base scraper interface."""

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from home_finder.logging import get_logger
from home_finder.models import FurnishType, Property, PropertySource

logger = get_logger(__name__)


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
        known_source_ids: set[str] | None = None,
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
            known_source_ids: Source IDs already in DB; enables early-stop pagination.

        Returns:
            List of Property objects found.
        """
        ...

    async def _paginate(
        self,
        fetch_page: Callable[[int], Awaitable[list[Property]]],
        *,
        max_pages: int,
        known_source_ids: set[str] | None = None,
        max_results: int | None = None,
        page_delay: Callable[[], Awaitable[None]] | None = None,
    ) -> list[Property]:
        """Generic pagination loop shared by all scrapers.

        Args:
            fetch_page: Async callable receiving 0-based page index, returns
                properties for that page (empty list signals end of results).
            max_pages: Maximum number of pages to fetch.
            known_source_ids: Source IDs already in DB; enables early-stop.
            max_results: Maximum total results to return (None = unlimited).
            page_delay: Optional async callable invoked between pages.

        Returns:
            Deduplicated list of properties across all pages.
        """
        all_properties: list[Property] = []
        seen_ids: set[str] = set()

        for page_idx in range(max_pages):
            properties = await fetch_page(page_idx)

            if not properties:
                break

            # Early-stop: all results on this page are already in DB
            if known_source_ids is not None and all(
                p.source_id in known_source_ids for p in properties
            ):
                logger.info(
                    "early_stop_all_known",
                    source=self.source.value,
                    page=page_idx + 1,
                )
                break

            # Deduplicate across pages
            new_properties = [p for p in properties if p.source_id not in seen_ids]
            for p in new_properties:
                seen_ids.add(p.source_id)

            if not new_properties:
                break

            all_properties.extend(new_properties)

            if max_results is not None and len(all_properties) >= max_results:
                all_properties = all_properties[:max_results]
                break

            # Delay between pages (not after last page)
            if page_delay is not None and page_idx < max_pages - 1:
                await page_delay()

        return all_properties

    async def area_delay(self) -> None:
        """Delay between area searches. Override for scraper-specific pacing."""
        await asyncio.sleep(random.uniform(2.0, 5.0))

    @property
    def max_areas_per_run(self) -> int | None:
        """Max areas to scrape per run. None means unlimited."""
        return None

    @property
    def should_skip_remaining_areas(self) -> bool:
        """Whether the scraper wants to abort remaining areas (e.g. too many blocks)."""
        return False

    async def close(self) -> None:  # noqa: B027
        """Clean up scraper resources (e.g. HTTP sessions)."""
