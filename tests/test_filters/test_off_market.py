"""Tests for off-market property detection (OffMarketChecker)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from home_finder.filters.off_market import (
    ListingStatus,
    OffMarketChecker,
    _check_onthemarket,
    _check_openrent,
    _check_rightmove,
    _check_zoopla,
    _CurlResponseAdapter,
)

# ---------------------------------------------------------------------------
# Helper: build a fake httpx.Response-like object
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    text: str = "",
    url: str = "https://example.com/property/123",
) -> httpx.Response:
    """Build a minimal httpx.Response for checker tests."""
    resp = httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", url),
    )
    # httpx.Response tracks the final URL via the request
    return resp


# ---------------------------------------------------------------------------
# Rightmove checker
# ---------------------------------------------------------------------------


class TestCheckRightmove:
    def test_404_is_removed(self):
        resp = _make_response(status_code=404)
        assert _check_rightmove(resp) == ListingStatus.REMOVED

    def test_410_is_removed(self):
        resp = _make_response(status_code=410)
        assert _check_rightmove(resp) == ListingStatus.REMOVED

    def test_200_with_removal_text_is_removed(self):
        resp = _make_response(text="Sorry, this property has been removed from Rightmove.")
        assert _check_rightmove(resp) == ListingStatus.REMOVED

    def test_200_with_no_longer_available_text(self):
        resp = _make_response(text="This property is no longer available on the market.")
        assert _check_rightmove(resp) == ListingStatus.REMOVED

    def test_200_normal_listing_is_active(self):
        resp = _make_response(text="<html>Nice 2-bed flat in Hackney</html>")
        assert _check_rightmove(resp) == ListingStatus.ACTIVE

    def test_200_redirect_to_search_is_removed(self):
        resp = _make_response(
            url="https://www.rightmove.co.uk/property-to-rent/find.html?searchType=RENT"
        )
        assert _check_rightmove(resp) == ListingStatus.REMOVED

    def test_429_is_unknown(self):
        resp = _make_response(status_code=429)
        assert _check_rightmove(resp) == ListingStatus.UNKNOWN

    def test_500_is_unknown(self):
        resp = _make_response(status_code=500)
        assert _check_rightmove(resp) == ListingStatus.UNKNOWN

    def test_cloudflare_challenge_is_unknown(self):
        html = "<html>Checking your browser before accessing rightmove.co.uk</html>"
        resp = _make_response(text=html)
        assert _check_rightmove(resp) == ListingStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Zoopla checker
# ---------------------------------------------------------------------------


class TestCheckZoopla:
    def test_404_is_removed(self):
        resp = _make_response(status_code=404)
        assert _check_zoopla(resp) == ListingStatus.REMOVED

    def test_200_with_no_longer_available(self):
        resp = _make_response(text="This property is no longer available.")
        assert _check_zoopla(resp) == ListingStatus.REMOVED

    def test_200_normal_is_active(self):
        resp = _make_response(text="<html>Beautiful apartment</html>")
        assert _check_zoopla(resp) == ListingStatus.ACTIVE

    def test_cloudflare_challenge_is_unknown(self):
        html = "<html>Checking your browser before accessing zoopla.co.uk</html>"
        resp = _make_response(text=html)
        assert _check_zoopla(resp) == ListingStatus.UNKNOWN

    def test_429_is_unknown(self):
        resp = _make_response(status_code=429)
        assert _check_zoopla(resp) == ListingStatus.UNKNOWN


# ---------------------------------------------------------------------------
# OpenRent checker
# ---------------------------------------------------------------------------


class TestCheckOpenrent:
    def test_404_is_removed(self):
        resp = _make_response(status_code=404)
        assert _check_openrent(resp) == ListingStatus.REMOVED

    def test_redirect_to_search_is_removed(self):
        resp = _make_response(
            url="https://www.openrent.com/properties-to-rent/london",
            text="<html>Browse properties</html>",
        )
        assert _check_openrent(resp) == ListingStatus.REMOVED

    def test_homepage_in_html_is_removed(self):
        resp = _make_response(text="<html>homepage browse our listings</html>")
        assert _check_openrent(resp) == ListingStatus.REMOVED

    def test_200_normal_is_active(self):
        resp = _make_response(
            url="https://www.openrent.com/property/12345",
            text="<html>1 bed flat in E8</html>",
        )
        assert _check_openrent(resp) == ListingStatus.ACTIVE

    def test_cloudflare_challenge_is_unknown(self):
        resp = _make_response(text="<html>Just a moment... Cloudflare Ray ID: abc</html>")
        assert _check_openrent(resp) == ListingStatus.UNKNOWN

    def test_429_is_unknown(self):
        resp = _make_response(status_code=429)
        assert _check_openrent(resp) == ListingStatus.UNKNOWN


# ---------------------------------------------------------------------------
# OnTheMarket checker
# ---------------------------------------------------------------------------


class TestCheckOnthemarket:
    def test_404_is_removed(self):
        resp = _make_response(status_code=404)
        assert _check_onthemarket(resp) == ListingStatus.REMOVED

    def test_200_with_no_longer_available(self):
        resp = _make_response(text="This property is no longer available.")
        assert _check_onthemarket(resp) == ListingStatus.REMOVED

    def test_redirect_to_search_is_removed(self):
        resp = _make_response(
            url="https://www.onthemarket.com/to-rent/property/london/",
            text="<html>Search results</html>",
        )
        assert _check_onthemarket(resp) == ListingStatus.REMOVED

    def test_200_with_details_in_url_is_active(self):
        resp = _make_response(
            url="https://www.onthemarket.com/details/12345/",
            text="<html>Nice flat</html>",
        )
        assert _check_onthemarket(resp) == ListingStatus.ACTIVE

    def test_cloudflare_challenge_is_unknown(self):
        resp = _make_response(
            text="<html>Checking your browser before accessing onthemarket.com</html>"
        )
        assert _check_onthemarket(resp) == ListingStatus.UNKNOWN


# ---------------------------------------------------------------------------
# CurlResponseAdapter
# ---------------------------------------------------------------------------


class TestCurlResponseAdapter:
    def test_adapts_attributes(self):
        mock = MagicMock()
        mock.status_code = 200
        mock.text = "<html>OK</html>"
        mock.url = "https://example.com/prop/1"
        adapter = _CurlResponseAdapter(mock)
        assert adapter.status_code == 200
        assert adapter.text == "<html>OK</html>"
        assert str(adapter.url) == "https://example.com/prop/1"


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    async def test_circuit_breaker_aborts_after_threshold(self):
        """After 5 consecutive UNKNOWNs for a source, remaining checks are skipped."""
        checker = OffMarketChecker()

        # All return UNKNOWN (simulate 429s)
        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = ListingStatus.UNKNOWN

            checks = [(f"prop-{i}", "zoopla", f"https://zoopla.co.uk/{i}") for i in range(10)]
            results = await checker.check_batch(checks)

        assert len(results) == 10
        # Circuit breaker triggers after 5 — check_url should only be called 5 times
        assert mock_check.call_count == 5
        # All results are UNKNOWN
        assert all(r.status == ListingStatus.UNKNOWN for r in results)

        await checker.close()

    async def test_circuit_breaker_resets_on_success(self):
        """A non-UNKNOWN result resets the consecutive counter."""
        checker = OffMarketChecker()

        statuses = [
            ListingStatus.UNKNOWN,
            ListingStatus.UNKNOWN,
            ListingStatus.ACTIVE,  # Resets counter
            ListingStatus.UNKNOWN,
            ListingStatus.UNKNOWN,
            ListingStatus.UNKNOWN,
            ListingStatus.UNKNOWN,
            ListingStatus.UNKNOWN,  # 5th consecutive UNKNOWN after reset
        ]

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = statuses

            checks = [(f"prop-{i}", "zoopla", f"https://zoopla.co.uk/{i}") for i in range(10)]
            results = await checker.check_batch(checks)

        # 8 actual calls before circuit breaker, remaining 2 marked UNKNOWN
        assert mock_check.call_count == 8
        assert len(results) == 10

        await checker.close()


# ---------------------------------------------------------------------------
# Multi-source aggregation (tested at higher level in DB tests)
# ---------------------------------------------------------------------------


class TestCheckBatchBasic:
    async def test_batch_returns_results_for_all_checks(self):
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = ListingStatus.ACTIVE

            checks = [
                ("prop-1", "rightmove", "https://rightmove.co.uk/1"),
                ("prop-2", "openrent", "https://openrent.com/2"),
            ]
            results = await checker.check_batch(checks)

        assert len(results) == 2
        assert all(r.status == ListingStatus.ACTIVE for r in results)

        await checker.close()

    async def test_batch_groups_by_source(self):
        """Checks for the same source run sequentially with delays."""
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = ListingStatus.ACTIVE

            checks = [
                ("p1", "rightmove", "https://rightmove.co.uk/1"),
                ("p2", "rightmove", "https://rightmove.co.uk/2"),
                ("p3", "zoopla", "https://zoopla.co.uk/1"),
            ]
            results = await checker.check_batch(checks)

        assert len(results) == 3
        assert mock_check.call_count == 3

        await checker.close()
