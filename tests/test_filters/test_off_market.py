"""Tests for off-market property detection (OffMarketChecker)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from home_finder.filters.off_market import (
    _LET_AGREED_PATTERNS,
    ListingStatus,
    OffMarketChecker,
    _check_let_agreed,
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

    def test_let_agreed_json_tag(self):
        html = '<script>window.PAGE_MODEL = {"tags":["LET_AGREED"]}</script>'
        resp = _make_response(text=html)
        assert _check_rightmove(resp) == ListingStatus.LET_AGREED

    def test_let_agreed_heading_text(self):
        html = "<html><h1>LET AGREED</h1><p>Nice flat</p></html>"
        resp = _make_response(text=html)
        assert _check_rightmove(resp) == ListingStatus.LET_AGREED

    def test_let_agreed_false_positive_url_param(self):
        """includeLetAgreed=true in agent URLs should NOT match."""
        html = '<a href="/agent?includeLetAgreed=true">Agent link</a><p>Nice active flat</p>'
        resp = _make_response(text=html)
        assert _check_rightmove(resp) == ListingStatus.ACTIVE


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

    def test_let_agreed_text(self):
        html = "<html><div class='status'>Let agreed</div><p>Nice flat</p></html>"
        resp = _make_response(text=html)
        assert _check_zoopla(resp) == ListingStatus.LET_AGREED

    def test_letting_agreed_text(self):
        html = "<html><span>Letting agreed</span></html>"
        resp = _make_response(text=html)
        assert _check_zoopla(resp) == ListingStatus.LET_AGREED


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

    def test_no_let_agreed_patterns(self):
        """OpenRent has no let-agreed state — always ACTIVE or REMOVED."""
        assert "openrent" not in _LET_AGREED_PATTERNS


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

    def test_let_agreed_text(self):
        html = '<html><div class="badge">Let agreed</div></html>'
        resp = _make_response(
            url="https://www.onthemarket.com/details/12345/",
            text=html,
        )
        assert _check_onthemarket(resp) == ListingStatus.LET_AGREED

    def test_under_offer_text(self):
        html = '<html><div class="badge">Under offer</div></html>'
        resp = _make_response(
            url="https://www.onthemarket.com/details/12345/",
            text=html,
        )
        assert _check_onthemarket(resp) == ListingStatus.LET_AGREED

    def test_let_agreed_false_positive_url_param(self):
        """?let-agreed=true in agent filter URLs should NOT match."""
        html = '<a href="/search?let-agreed=true">Filter</a><p>Active listing</p>'
        resp = _make_response(
            url="https://www.onthemarket.com/details/12345/",
            text=html,
        )
        assert _check_onthemarket(resp) == ListingStatus.ACTIVE


# ---------------------------------------------------------------------------
# Let-agreed pattern tests
# ---------------------------------------------------------------------------


class TestLetAgreedPatterns:
    def test_rightmove_json_tags(self):
        assert _check_let_agreed("rightmove", '"tags": ["LET_AGREED"]')
        assert _check_let_agreed("rightmove", '"tags":["LET_AGREED","FEATURED"]')

    def test_rightmove_heading(self):
        assert _check_let_agreed("rightmove", "<h1>LET AGREED</h1>")
        assert _check_let_agreed("rightmove", "<h1>LET_AGREED</h1>")

    def test_rightmove_no_false_positive_url(self):
        assert not _check_let_agreed("rightmove", "?includeLetAgreed=true")
        assert not _check_let_agreed("rightmove", "&includeLetAgreed=true")

    def test_zoopla_let_agreed(self):
        assert _check_let_agreed("zoopla", "Let agreed")
        assert _check_let_agreed("zoopla", "Letting agreed")

    def test_onthemarket_let_agreed(self):
        assert _check_let_agreed("onthemarket", "Let agreed")
        assert _check_let_agreed("onthemarket", "Let-agreed")

    def test_onthemarket_under_offer(self):
        assert _check_let_agreed("onthemarket", "Under offer")

    def test_onthemarket_no_false_positive_url(self):
        assert not _check_let_agreed("onthemarket", "?let-agreed=true")

    def test_openrent_no_patterns(self):
        assert not _check_let_agreed("openrent", "Let agreed")


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
# check_url (GET-only)
# ---------------------------------------------------------------------------


class TestCheckUrl:
    async def test_get_404_is_removed(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(status_code=404)
            status = await checker.check_url("rightmove", "https://rightmove.co.uk/1")

        assert status == ListingStatus.REMOVED
        mock_fetch.assert_called_once_with("rightmove", "https://rightmove.co.uk/1")
        await checker.close()

    async def test_get_200_active(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(text="<html>Nice flat</html>")
            status = await checker.check_url("rightmove", "https://rightmove.co.uk/1")

        assert status == ListingStatus.ACTIVE
        mock_fetch.assert_called_once()
        await checker.close()

    async def test_fetch_failure_returns_unknown(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            status = await checker.check_url("rightmove", "https://rightmove.co.uk/1")

        assert status == ListingStatus.UNKNOWN
        await checker.close()

    async def test_unknown_source_returns_unknown(self):
        checker = OffMarketChecker()
        status = await checker.check_url("fakesource", "https://example.com/1")
        assert status == ListingStatus.UNKNOWN
        await checker.close()


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

    async def test_let_agreed_in_batch(self):
        """LET_AGREED status propagates through batch results."""
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = ListingStatus.LET_AGREED

            checks = [("p1", "rightmove", "https://rightmove.co.uk/1")]
            results = await checker.check_batch(checks)

        assert len(results) == 1
        assert results[0].status == ListingStatus.LET_AGREED

        await checker.close()
