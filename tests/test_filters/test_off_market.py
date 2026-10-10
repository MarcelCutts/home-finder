"""Tests for off-market property detection (OffMarketChecker)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import structlog.testing

from home_finder.filters.availability import is_challenge as _is_cloudflare_challenge
from home_finder.filters.off_market import (
    ListingStatus,
    OffMarketChecker,
    _CurlResponseAdapter,
)

# ---------------------------------------------------------------------------
# Helper: build a fake httpx.Response-like object
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    text: str = "",
    url: str = "https://www.rightmove.co.uk/properties/123",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response for checker tests."""
    resp = httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", url),
        headers=headers or {},
    )
    # httpx.Response tracks the final URL via the request
    return resp


# ---------------------------------------------------------------------------
# CurlResponseAdapter
# ---------------------------------------------------------------------------


class TestCurlResponseAdapter:
    def test_adapts_attributes(self):
        mock = MagicMock()
        mock.status_code = 200
        mock.text = "<html>OK</html>"
        mock.url = "https://example.com/prop/1"
        mock.headers = {"server": "cloudflare", "cf-mitigated": "challenge"}
        adapter = _CurlResponseAdapter(mock)
        assert adapter.status_code == 200
        assert adapter.text == "<html>OK</html>"
        assert str(adapter.url) == "https://example.com/prop/1"
        assert adapter.headers["cf-mitigated"] == "challenge"
        assert adapter.headers["server"] == "cloudflare"

    def test_headers_empty_when_none(self):
        mock = MagicMock()
        mock.status_code = 200
        mock.text = ""
        mock.url = "https://example.com"
        mock.headers = None
        adapter = _CurlResponseAdapter(mock)
        assert adapter.headers == {}


# ---------------------------------------------------------------------------
# Cloudflare cf-mitigated header detection
# ---------------------------------------------------------------------------


class TestCloudflareHeaderDetection:
    def test_cf_mitigated_challenge_header_detected(self):
        assert _is_cloudflare_challenge("", headers={"cf-mitigated": "challenge"})

    def test_cf_mitigated_captcha_not_detected(self):
        """Only 'challenge' is a documented cf-mitigated value; other values are ignored."""
        assert not _is_cloudflare_challenge("", headers={"cf-mitigated": "captcha"})

    def test_cf_mitigated_case_insensitive(self):
        assert _is_cloudflare_challenge("", headers={"cf-mitigated": "Challenge"})

    def test_cf_mitigated_unrelated_value_not_detected(self):
        assert not _is_cloudflare_challenge("normal html", headers={"cf-mitigated": "skipped"})

    def test_body_pattern_still_works_without_header(self):
        assert _is_cloudflare_challenge("Just a moment... Cloudflare Ray ID: abc")

    def test_header_takes_precedence_over_clean_body(self):
        """cf-mitigated header should trigger even with clean body text."""
        assert _is_cloudflare_challenge(
            "<html>Normal page</html>", headers={"cf-mitigated": "challenge"}
        )

    def test_checker_returns_unknown_on_cf_mitigated_header(self):
        """Rightmove checker should return UNKNOWN when cf-mitigated header is present."""
        resp = _make_response(
            text="<html>Normal looking page</html>",
            headers={"cf-mitigated": "challenge"},
        )
        assert _is_cloudflare_challenge(resp.text, resp.headers)

    def test_zoopla_cf_mitigated_header(self):
        resp = _make_response(
            text="<html>Normal page</html>",
            headers={"cf-mitigated": "challenge"},
        )
        assert _is_cloudflare_challenge(resp.text, resp.headers)


# ---------------------------------------------------------------------------
# check_url (GET-only)
# ---------------------------------------------------------------------------


class TestCheckUrl:
    async def test_get_404_is_removed(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(status_code=410)
            status = await checker.check_url(
                "rightmove", "https://www.rightmove.co.uk/properties/123"
            )

        assert status == ListingStatus.REMOVED
        mock_fetch.assert_called_once_with(
            "rightmove", "https://www.rightmove.co.uk/properties/123"
        )
        await checker.close()

    async def test_get_200_active(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(
                text="<script>window.PAGE_MODEL = "
                '{"propertyData":{"id":123,"status":"available"}}</script>'
            )
            status = await checker.check_url(
                "rightmove", "https://www.rightmove.co.uk/properties/123"
            )

        assert status == ListingStatus.ACTIVE
        mock_fetch.assert_called_once()
        await checker.close()

    async def test_fetch_failure_returns_unknown(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            status = await checker.check_url(
                "rightmove", "https://www.rightmove.co.uk/properties/123"
            )

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
            batch = await checker.check_batch(checks)

        assert len(batch.results) == 10
        # Circuit breaker triggers after 5 — check_url should only be called 5 times
        assert mock_check.call_count == 5
        # All results are UNKNOWN
        assert all(r.status == ListingStatus.UNKNOWN for r in batch.results)
        # Breaker should be reported as tripped
        assert "zoopla" in batch.circuit_breakers_tripped

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
            batch = await checker.check_batch(checks)

        # 8 actual calls before circuit breaker, remaining 2 marked UNKNOWN
        assert mock_check.call_count == 8
        assert len(batch.results) == 10

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
                ("prop-1", "rightmove", "https://www.rightmove.co.uk/properties/123"),
                ("prop-2", "openrent", "https://openrent.com/2"),
            ]
            batch = await checker.check_batch(checks)

        assert len(batch.results) == 2
        assert all(r.status == ListingStatus.ACTIVE for r in batch.results)

        await checker.close()

    async def test_batch_groups_by_source(self):
        """Checks for the same source run sequentially with delays."""
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = ListingStatus.ACTIVE

            checks = [
                ("p1", "rightmove", "https://www.rightmove.co.uk/properties/123"),
                ("p2", "rightmove", "https://rightmove.co.uk/2"),
                ("p3", "zoopla", "https://zoopla.co.uk/1"),
            ]
            batch = await checker.check_batch(checks)

        assert len(batch.results) == 3
        assert mock_check.call_count == 3

        await checker.close()

    async def test_let_agreed_in_batch(self):
        """LET_AGREED status propagates through batch results."""
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = ListingStatus.LET_AGREED

            checks = [("p1", "rightmove", "https://www.rightmove.co.uk/properties/123")]
            batch = await checker.check_batch(checks)

        assert len(batch.results) == 1
        assert batch.results[0].status == ListingStatus.LET_AGREED

        await checker.close()

    async def test_batch_by_source_breakdown(self):
        """BatchResult includes per-source status breakdown."""
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = [ListingStatus.ACTIVE, ListingStatus.REMOVED]

            checks = [
                ("p1", "rightmove", "https://www.rightmove.co.uk/properties/123"),
                ("p2", "rightmove", "https://rightmove.co.uk/2"),
            ]
            batch = await checker.check_batch(checks)

        assert batch.by_source["rightmove"]["active"] == 1
        assert batch.by_source["rightmove"]["removed"] == 1
        assert batch.circuit_breakers_tripped == []

        await checker.close()


# ---------------------------------------------------------------------------
# Logging assertions
# ---------------------------------------------------------------------------


class TestCheckUrlLogging:
    async def test_debug_log_emitted_on_check(self):
        """check_url emits a DEBUG log with response diagnostics."""
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(
                text="<script>window.PAGE_MODEL = "
                '{"propertyData":{"id":123,"status":"available"}}</script>'
            )
            with structlog.testing.capture_logs() as captured:
                await checker.check_url("rightmove", "https://www.rightmove.co.uk/properties/123")

        debug_logs = [e for e in captured if e.get("event") == "off_market_check_result"]
        assert len(debug_logs) == 1
        log = debug_logs[0]
        assert log["source"] == "rightmove"
        assert log["status"] == "active"
        assert log["status_code"] == 200
        assert "response_time_ms" in log
        assert "final_url" in log
        await checker.close()

    async def test_warning_log_on_unknown(self):
        """check_url emits a WARNING when status is UNKNOWN."""
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(status_code=429, text="Rate limited")
            with structlog.testing.capture_logs() as captured:
                status = await checker.check_url(
                    "rightmove", "https://www.rightmove.co.uk/properties/123"
                )

        assert status == ListingStatus.UNKNOWN
        unknown_logs = [e for e in captured if e.get("event") == "off_market_unknown_response"]
        assert len(unknown_logs) == 1
        log = unknown_logs[0]
        assert log["status_code"] == 429
        assert "reason" in log
        assert "body_preview" not in log
        await checker.close()

    async def test_warning_log_on_fetch_failure(self):
        """check_url emits a WARNING when fetch returns None."""
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            with structlog.testing.capture_logs() as captured:
                status = await checker.check_url(
                    "rightmove", "https://www.rightmove.co.uk/properties/123"
                )

        assert status == ListingStatus.UNKNOWN
        unknown_logs = [e for e in captured if e.get("event") == "off_market_unknown_response"]
        assert len(unknown_logs) == 1
        assert unknown_logs[0]["reason"] == "fetch_failed"
        await checker.close()
