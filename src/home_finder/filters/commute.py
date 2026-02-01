"""Commute time filtering using TravelTime API."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import Property, TransportMode

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
        self._client = None

    def _get_client(self):
        """Lazily initialize the TravelTime async client."""
        if self._client is None:
            from traveltimepy import AsyncClient

            self._client = AsyncClient(
                app_id=self.app_id,
                api_key=self.api_key,
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
        from traveltimepy.requests.common import Coordinates, Location
        from traveltimepy.requests.time_filter import (
            Cycling,
            Driving,
            Property as TravelTimeProperty,
            PublicTransport,
            TimeFilterDepartureSearch,
            TimeFilterRequest,
            Walking,
        )

        # Create departure location (the destination we're commuting TO)
        departure_location = Location(
            id="destination",
            coords=Coordinates(lat=dest_coords[0], lng=dest_coords[1]),
        )

        # Create arrival locations (all the properties)
        arrival_locations = []
        for prop in props_with_coords:
            arrival_locations.append(
                Location(
                    id=prop.unique_id,
                    coords=Coordinates(lat=prop.latitude, lng=prop.longitude),
                )
            )

        # Configure transportation
        if transport_mode == TransportMode.PUBLIC_TRANSPORT:
            transportation = PublicTransport()
        elif transport_mode == TransportMode.CYCLING:
            transportation = Cycling()
        elif transport_mode == TransportMode.DRIVING:
            transportation = Driving()
        else:
            transportation = Walking()

        # Create search request
        departure_search = TimeFilterDepartureSearch(
            id="property-search",
            departure_location_id="destination",
            arrival_location_ids=[loc.id for loc in arrival_locations],
            departure_time=datetime.now(timezone.utc),
            travel_time=max_minutes * 60,  # Convert to seconds
            transportation=transportation,
            properties=[TravelTimeProperty.TRAVEL_TIME],
        )

        try:
            client = self._get_client()
            async with client:
                response = await client.time_filter(
                    locations=[departure_location] + arrival_locations,
                    departure_searches=[departure_search],
                    arrival_searches=[],
                )
        except Exception as e:
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
        try:
            client = self._get_client()
            async with client:
                response = await client.geocoding(query=postcode, limit=1)
                if response.features:
                    coords = response.features[0].geometry.coordinates
                    # GeoJSON uses [longitude, latitude] order
                    return (coords[1], coords[0])
        except Exception as e:
            logger.warning("geocoding_failed", postcode=postcode, error=str(e))

        return None
