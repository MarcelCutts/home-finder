"""Base scraper interface."""

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from home_finder.logging import get_logger
from home_finder.models import FurnishType, ListingTextEvidence, Property, PropertySource

logger = get_logger(__name__)


@dataclass
class ScrapeResult:
    """Result of a scrape operation with metadata for distinguishing success from failure."""

    properties: list[Property] = field(default_factory=list)
    pages_fetched: int = 0
    pages_failed: int = 0
    parse_errors: int = 0
    observations: list[Property] = field(default_factory=list)
    stop_reason: str = "unspecified"

    @property
    def is_healthy(self) -> bool:
        """No recorded request/parse failure; this does not imply full coverage."""
        return (
            self.pages_fetched > 0
            and self.pages_failed == 0
            and self.parse_errors == 0
            and self.stop_reason != "cancelled"
        )


class ScrapeCancelled(asyncio.CancelledError):
    """Cancellation carrying only facts already received, for durable recording."""

    def __init__(self, result: ScrapeResult) -> None:
        super().__init__("scrape cancelled")
        self.result = result


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
    ) -> ScrapeResult:
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
            ScrapeResult with properties and scrape health metadata.
        """
        ...

    async def _paginate(
        self,
        fetch_page: Callable[[int], Awaitable[list[Property] | None]],
        *,
        max_pages: int,
        known_source_ids: set[str] | None = None,
        max_results: int | None = None,
        page_delay: Callable[[], Awaitable[None]] | None = None,
    ) -> ScrapeResult:
        """Generic pagination loop shared by all scrapers.

        Args:
            fetch_page: Async callable receiving 0-based page index. Returns
                properties for that page, empty list for end of results, or
                None for fetch failure.
            max_pages: Maximum number of pages to fetch.
            known_source_ids: Source IDs already in DB; enables early-stop.
            max_results: Maximum total results to return (None = unlimited).
            page_delay: Optional async callable invoked between pages.

        Returns:
            ScrapeResult with deduplicated properties and health metadata.
        """
        result = ScrapeResult(stop_reason="page_limit")
        observed: dict[str, Property] = {}
        if max_results is not None and max_results <= 0:
            result.stop_reason = "result_limit"
            return result

        try:
            for page_idx in range(max_pages):
                try:
                    properties = await fetch_page(page_idx)
                except Exception as exc:
                    result.pages_failed += 1
                    result.stop_reason = "page_error"
                    logger.warning(
                        "scrape_page_error",
                        source=self.source.value,
                        page=page_idx + 1,
                        error_type=type(exc).__name__,
                    )
                    break

                if properties is None:
                    result.pages_failed += 1
                    if page_delay is not None and page_idx < max_pages - 1:
                        await page_delay()
                    continue
                result.pages_fetched += 1
                if not properties:
                    result.stop_reason = "empty_page"
                    break

                # Date text at the real receipt boundary, never when reloading
                # a persisted Property or when a retry is promoted.
                received_at = datetime.now(UTC)
                properties = [
                    prop.model_copy(
                        update={
                            "search_text": ListingTextEvidence.observed(
                                observed_at=received_at,
                                description=prop.description,
                                features=prop.search_text.features if prop.search_text else None,
                                origin="search",
                            )
                        }
                    )
                    if prop.description is not None or prop.search_text is not None
                    else prop
                    for prop in properties
                ]
                previous_ids = set(observed)
                observed.update((p.source_id, p) for p in properties)
                # Retain the fetched page before applying any stop/cap policy.
                # Last received data wins within this scrape's current projection.
                result.observations = list(observed.values())
                result.properties = result.observations[:max_results]

                if known_source_ids is not None and all(
                    p.source_id in known_source_ids for p in properties
                ):
                    result.stop_reason = "known_page"
                    logger.info("early_stop_all_known", source=self.source.value, page=page_idx + 1)
                    break
                if max_results is not None and len(result.properties) >= max_results:
                    result.stop_reason = "result_limit"
                    break
                if set(observed) == previous_ids:
                    result.stop_reason = "duplicate_page"
                    break
                if page_delay is not None and page_idx < max_pages - 1:
                    await page_delay()
        except asyncio.CancelledError:
            result.stop_reason = "cancelled"
            raise ScrapeCancelled(result) from None

        logger.info(
            "scrape_pagination_complete",
            source=self.source.value,
            stop_reason=result.stop_reason,
            observed=len(result.observations),
            selected=len(result.properties),
            pages_fetched=result.pages_fetched,
            pages_failed=result.pages_failed,
        )
        return result

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
