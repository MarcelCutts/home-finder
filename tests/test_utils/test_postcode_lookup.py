"""Tests for postcodes.io client utility."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from home_finder.utils.postcode_lookup import (
    bulk_reverse_lookup_wards,
    lookup_ward,
    reverse_lookup_ward,
)


@pytest.fixture
def mock_response() -> MagicMock:
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = 200
    return resp


class TestLookupWard:
    async def test_successful_lookup(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": 200,
            "result": {"admin_ward": "London Fields", "admin_district": "Hackney"},
        }

        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await lookup_ward("E8 3RH")
            assert result == "London Fields"
            instance.get.assert_called_once()

    async def test_invalid_postcode_returns_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await lookup_ward("INVALID")
            assert result is None

    async def test_network_error_returns_none(self) -> None:
        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await lookup_ward("E8 3RH")
            assert result is None


class TestReverseLookupWard:
    async def test_successful_reverse_lookup(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": 200,
            "result": [{"admin_ward": "Dalston", "postcode": "E8 2PB"}],
        }

        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await reverse_lookup_ward(51.5493, -0.0756)
            assert result == "Dalston"

    async def test_no_results_returns_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": 200, "result": None}

        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await reverse_lookup_ward(0.0, 0.0)
            assert result is None


class TestBulkReverseLookup:
    async def test_empty_input(self) -> None:
        result = await bulk_reverse_lookup_wards([])
        assert result == []

    async def test_successful_bulk_lookup(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": 200,
            "result": [
                {"query": {}, "result": [{"admin_ward": "Dalston"}]},
                {"query": {}, "result": [{"admin_ward": "Haggerston"}]},
            ],
        }

        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await bulk_reverse_lookup_wards(
                [(51.549, -0.075), (51.536, -0.076)]
            )
            assert result == ["Dalston", "Haggerston"]

    async def test_partial_results(self) -> None:
        """Some coordinates may not resolve — those should be None."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": 200,
            "result": [
                {"query": {}, "result": [{"admin_ward": "Dalston"}]},
                {"query": {}, "result": None},
            ],
        }

        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await bulk_reverse_lookup_wards(
                [(51.549, -0.075), (0.0, 0.0)]
            )
            assert result == ["Dalston", None]

    async def test_api_error_returns_nones(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await bulk_reverse_lookup_wards([(51.549, -0.075)])
            assert result == [None]
