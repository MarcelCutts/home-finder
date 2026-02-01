"""Commute time filtering using TravelTime API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import Property, TransportMode

if TYPE_CHECKING:
    from traveltimepy import AsyncClient

logger = get_logger(__name__)


class CommuteResult(BaseModel):
    """Result of a commute time calculation."""

    model_config = ConfigDict(frozen=True)

    property_id: str
    destination_postcode: str
    travel_time_minutes: int
    transport_mode: TransportMode
    within_limit: bool


class CommuteFilter:
    """Filter properties by commute time using TravelTime API."""

    # Class-level cache for geocoding results to avoid redundant API calls
    _geocoding_cache: ClassVar[dict[str, tuple[float, float]]] = {}

    def __init__(
        self,
        *,
        app_id: str,
        api_key: str,
        destination_postcode: str,
    ) -> None:
        """Initialize the commute filter.

        Args:
            app_id: TravelTime API application ID.
            api_key: TravelTime API key.
            destination_postcode: Destination postcode for commute calculations.
        """
        self.app_id = app_id
        self.api_key = api_key
        self.destination_postcode = destination_postcode
        self._client: AsyncClient | None = None

    def _get_client(self) -> AsyncClient:
        """Lazily initialize the TravelTime async client with rate limiting."""
        if self._client is None:
            from traveltimepy import AsyncClient

            self._client = AsyncClient(
                app_id=self.app_id,
                api_key=self.api_key,
                max_rpm=50,  # Stay under 60 limit with safety margin
                retry_attempts=3,  # Retry transient failures
                timeout=60,  # Reasonable timeout in seconds
            )
        return self._client

    async def filter_properties(
        self,
        properties: list[Property],
        *,
        max_minutes: int,
        transport_mode: TransportMode,
    ) -> list[CommuteResult]:
        """Filter properties by commute time.

        Args:
            properties: List of properties to filter.
            max_minutes: Maximum commute time in minutes.
            transport_mode: Mode of transport for commute calculation.

        Returns:
            List of CommuteResult objects for reachable properties.
        """
        # Filter to only properties with coordinates
        props_with_coords = [p for p in properties if p.latitude and p.longitude]

        if not props_with_coords:
            logger.info("no_properties_with_coordinates")
            return []

        logger.info(
            "filtering_properties_by_commute",
            total_properties=len(properties),
            with_coordinates=len(props_with_coords),
            max_minutes=max_minutes,
            transport_mode=transport_mode.value,
        )

        # Get destination coordinates from postcode
        dest_coords = await self._geocode_postcode(self.destination_postcode)
        if not dest_coords:
            logger.error(
                "failed_to_geocode_destination",
                postcode=self.destination_postcode,
            )
            return []

        # Import required types
        from traveltimepy.requests.common import (
            Coordinates,
            Location,
        )
        from traveltimepy.requests.common import (
            Property as TravelTimeProperty,
        )
        from traveltimepy.requests.time_filter import TimeFilterArrivalSearch
        from traveltimepy.requests.transportation import (
            Cycling,
            Driving,
            PublicTransport,
            Walking,
        )

        # Create arrival location (the destination we're commuting TO)
        arrival_location = Location(
            id="destination",
            coords=Coordinates(lat=dest_coords[0], lng=dest_coords[1]),
        )

        # Create departure locations (all the properties we're commuting FROM)
        departure_locations = []
        for prop in props_with_coords:
            # Already filtered above, but assert for type checker
            assert prop.latitude is not None and prop.longitude is not None
            departure_locations.append(
                Location(
                    id=prop.unique_id,
                    coords=Coordinates(lat=prop.latitude, lng=prop.longitude),
                )
            )

        # Configure transportation
        transportation: PublicTransport | Cycling | Driving | Walking
        if transport_mode == TransportMode.PUBLIC_TRANSPORT:
            transportation = PublicTransport()
        elif transport_mode == TransportMode.CYCLING:
            transportation = Cycling()
        elif transport_mode == TransportMode.DRIVING:
            transportation = Driving()
        else:
            transportation = Walking()

        # Create search request (many-to-one: from properties to destination)
        arrival_search = TimeFilterArrivalSearch(
            id="property-search",
            arrival_location_id="destination",
            departure_location_ids=[loc.id for loc in departure_locations],
            arrival_time=datetime.now(UTC),
            travel_time=max_minutes * 60,  # Convert to seconds
            transportation=transportation,
            properties=[TravelTimeProperty.TRAVEL_TIME],
        )

        try:
            client = self._get_client()
            async with client:
                response = await client.time_filter(
                    locations=[arrival_location] + departure_locations,
                    departure_searches=[],
                    arrival_searches=[arrival_search],
                )
        except Exception as e:
            error_str = str(e).lower()
            if "rate limit" in error_str or "429" in error_str:
                logger.warning("rate_limit_hit", error=str(e))
            else:
                logger.error("traveltime_api_error", error=str(e))
            return []

        # Process results
        results: list[CommuteResult] = []

        for search_result in response.results:
            for location in search_result.locations:
                travel_time_seconds = location.properties[0].travel_time
                travel_time_minutes = travel_time_seconds // 60

                results.append(
                    CommuteResult(
                        property_id=location.id,
                        destination_postcode=self.destination_postcode,
                        travel_time_minutes=travel_time_minutes,
                        transport_mode=transport_mode,
                        within_limit=travel_time_minutes <= max_minutes,
                    )
                )

        logger.info(
            "commute_filter_complete",
            total_results=len(results),
            within_limit=sum(1 for r in results if r.within_limit),
        )

        return results

    async def _geocode_postcode(self, postcode: str) -> tuple[float, float] | None:
        """Geocode a UK postcode to coordinates.

        Args:
            postcode: UK postcode to geocode.

        Returns:
            Tuple of (latitude, longitude) or None if geocoding fails.
        """
        # Check cache first
        if postcode in self._geocoding_cache:
            logger.debug("geocoding_cache_hit", postcode=postcode)
            return self._geocoding_cache[postcode]

        try:
            client = self._get_client()
            async with client:
                response = await client.geocoding(query=postcode, limit=1)
                if response.features:
                    coords = response.features[0].geometry.coordinates
                    # GeoJSON uses [longitude, latitude] order
                    result = (coords[1], coords[0])
                    self._geocoding_cache[postcode] = result
                    return result
        except Exception as e:
            logger.warning("geocoding_failed", postcode=postcode, error=str(e))

        return None
