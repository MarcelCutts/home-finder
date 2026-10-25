"""Tests for advisory commute assessment with TravelTime API."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl, ValidationError

from home_finder.enrichment.commute import CommuteAssessor, CommuteResult
from home_finder.models import CommuteState, Property, PropertySource, TransportMode
from home_finder.models.commute import best_measured_commutes


@pytest.fixture(autouse=True)
def isolated_geocoding_cache():
    CommuteAssessor._geocoding_cache.clear()
    yield
    CommuteAssessor._geocoding_cache.clear()


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


def create_time_filter_response(
    locations: list[tuple[str, int]], *, unreachable: list[str] | None = None
) -> MagicMock:
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
    mock_search_result.unreachable = unreachable or []

    mock_response = MagicMock()
    mock_response.results = [mock_search_result]

    return mock_response


class TestCommuteAssessor:
    """Tests for CommuteAssessor."""

    def test_init_with_credentials(self) -> None:
        """Test initializing filter with API credentials."""
        commute_assessor = CommuteAssessor(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )
        assert commute_assessor.destination_postcode == "N1 5AA"

    @pytest.mark.asyncio
    async def test_assess_commutes_returns_results(self, sample_properties: list[Property]) -> None:
        """Test filtering properties returns commute results."""
        commute_assessor = CommuteAssessor(
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
            results = await commute_assessor.assess_commutes(
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
    async def test_assess_commutes_reports_missing_coords(
        self, properties_without_coords: list[Property]
    ) -> None:
        """No provider request is possible, but the input still has an outcome."""
        commute_assessor = CommuteAssessor(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        # No mocking needed - filter should exit early
        results = await commute_assessor.assess_commutes(
            properties_without_coords,
            max_minutes=30,
            transport_mode=TransportMode.CYCLING,
        )

        assert len(results) == 1
        assert results[0].state == CommuteState.MISSING_COORDINATES
        assert results[0].within_limit is None
        assert results[0].travel_time_minutes is None

    @pytest.mark.asyncio
    async def test_assess_commutes_handles_unreachable(
        self, sample_properties: list[Property]
    ) -> None:
        """Test handling of unreachable locations."""
        commute_assessor = CommuteAssessor(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        # Only one reachable location in response
        geocoding_response = create_geocoding_response(51.5448, -0.0934)
        time_filter_response = create_time_filter_response(
            [
                ("openrent:1", 1200),  # Only one reachable
            ],
            unreachable=["rightmove:2", "zoopla:3"],
        )

        mock_client = create_mock_client(
            geocoding_response=geocoding_response,
            time_filter_response=time_filter_response,
        )

        with patch("traveltimepy.AsyncClient", return_value=mock_client):
            results = await commute_assessor.assess_commutes(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.PUBLIC_TRANSPORT,
            )

        assert len(results) == 3
        assert results[0].property_id == "openrent:1"
        assert all(r.state == CommuteState.UNREACHABLE for r in results[1:])
        assert all(r.within_limit is None for r in results[1:])

    @pytest.mark.asyncio
    async def test_assess_commutes_handles_geocoding_failure(
        self, sample_properties: list[Property]
    ) -> None:
        """Test handling of geocoding failure."""
        commute_assessor = CommuteAssessor(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="INVALID",
        )

        # Mock geocoding failure (no features)
        mock_geocoding_response = MagicMock()
        mock_geocoding_response.features = []

        mock_client = create_mock_client(geocoding_response=mock_geocoding_response)

        with patch("traveltimepy.AsyncClient", return_value=mock_client):
            results = await commute_assessor.assess_commutes(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        assert len(results) == len(sample_properties)
        assert all(r.state == CommuteState.DESTINATION_UNRESOLVED for r in results)
        assert all(r.travel_time_minutes is None for r in results)
        mock_client.time_filter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_client_configured_with_rate_limiting(
        self, sample_properties: list[Property]
    ) -> None:
        """Test that client is configured with rate limiting parameters."""
        commute_assessor = CommuteAssessor(
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
            await commute_assessor.assess_commutes(
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
        CommuteAssessor._geocoding_cache.clear()

        commute_assessor = CommuteAssessor(
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
            await commute_assessor.assess_commutes(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

            # Second call - should use cache for geocoding
            await commute_assessor.assess_commutes(
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
        CommuteAssessor._geocoding_cache.clear()

        commute_assessor = CommuteAssessor(
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
            await commute_assessor.assess_commutes(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        # Check cache was populated
        assert "SW1A 1AA" in CommuteAssessor._geocoding_cache
        assert CommuteAssessor._geocoding_cache["SW1A 1AA"] == (51.5034, -0.1276)

    @pytest.mark.asyncio
    async def test_rate_limit_error_graceful_degradation(
        self, sample_properties: list[Property]
    ) -> None:
        """Test that rate limit errors pass all properties through (graceful degradation)."""
        commute_assessor = CommuteAssessor(
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
            patch("home_finder.enrichment.commute.logger") as mock_logger,
        ):
            results = await commute_assessor.assess_commutes(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        # Fail open for admission, but never claim the journey met the limit.
        assert len(results) == len(sample_properties)
        assert all(r.within_limit is None for r in results)
        assert all(r.travel_time_minutes is None for r in results)
        assert all(r.state == CommuteState.PROVIDER_ERROR for r in results)
        # Rate limit logged as warning, then commute_assessment_failed also logged
        warning_events = [call[0][0] for call in mock_logger.warning.call_args_list]
        assert "rate_limit_hit" in warning_events
        assert "commute_assessment_failed" in warning_events

    @pytest.mark.asyncio
    async def test_general_api_error_graceful_degradation(
        self, sample_properties: list[Property]
    ) -> None:
        """Test that non-rate-limit errors pass all properties through (graceful degradation)."""
        commute_assessor = CommuteAssessor(
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
            patch("home_finder.enrichment.commute.logger") as mock_logger,
        ):
            results = await commute_assessor.assess_commutes(
                sample_properties,
                max_minutes=30,
                transport_mode=TransportMode.CYCLING,
            )

        # Fail open for admission, but never claim the journey met the limit.
        assert len(results) == len(sample_properties)
        assert all(r.within_limit is None for r in results)
        assert all(r.travel_time_minutes is None for r in results)
        assert all(r.state == CommuteState.PROVIDER_ERROR for r in results)
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        assert call_args[0][0] == "traveltime_api_error"
        # Also check commute_assessment_failed was logged
        warning_events = [call[0][0] for call in mock_logger.warning.call_args_list]
        assert "commute_assessment_failed" in warning_events


class TestCommuteResult:
    """Tests for CommuteResult model."""

    def test_within_limit_true(self) -> None:
        """Test within_limit is True when under max."""
        result = CommuteResult(
            property_id="test:1",
            destination_postcode="N1 5AA",
            travel_time_seconds=1200,
            limit_seconds=1800,
            transport_mode=TransportMode.CYCLING,
            state=CommuteState.WITHIN_LIMIT,
        )
        assert result.within_limit is True

    def test_within_limit_false(self) -> None:
        """Test within_limit is False when over max."""
        result = CommuteResult(
            property_id="test:1",
            destination_postcode="N1 5AA",
            travel_time_seconds=2700,
            limit_seconds=1800,
            transport_mode=TransportMode.CYCLING,
            state=CommuteState.OVER_LIMIT,
        )
        assert result.within_limit is False


@pytest.mark.parametrize(
    "seconds,minutes,within", [(0, 0, True), (1799, 30, True), (1800, 30, True), (1801, 31, False)]
)
async def test_exact_second_boundary(sample_properties, seconds, minutes, within):
    """1,801s is a defensive over-cap response, not a normal provider incident."""
    client = create_mock_client(
        geocoding_response=create_geocoding_response(51.54, -0.09),
        time_filter_response=create_time_filter_response([("openrent:1", seconds)]),
    )
    commute = CommuteAssessor(app_id="test", api_key="test", destination_postcode="N1 5AA")
    with patch("traveltimepy.AsyncClient", return_value=client):
        result = (
            await commute.assess_commutes(
                sample_properties[:1], max_minutes=30, transport_mode=TransportMode.CYCLING
            )
        )[0]
    assert result.travel_time_seconds == seconds
    assert result.travel_time_minutes == minutes
    assert result.within_limit is within
    assert client.time_filter.call_args.kwargs["arrival_searches"][0].travel_time == 1800


@pytest.mark.parametrize("bad_seconds", [-1, None, True, 1.5, "1200"])
async def test_malformed_measurement_is_unknown(sample_properties, bad_seconds):
    response = create_time_filter_response([("openrent:1", bad_seconds)])
    client = create_mock_client(
        geocoding_response=create_geocoding_response(51.54, -0.09), time_filter_response=response
    )
    commute = CommuteAssessor(app_id="test", api_key="test", destination_postcode="N1 5AA")
    with patch("traveltimepy.AsyncClient", return_value=client):
        results = await commute.assess_commutes(
            sample_properties, max_minutes=30, transport_mode=TransportMode.CYCLING
        )
    assert all(r.state == CommuteState.INVALID_RESPONSE for r in results)
    assert all(r.within_limit is None and r.travel_time_seconds is None for r in results)


@pytest.mark.parametrize("shape", ["missing", "duplicate", "conflict", "malformed"])
async def test_incomplete_or_conflicting_response_is_unknown(sample_properties, shape):
    response = create_time_filter_response([("unrequested:99", 0)])
    if shape == "duplicate":
        response = create_time_filter_response([("openrent:1", 60), ("openrent:1", 60)])
    elif shape == "conflict":
        response = create_time_filter_response([("openrent:1", 60)], unreachable=["openrent:1"])
    elif shape == "malformed":
        response.results = None
    client = create_mock_client(
        geocoding_response=create_geocoding_response(51.54, -0.09), time_filter_response=response
    )
    commute = CommuteAssessor(app_id="test", api_key="test", destination_postcode="N1 5AA")
    with patch("traveltimepy.AsyncClient", return_value=client):
        results = await commute.assess_commutes(
            sample_properties, max_minutes=30, transport_mode=TransportMode.CYCLING
        )
    assert len(results) == len(sample_properties)
    assert all(r.state == CommuteState.INVALID_RESPONSE for r in results)


async def test_zero_longitude_is_a_real_coordinate(sample_properties):
    prop = sample_properties[0].model_copy(update={"longitude": 0.0})
    client = create_mock_client(
        geocoding_response=create_geocoding_response(51.54, -0.09),
        time_filter_response=create_time_filter_response([("openrent:1", 60)]),
    )
    commute = CommuteAssessor(app_id="test", api_key="test", destination_postcode="N1 5AA")
    with patch("traveltimepy.AsyncClient", return_value=client):
        results = await commute.assess_commutes(
            [prop], max_minutes=30, transport_mode=TransportMode.CYCLING
        )
    assert results[0].within_limit is True
    assert client.time_filter.call_args.kwargs["locations"][1].coords.lng == 0


@pytest.mark.parametrize(
    "state,seconds",
    [
        (CommuteState.PROVIDER_ERROR, 0),
        (CommuteState.WITHIN_LIMIT, None),
        (CommuteState.WITHIN_LIMIT, 1801),
        (CommuteState.OVER_LIMIT, 1800),
        (CommuteState.WITHIN_LIMIT, True),
    ],
)
def test_incoherent_outcomes_rejected(state, seconds):
    with pytest.raises(ValidationError):
        CommuteResult(
            property_id="test:1",
            destination_postcode="N1 5AA",
            transport_mode=TransportMode.CYCLING,
            limit_seconds=1800,
            state=state,
            travel_time_seconds=seconds,
        )


def test_best_mode_uses_seconds_before_display_rounding():
    results = [
        CommuteResult(
            property_id="test:1",
            destination_postcode="N1 5AA",
            transport_mode=mode,
            limit_seconds=1800,
            state=CommuteState.WITHIN_LIMIT,
            travel_time_seconds=seconds,
        )
        for mode, seconds in [(TransportMode.CYCLING, 1259), (TransportMode.PUBLIC_TRANSPORT, 1201)]
    ]
    assert results[0].travel_time_minutes == results[1].travel_time_minutes == 21
    assert (
        best_measured_commutes(results)["test:1"].transport_mode == TransportMode.PUBLIC_TRANSPORT
    )


async def test_cancellation_propagates_instead_of_becoming_a_result(sample_properties):
    client = create_mock_client(geocoding_response=create_geocoding_response(51.54, -0.09))
    client.time_filter.side_effect = asyncio.CancelledError()
    commute = CommuteAssessor(app_id="test", api_key="test", destination_postcode="N1 5AA")
    with (
        patch("traveltimepy.AsyncClient", return_value=client),
        pytest.raises(asyncio.CancelledError),
    ):
        await commute.assess_commutes(
            sample_properties, max_minutes=30, transport_mode=TransportMode.CYCLING
        )


async def test_missing_coordinates_remain_distinct_during_outage(
    sample_properties, properties_without_coords
):
    client = create_mock_client(
        geocoding_response=create_geocoding_response(51.54, -0.09),
        time_filter_error=RuntimeError("unauthorized"),
    )
    commute = CommuteAssessor(app_id="test", api_key="test", destination_postcode="N1 5AA")
    with patch("traveltimepy.AsyncClient", return_value=client):
        results = await commute.assess_commutes(
            [sample_properties[0], properties_without_coords[0]],
            max_minutes=30,
            transport_mode=TransportMode.CYCLING,
        )
    assert [r.state for r in results] == [
        CommuteState.PROVIDER_ERROR,
        CommuteState.MISSING_COORDINATES,
    ]
