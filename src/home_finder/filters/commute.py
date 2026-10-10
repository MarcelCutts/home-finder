"""Commute time filtering using TravelTime API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, assert_never

from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, TransportMode
from home_finder.models.commute import CommuteResult as CommuteResult
from home_finder.models.commute import CommuteState
from home_finder.utils.address import is_outcode

if TYPE_CHECKING:
    from traveltimepy import AsyncClient

logger = get_logger(__name__)


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

    async def filter_properties(
        self,
        properties: list[Property],
        *,
        max_minutes: int,
        transport_mode: TransportMode,
    ) -> list[CommuteResult]:
        """Return one explicit outcome per input, with no admission decisions.

        The API's unreachable list describes this bounded search, not proof of
        an impossible journey or a measured duration beyond the user's limit.
        """

        def outcome(
            prop: Property, state: CommuteState, seconds: int | None = None
        ) -> CommuteResult:
            return CommuteResult(
                property_id=prop.unique_id,
                destination_postcode=self.destination_postcode,
                transport_mode=transport_mode,
                limit_seconds=max_minutes * 60,
                state=state,
                travel_time_seconds=seconds,
            )

        props_with_coords = [
            p for p in properties if p.latitude is not None and p.longitude is not None
        ]
        by_id = {p.unique_id: outcome(p, CommuteState.MISSING_COORDINATES) for p in properties}
        if not props_with_coords:
            logger.info("no_properties_with_coordinates")
            return [by_id[p.unique_id] for p in properties]

        # Missing response members remain explicit unknowns, not unreachable.
        by_id.update(
            {p.unique_id: outcome(p, CommuteState.INVALID_RESPONSE) for p in props_with_coords}
        )
        from traveltimepy import AsyncClient
        from traveltimepy.requests.common import Coordinates, Location
        from traveltimepy.requests.common import Property as TravelTimeProperty
        from traveltimepy.requests.time_filter import TimeFilterArrivalSearch
        from traveltimepy.requests.transportation import Cycling, Driving, PublicTransport, Walking

        locations = []
        for prop in props_with_coords:
            assert prop.latitude is not None and prop.longitude is not None
            locations.append(
                Location(
                    id=prop.unique_id, coords=Coordinates(lat=prop.latitude, lng=prop.longitude)
                )
            )

        transportation: PublicTransport | Cycling | Driving | Walking
        if transport_mode == TransportMode.PUBLIC_TRANSPORT:
            transportation = PublicTransport()
        elif transport_mode == TransportMode.CYCLING:
            transportation = Cycling()
        elif transport_mode == TransportMode.DRIVING:
            transportation = Driving()
        elif transport_mode == TransportMode.WALKING:
            transportation = Walking()
        else:
            assert_never(transport_mode)

        try:
            async with AsyncClient(
                app_id=self.app_id,
                api_key=self.api_key,
                max_rpm=50,
                retry_attempts=3,
                timeout=60,
            ) as client:
                dest_coords = await self._geocode_with_client(
                    client, self.destination_postcode, raise_on_error=True
                )
                if not dest_coords:
                    by_id.update(
                        {
                            p.unique_id: outcome(p, CommuteState.DESTINATION_UNRESOLVED)
                            for p in props_with_coords
                        }
                    )
                    return [by_id[p.unique_id] for p in properties]

                destination = Location(
                    id="destination", coords=Coordinates(lat=dest_coords[0], lng=dest_coords[1])
                )
                search = TimeFilterArrivalSearch(
                    id="property-search",
                    arrival_location_id="destination",
                    departure_location_ids=[loc.id for loc in locations],
                    arrival_time=datetime.now(UTC),
                    travel_time=max_minutes * 60,
                    transportation=transportation,
                    properties=[TravelTimeProperty.TRAVEL_TIME],
                )
                response = await client.time_filter(
                    locations=[destination, *locations],
                    departure_searches=[],
                    arrival_searches=[search],
                )
        except Exception as exc:
            # No exception text/traceback: SDK failures can include request data.
            if "rate limit" in str(exc).lower() or "429" in str(exc):
                logger.warning("rate_limit_hit", error_type=type(exc).__name__)
            else:
                logger.error("traveltime_api_error", error_type=type(exc).__name__)
            logger.warning(
                "commute_filter_skipped", reason="api_failure", property_count=len(properties)
            )
            by_id.update(
                {p.unique_id: outcome(p, CommuteState.PROVIDER_ERROR) for p in props_with_coords}
            )
            return [by_id[p.unique_id] for p in properties]

        requested = {p.unique_id: p for p in props_with_coords}
        seen: set[str] = set()
        try:
            for search_result in response.results:
                for uid in search_result.unreachable:
                    if uid in requested:
                        state = (
                            CommuteState.INVALID_RESPONSE
                            if uid in seen
                            else CommuteState.UNREACHABLE
                        )
                        by_id[uid] = outcome(requested[uid], state)
                        seen.add(uid)
                for location in search_result.locations:
                    uid = location.id
                    if uid not in requested:
                        continue
                    seconds = location.properties[0].travel_time if location.properties else None
                    # Duplicate/conflicting or malformed outcomes are not measurements.
                    if uid in seen or type(seconds) is not int or seconds < 0:
                        by_id[uid] = outcome(requested[uid], CommuteState.INVALID_RESPONSE)
                    else:
                        state = (
                            CommuteState.WITHIN_LIMIT
                            if seconds <= max_minutes * 60
                            else CommuteState.OVER_LIMIT
                        )
                        by_id[uid] = outcome(requested[uid], state, seconds)
                    seen.add(uid)
        except (AttributeError, IndexError, TypeError):
            logger.warning("invalid_commute_response")
            by_id.update(
                {p.unique_id: outcome(p, CommuteState.INVALID_RESPONSE) for p in props_with_coords}
            )

        results = [by_id[p.unique_id] for p in properties]
        logger.info(
            "commute_filter_complete",
            total_results=len(results),
            within_limit=sum(r.within_limit is True for r in results),
            over_limit=sum(r.within_limit is False for r in results),
            unknown=sum(r.within_limit is None for r in results),
        )
        return results

    async def geocode_properties(self, properties: list[MergedProperty]) -> list[MergedProperty]:
        """Geocode merged properties that have a postcode but no coordinates.

        Properties that already have coordinates are returned unchanged.
        Uses the class-level geocoding cache to avoid redundant API calls.

        Args:
            properties: Merged properties, some possibly missing coordinates.

        Returns:
            Updated list with coordinates filled in where possible.
        """
        from traveltimepy import AsyncClient

        needs_geocoding = [
            m
            for m in properties
            if (m.canonical.latitude is None or m.canonical.longitude is None)
            and m.canonical.postcode
            and not is_outcode(m.canonical.postcode)
        ]

        outcode_only_count = sum(
            1
            for m in properties
            if (m.canonical.latitude is None or m.canonical.longitude is None)
            and m.canonical.postcode
            and is_outcode(m.canonical.postcode)
        )
        if outcode_only_count:
            logger.info(
                "skipping_outcode_geocoding",
                count=outcode_only_count,
                reason="outcode-only postcodes too imprecise for commute calculation",
            )

        if not needs_geocoding:
            return properties

        logger.info("geocoding_properties", count=len(needs_geocoding))

        geocoded_count = 0
        try:
            async with AsyncClient(
                app_id=self.app_id,
                api_key=self.api_key,
                max_rpm=50,
                retry_attempts=3,
                timeout=60,
            ) as client:
                # Build a lookup of postcode -> coords (batch unique postcodes)
                postcodes = {m.canonical.postcode for m in needs_geocoding if m.canonical.postcode}
                coords_lookup: dict[str, tuple[float, float]] = {}
                for postcode in postcodes:
                    coords = await self._geocode_with_client(client, postcode)
                    if coords:
                        coords_lookup[postcode] = coords
        except Exception as e:
            logger.warning("geocoding_batch_failed", error=str(e), exc_info=True)
            return properties

        # Build updated list, replacing properties that got coordinates
        result: list[MergedProperty] = []
        for merged in properties:
            canon = merged.canonical
            if (canon.latitude is None or canon.longitude is None) and canon.postcode:
                coords = coords_lookup.get(canon.postcode)
                if coords:
                    updated_canon = canon.model_copy(
                        update={"latitude": coords[0], "longitude": coords[1]}
                    )
                    merged = merged.model_copy(update={"canonical": updated_canon})
                    geocoded_count += 1
            result.append(merged)

        logger.info(
            "geocoding_complete",
            geocoded=geocoded_count,
            failed=len(needs_geocoding) - geocoded_count,
        )
        return result

    async def _geocode_with_client(
        self, client: AsyncClient, postcode: str, *, raise_on_error: bool = False
    ) -> tuple[float, float] | None:
        """Geocode a UK postcode to coordinates using the provided client.

        Args:
            client: TravelTime AsyncClient (must be within an active context manager).
            postcode: UK postcode to geocode.

        Returns:
            Tuple of (latitude, longitude) or None if geocoding fails.
        """
        # Check cache first
        if postcode in self._geocoding_cache:
            logger.debug("geocoding_cache_hit", postcode=postcode)
            return self._geocoding_cache[postcode]

        try:
            response = await client.geocoding(query=postcode, limit=1)
            if response.features:
                coords = response.features[0].geometry.coordinates
                # GeoJSON uses [longitude, latitude] order
                result = (coords[1], coords[0])
                self._geocoding_cache[postcode] = result
                return result
        except Exception as e:
            logger.warning("geocoding_failed", error_type=type(e).__name__)
            if raise_on_error:
                raise

        return None
