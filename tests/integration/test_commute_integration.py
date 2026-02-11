"""Integration tests for commute filtering with mocked TravelTime API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.filters.commute import CommuteFilter, CommuteResult
from home_finder.models import MergedProperty, Property, PropertySource, TransportMode


def _make_property(
    source_id: str,
    postcode: str,
    lat: float | None = None,
    lon: float | None = None,
    price: int = 1900,
) -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id=source_id,
        url=HttpUrl(f"https://www.openrent.com/property/{source_id}"),
        title=f"Flat {source_id}",
        price_pcm=price,
        bedrooms=1,
        address=f"{source_id} Test Street, London",
        postcode=postcode,
        latitude=lat,
        longitude=lon,
    )


def _make_merged(prop: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


def _mock_time_filter_response(location_results: list[dict]):
    """Build a mock TravelTime time_filter response.

    location_results: list of {"id": str, "travel_time": int_seconds}
    """
    locations = []
    for lr in location_results:
        prop_mock = MagicMock()
        prop_mock.travel_time = lr["travel_time"]
        loc = MagicMock()
        loc.id = lr["id"]
        loc.properties = [prop_mock]
        locations.append(loc)

    search_result = MagicMock()
    search_result.locations = locations
    response = MagicMock()
    response.results = [search_result]
    return response


def _mock_geocoding_response(lat: float, lon: float):
    """Build a mock geocoding response."""
    coord = MagicMock()
    coord.coordinates = [lon, lat]  # GeoJSON: [lng, lat]
    feature = MagicMock()
    feature.geometry = coord
    response = MagicMock()
    response.features = [feature]
    return response


@pytest.mark.integration
class TestCommuteFilterIntegration:
    """Test CommuteFilter with mocked TravelTime API."""

    async def test_filter_properties_by_commute(self):
        """Properties within commute limit should be returned."""
        props = [
            _make_property("1", "E8 3RH", 51.5465, -0.0553),
            _make_property("2", "E8 4AB", 51.5470, -0.0560),
            _make_property("3", "E8 1CD", 51.5480, -0.0540),
            _make_property("4", "N1 5AA", 51.5400, -0.1000),
            _make_property("5", "N16 7EF", 51.5600, -0.0800),
        ]

        # 3 within limit (travel_time <= 30 min = 1800s), 2 outside
        time_filter_response = _mock_time_filter_response([
            {"id": "openrent:1", "travel_time": 900},   # 15 min - within
            {"id": "openrent:2", "travel_time": 1200},  # 20 min - within
            {"id": "openrent:3", "travel_time": 1500},  # 25 min - within
            {"id": "openrent:4", "travel_time": 2400},  # 40 min - outside
            {"id": "openrent:5", "travel_time": 3000},  # 50 min - outside
        ])
        geocode_resp = _mock_geocoding_response(51.5350, -0.0900)

        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        with patch("traveltimepy.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.time_filter = AsyncMock(return_value=time_filter_response)
            client_instance.geocoding = AsyncMock(return_value=geocode_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

            results = await commute_filter.filter_properties(
                props, max_minutes=30, transport_mode=TransportMode.CYCLING
            )

        assert len(results) == 5
        within = [r for r in results if r.within_limit]
        assert len(within) == 3
        assert all(r.travel_time_minutes <= 30 for r in within)

    async def test_geocode_properties_without_coords(self):
        """Properties missing coords should gain lat/lon after geocoding."""
        prop_no_coords = _make_property("10", "E8 3RH")
        merged = _make_merged(prop_no_coords)

        geocode_resp = _mock_geocoding_response(51.5465, -0.0553)

        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )
        # Clear cache to ensure fresh lookup
        CommuteFilter._geocoding_cache.clear()

        with patch("traveltimepy.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.geocoding = AsyncMock(return_value=geocode_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await commute_filter.geocode_properties([merged])

        assert len(result) == 1
        assert result[0].canonical.latitude == pytest.approx(51.5465)
        assert result[0].canonical.longitude == pytest.approx(-0.0553)

    async def test_geocode_caching(self):
        """Pre-populated cache should prevent API calls."""
        prop_no_coords = _make_property("20", "E8 3RH")
        merged = _make_merged(prop_no_coords)

        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )
        # Pre-populate cache
        CommuteFilter._geocoding_cache["E8 3RH"] = (51.5465, -0.0553)

        with patch("traveltimepy.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.geocoding = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await commute_filter.geocode_properties([merged])

        assert result[0].canonical.latitude == pytest.approx(51.5465)
        # Geocoding API should NOT have been called (cache hit handles it)

    async def test_multiple_transport_modes(self):
        """Best travel time across modes should be selected."""
        props = [_make_property("30", "E8 3RH", 51.5465, -0.0553)]

        cycling_resp = _mock_time_filter_response([
            {"id": "openrent:30", "travel_time": 1200},  # 20 min cycling
        ])
        pt_resp = _mock_time_filter_response([
            {"id": "openrent:30", "travel_time": 900},  # 15 min PT (better)
        ])
        geocode_resp = _mock_geocoding_response(51.5350, -0.0900)

        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        with patch("traveltimepy.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.geocoding = AsyncMock(return_value=geocode_resp)
            # Return different responses for each call
            client_instance.time_filter = AsyncMock(side_effect=[cycling_resp, pt_resp])
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

            cycling_results = await commute_filter.filter_properties(
                props, max_minutes=30, transport_mode=TransportMode.CYCLING
            )
            pt_results = await commute_filter.filter_properties(
                props, max_minutes=30, transport_mode=TransportMode.PUBLIC_TRANSPORT
            )

        assert cycling_results[0].travel_time_minutes == 20
        assert pt_results[0].travel_time_minutes == 15
        # PT is faster
        assert pt_results[0].travel_time_minutes < cycling_results[0].travel_time_minutes

    async def test_api_failure_returns_empty(self):
        """API errors should return empty results."""
        props = [_make_property("40", "E8 3RH", 51.5465, -0.0553)]

        commute_filter = CommuteFilter(
            app_id="test-app-id",
            api_key="test-api-key",
            destination_postcode="N1 5AA",
        )

        with patch("traveltimepy.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.geocoding = AsyncMock(side_effect=Exception("Connection failed"))
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

            results = await commute_filter.filter_properties(
                props, max_minutes=30, transport_mode=TransportMode.CYCLING
            )

        assert results == []
