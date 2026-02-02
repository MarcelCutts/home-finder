"""Tests for commute filtering with TravelTime API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.filters.commute import CommuteFilter, CommuteResult
from home_finder.models import Property, PropertySource, TransportMode


@pytest.fixture
def sample_properties() -> list[Property]:
    """Create sample properties with coordinates."""
    return [
        Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Property 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address 1",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
        ),
        Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Property 2",
            price_pcm=1900,
            bedrooms=2,
            address="Address 2",
            postcode="E8 1HN",
            latitude=51.5489,
            longitude=-0.0612,
        ),
        Property(
            source=PropertySource.ZOOPLA,
            source_id="3",
            url=HttpUrl("https://example.com/3"),
            title="Property 3",
            price_pcm=2100,
            bedrooms=1,
            address="Address 3",
            postcode="E8 2PB",
            latitude=51.5512,
            longitude=-0.0498,
        ),
    ]


@pytest.fixture
def properties_without_coords() -> list[Property]:
    """Create sample properties without coordinates."""
    return [
        Property(
            source=PropertySource.OPENRENT,
            source_id="10",
            url=HttpUrl("https://example.com/10"),
            title="No Coords Property",
            price_pcm=2000,
            bedrooms=1,
            address="Address 10",
            postcode="E8 3AA",
        ),
    ]


def create_mock_client(
    geocoding_response: MagicMock | None = None,
    time_filter_response: MagicMock | None = None,
    time_filter_error: Exception | None = None,
) -> MagicMock:
    """Create a mock AsyncClient with configured responses."""
    mock_client = AsyncMock()

    if geocoding_response:
        mock_client.geocoding = AsyncMock(return_value=geocoding_response)
    else:
        # Default: empty geocoding response
        default_geocoding = MagicMock()
        default_geocoding.features = []
        mock_client.geocoding = AsyncMock(return_value=default_geocoding)

    if time_filter_error:
        mock_client.time_filter = AsyncMock(side_effect=time_filter_error)
    elif time_filter_response:
        mock_client.time_filter = AsyncMock(return_value=time_filter_response)
    else:
        mock_client.time_filter = AsyncMock()

    # Context manager support
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    return mock_client


def create_geocoding_response(lat: float, lon: float) -> MagicMock:
    """Create a mock geocoding response."""
    mock_response = MagicMock()
    mock_feature = MagicMock()
    mock_feature.geometry.coordinates = [lon, lat]  # GeoJSON: [lon, lat]
    mock_response.features = [mock_feature]
    return mock_response


def create_time_filter_response(locations: list[tuple[str, int]]) -> MagicMock:
    """Create a mock time_filter response.

    Args:
        locations: List of (property_id, travel_time_seconds) tuples.
    """
    mock_locations = []
    for prop_id, travel_time in locations:
        mock_loc = MagicMock()
        mock_loc.id = prop_id
        mock_loc.properties = [MagicMock(travel_time=travel_time)]
        mock_locations.append(mock_loc)

    mock_search_result = MagicMock()
    mock_search_result.locations = mock_locations

    mock_response = MagicMock()
    mock_response.results = [mock_search_result]

    return mock_response


class TestCommuteFilter:
    """Tests for CommuteFilter."""

    def test_init_with_credentials(self) -> None:
        """Test initializing filter with API credentials."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )
        assert commute_filter.destination_postcode == "N1 5AA"

    @pytest.mark.asyncio
    async def test_filter_properties_returns_results(
        self, sample_properties: list[Property]
    ) -> None:
        """Test filtering properties returns commute results."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        # Create mock responses
        geocoding_response = create_geocoding_response(51.5448, -0.0934)
        time_filter_response = create_time_filter_response(
            [
                ("openrent:1", 1200),  # 20 min
                ("rightmove:2", 2400),  # 40 min
                ("zoopla:3", 900),  # 15 min
            ]
        )

        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_response=time_filter_response,
        )

        with patch("traveltimepy.AsyncClient", return_value=mock_client):
            results = await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        assert len(results) == 3

        # Check results are correctly mapped
        result_by_id = {r.property_id: r for r in results}

        # Property 1: 20 min (1200s) - within 30 min
        assert result_by_id["openrent:1"].travel_time_minutes == 20
        assert result_by_id["openrent:1"].within_limit is True

        # Property 2: 40 min (2400s) - outside 30 min
        assert result_by_id["rightmove:2"].travel_time_minutes == 40
        assert result_by_id["rightmove:2"].within_limit is False

        # Property 3: 15 min (900s) - within 30 min
        assert result_by_id["zoopla:3"].travel_time_minutes == 15
        assert result_by_id["zoopla:3"].within_limit is True

    @pytest.mark.asyncio
    async def test_filter_properties_skips_without_coords(
        self, properties_without_coords: list[Property]
    ) -> None:
        """Test that properties without coordinates are skipped."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        # No mocking needed - filter should exit early
        results = await commute_filter.filter_properties(
            properties_without_coords,
            max_minutes=30,
            transport_mode=TransportMode.CYCLING,
        )

        # No properties should be filtered (all lack coordinates)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_filter_properties_handles_unreachable(
        self, sample_properties: list[Property]
    ) -> None:
        """Test handling of unreachable locations."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        # Only one reachable location in response
        geocoding_response = create_geocoding_response(51.5448, -0.0934)
        time_filter_response = create_time_filter_response(
            [
                ("openrent:1", 1200),  # Only one reachable
            ]
        )

        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_response=time_filter_response,
        )

        with patch("traveltimepy.AsyncClient", return_value=mock_client):
            results = await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.PUBLIC_TRANSPORT,
            )

        # Only reachable property should have result
        assert len(results) == 1
        assert results[0].property_id == "openrent:1"

    @pytest.mark.asyncio
    async def test_filter_properties_handles_geocoding_failure(
        self, sample_properties: list[Property]
    ) -> None:
        """Test handling of geocoding failure."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="INVALID",
        )

        # Mock geocoding failure (no features)
        mock_geocoding_response = MagicMock()
        mock_geocoding_response.features = []

        mock_client = create_mock_client(geocoding_response=mock_geocoding_response)

        with patch("traveltimepy.AsyncClient", return_value=mock_client):
            results = await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        # Should return empty results on geocoding failure
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_client_configured_with_rate_limiting(
        self, sample_properties: list[Property]
    ) -> None:
        """Test that client is configured with rate limiting parameters."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        geocoding_response = create_geocoding_response(51.5448, -0.0934)
        time_filter_response = create_time_filter_response([("openrent:1", 1200)])
        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_response=time_filter_response,
        )

        with patch("traveltimepy.AsyncClient", return_value=mock_client) as mock_client_class:
            await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

            mock_client_class.assert_called_once_with(
                app_id="test-app-id",
                api_key="test-api-key",
                max_rpm=50,
                retry_attempts=3,
                timeout=60,
            )

    @pytest.mark.asyncio
    async def test_geocoding_uses_cache_on_second_call(
        self, sample_properties: list[Property]
    ) -> None:
        """Test that geocoding results are cached and reused."""
        # Clear the cache before test
        CommuteFilter._geocoding_cache.clear()

        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        geocoding_response = create_geocoding_response(51.5448, -0.0934)
        time_filter_response = create_time_filter_response([("openrent:1", 1200)])
        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_response=time_filter_response,
        )

        with patch("traveltimepy.AsyncClient", return_value=mock_client):
            # First call - should hit geocoding API
            await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

            # Second call - should use cache for geocoding
            await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        # Geocoding should only be called once (first call), cached for second
        assert mock_client.geocoding.call_count == 1

    @pytest.mark.asyncio
    async def test_geocoding_cache_stores_result(self, sample_properties: list[Property]) -> None:
        """Test that geocoding results are stored in cache."""
        # Clear the cache before test
        CommuteFilter._geocoding_cache.clear()

        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="SW1A 1AA",
        )

        geocoding_response = create_geocoding_response(51.5034, -0.1276)
        time_filter_response = create_time_filter_response([("openrent:1", 1200)])
        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_response=time_filter_response,
        )

        with patch("traveltimepy.AsyncClient", return_value=mock_client):
            await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        # Check cache was populated
        assert "SW1A 1AA" in CommuteFilter._geocoding_cache
        assert CommuteFilter._geocoding_cache["SW1A 1AA"] == (51.5034, -0.1276)

    @pytest.mark.asyncio
    async def test_rate_limit_error_logged_as_warning(
        self, sample_properties: list[Property]
    ) -> None:
        """Test that rate limit errors are logged as warnings, not errors."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        geocoding_response = create_geocoding_response(51.5448, -0.0934)
        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_error=Exception("Rate limit exceeded (429)"),
        )

        with (
            patch("traveltimepy.AsyncClient", return_value=mock_client),
            patch("home_finder.filters.commute.logger") as mock_logger,
        ):
            results = await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        assert results == []
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "rate_limit_hit"

    @pytest.mark.asyncio
    async def test_general_api_error_logged_as_error(
        self, sample_properties: list[Property]
    ) -> None:
        """Test that non-rate-limit errors are logged as errors."""
        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        geocoding_response = create_geocoding_response(51.5448, -0.0934)
        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_error=Exception("Network connection error"),
        )

        with (
            patch("traveltimepy.AsyncClient", return_value=mock_client),
            patch("home_finder.filters.commute.logger") as mock_logger,
        ):
            results = await commute_filter.filter_properties(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        assert results == []
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        assert call_args[0][0] == "traveltime_api_error"


class TestCommuteResult:
    """Tests for CommuteResult model."""

    def test_within_limit_true(self) -> None:
        """Test within_limit is True when under max."""
        result = CommuteResult(
            property_id="test:1",
            destination_postcode="N1 5AA",
            travel_time_minutes=20,
            transport_mode=TransportMode.CYCLING,
            within_limit=True,
        )
        assert result.within_limit is True

    def test_within_limit_false(self) -> None:
        """Test within_limit is False when over max."""
        result = CommuteResult(
            property_id="test:1",
            destination_postcode="N1 5AA",
            travel_time_minutes=45,
            transport_mode=TransportMode.CYCLING,
            within_limit=False,
        )
        assert result.within_limit is False
