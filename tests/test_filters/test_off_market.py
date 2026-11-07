"""Off-market spot checks: fetching, page assessment, batching and diagnostics."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog.testing

from home_finder.filters.off_market import (
    _SOURCE_DELAYS,
    ListingStatus,
    OffMarketChecker,
    _CurlResponseAdapter,
)

RIGHTMOVE_URL = "https://www.rightmove.co.uk/properties/123"
RIGHTMOVE_AVAILABLE_PAGE = (
    '<script>window.PAGE_MODEL = {"propertyData":{"id":123,"status":"available"}}</script>'
)


@pytest.fixture
def recorded_pauses():
    """Record the per-source pauses between checks instead of waiting them out.

    Zoopla pauses 2 seconds between requests, so a ten-check batch would wait
    for real seconds that the batch logic under test does not depend on.
    """
    with patch("home_finder.filters.off_market.asyncio.sleep", new_callable=AsyncMock) as sleep:
        yield sleep


@pytest.fixture
def curl_session():
    """Replace curl_cffi's session class; tests set ``session.get.return_value``."""
    session = MagicMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    session.get = AsyncMock()
    with patch("curl_cffi.requests.AsyncSession", return_value=session) as session_class:
        yield session_class, session


def _make_response(
    status_code: int = 200,
    text: str = "",
    url: str = RIGHTMOVE_URL,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build an httpx response whose request URL is the final URL the checker sees."""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", url),
        headers=headers or {},
    )


# ---------------------------------------------------------------------------
# CurlResponseAdapter
# ---------------------------------------------------------------------------


class TestCurlResponseAdapter:
    def test_exposes_status_text_url_and_headers(self):
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

    def test_missing_headers_become_an_empty_mapping(self):
        mock = MagicMock()
        mock.status_code = 200
        mock.text = ""
        mock.url = "https://example.com"
        mock.headers = None
        adapter = _CurlResponseAdapter(mock)
        assert adapter.headers == {}


# ---------------------------------------------------------------------------
# check_url
# ---------------------------------------------------------------------------


class TestCheckUrl:
    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [(410, ListingStatus.REMOVED), (404, ListingStatus.UNKNOWN)],
        ids=["gone", "bare-not-found"],
    )
    async def test_http_410_alone_proves_removal_but_a_bare_404_does_not(
        self, status_code, expected
    ):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(status_code=status_code)
            status = await checker.check_url("rightmove", RIGHTMOVE_URL)

        assert status == expected
        mock_fetch.assert_awaited_once_with("rightmove", RIGHTMOVE_URL)
        await checker.close()

    async def test_available_target_page_model_is_active(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(text=RIGHTMOVE_AVAILABLE_PAGE)
            status = await checker.check_url("rightmove", RIGHTMOVE_URL)

        assert status == ListingStatus.ACTIVE
        await checker.close()

    async def test_redirect_to_another_listing_is_unknown_despite_its_status(self):
        """The requested URL, not the final one, decides which listing is the target."""
        checker = OffMarketChecker()
        other_url = RIGHTMOVE_URL.replace("123", "456")

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(
                text=RIGHTMOVE_AVAILABLE_PAGE.replace("123", "456"), url=other_url
            )
            with structlog.testing.capture_logs() as captured:
                status = await checker.check_url("rightmove", RIGHTMOVE_URL)

        assert status == ListingStatus.UNKNOWN
        unknown_logs = [e for e in captured if e.get("event") == "off_market_unknown_response"]
        assert [e["reason"] for e in unknown_logs] == ["target_url_unconfirmed"]
        await checker.close()

    async def test_unsupported_source_is_unknown_without_fetching(self):
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            status = await checker.check_url("fakesource", "https://example.com/1")

        assert status == ListingStatus.UNKNOWN
        mock_fetch.assert_not_awaited()
        await checker.close()


class TestCurlFetch:
    async def test_fetches_with_chrome_impersonation_through_the_proxy(self, curl_session):
        session_class, session = curl_session
        url = "https://www.zoopla.co.uk/to-rent/details/123/"
        session.get.return_value = SimpleNamespace(status_code=429, text="", url=url, headers=None)
        checker = OffMarketChecker(proxy_url="http://proxy.test:8080")

        await checker.check_url("zoopla", url)

        session_class.assert_called_once_with(proxy="http://proxy.test:8080")
        session.get.assert_awaited_once()
        assert session.get.await_args.args == (url,)
        assert session.get.await_args.kwargs["impersonate"] == "chrome"
        assert session.get.await_args.kwargs["allow_redirects"] is True
        await checker.close()

    async def test_cf_mitigated_header_makes_an_otherwise_active_page_unknown(
        self, curl_session, fixtures_path: Path
    ):
        """The retained Zoopla fixture is active; only the curl response header changes that."""
        _, session = curl_session
        url = "https://www.zoopla.co.uk/to-rent/details/123/"
        session.get.return_value = SimpleNamespace(
            status_code=200,
            text=(fixtures_path / "zoopla_availability_linked_data.html").read_text(),
            url=url,
            headers={"cf-mitigated": "challenge", "server": "cloudflare"},
        )
        checker = OffMarketChecker()

        with structlog.testing.capture_logs() as captured:
            status = await checker.check_url("zoopla", url)

        assert status == ListingStatus.UNKNOWN
        unknown_logs = [e for e in captured if e.get("event") == "off_market_unknown_response"]
        assert len(unknown_logs) == 1
        assert unknown_logs[0]["reason"] == "challenge"
        assert unknown_logs[0]["cf_mitigated"] == "challenge"
        assert unknown_logs[0]["server"] == "cloudflare"
        await checker.close()


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("recorded_pauses")
class TestCircuitBreaker:
    async def test_circuit_breaker_aborts_after_threshold(self):
        """After 5 consecutive UNKNOWNs for a source, remaining checks are skipped."""
        checker = OffMarketChecker()

        # Every check is UNKNOWN, as for a source that keeps answering 429.
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

        # 8 actual calls before the breaker trips; the remaining 2 are marked UNKNOWN unchecked.
        assert mock_check.call_count == 8
        assert [r.status for r in batch.results] == [
            *statuses,
            ListingStatus.UNKNOWN,
            ListingStatus.UNKNOWN,
        ]
        assert batch.circuit_breakers_tripped == ["zoopla"]

        await checker.close()


# ---------------------------------------------------------------------------
# check_batch: results, per-source pacing and breakdown
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("recorded_pauses")
class TestCheckBatchBasic:
    async def test_results_keep_each_checks_property_source_url_and_status(self):
        checker = OffMarketChecker()
        statuses = {
            RIGHTMOVE_URL: ListingStatus.LET_AGREED,
            "https://openrent.com/2": ListingStatus.ACTIVE,
        }

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = lambda source, url: statuses[url]

            checks = [
                ("prop-1", "rightmove", RIGHTMOVE_URL),
                ("prop-2", "openrent", "https://openrent.com/2"),
            ]
            batch = await checker.check_batch(checks)

        assert {(r.property_id, r.source, r.url, r.status) for r in batch.results} == {
            ("prop-1", "rightmove", RIGHTMOVE_URL, ListingStatus.LET_AGREED),
            ("prop-2", "openrent", "https://openrent.com/2", ListingStatus.ACTIVE),
        }

        await checker.close()

    async def test_checks_for_one_source_pause_between_requests(self, recorded_pauses):
        """Each source pauses for its own delay between checks, not after its last one."""
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = ListingStatus.ACTIVE

            checks = [
                ("p1", "rightmove", RIGHTMOVE_URL),
                ("p2", "rightmove", "https://rightmove.co.uk/2"),
                ("p3", "zoopla", "https://zoopla.co.uk/1"),
            ]
            batch = await checker.check_batch(checks)

        assert len(batch.results) == 3
        assert mock_check.call_count == 3
        # Two Rightmove checks need one pause; a single Zoopla check needs none.
        assert [call.args[0] for call in recorded_pauses.await_args_list] == [
            _SOURCE_DELAYS["rightmove"]
        ]

        await checker.close()

    async def test_batch_by_source_breakdown(self):
        """BatchResult counts every status for each source, including the zero counts."""
        checker = OffMarketChecker()

        with patch.object(checker, "check_url", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = [ListingStatus.ACTIVE, ListingStatus.REMOVED]

            checks = [
                ("p1", "rightmove", RIGHTMOVE_URL),
                ("p2", "rightmove", "https://rightmove.co.uk/2"),
            ]
            batch = await checker.check_batch(checks)

        assert batch.by_source == {
            "rightmove": {"active": 1, "removed": 1, "let_agreed": 0, "unknown": 0}
        }
        assert batch.circuit_breakers_tripped == []

        await checker.close()


# ---------------------------------------------------------------------------
# Logging assertions
# ---------------------------------------------------------------------------


class TestCheckUrlLogging:
    async def test_each_check_logs_status_reason_and_final_url(self):
        """check_url emits a DEBUG log with response diagnostics."""
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(text=RIGHTMOVE_AVAILABLE_PAGE)
            with structlog.testing.capture_logs() as captured:
                await checker.check_url("rightmove", RIGHTMOVE_URL)

        debug_logs = [e for e in captured if e.get("event") == "off_market_check_result"]
        assert len(debug_logs) == 1
        log = debug_logs[0]
        assert log["source"] == "rightmove"
        assert log["status"] == "active"
        assert log["reason"] == "target_structured_status"
        assert log["status_code"] == 200
        assert log["final_url"] == RIGHTMOVE_URL
        assert "response_time_ms" in log
        await checker.close()

    async def test_unknown_response_warning_has_reason_but_no_page_body(self):
        """check_url emits a WARNING when status is UNKNOWN, without listing content."""
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _make_response(status_code=429, text="Rate limited")
            with structlog.testing.capture_logs() as captured:
                status = await checker.check_url("rightmove", RIGHTMOVE_URL)

        assert status == ListingStatus.UNKNOWN
        unknown_logs = [e for e in captured if e.get("event") == "off_market_unknown_response"]
        assert len(unknown_logs) == 1
        log = unknown_logs[0]
        assert log["status_code"] == 429
        assert log["reason"] == "http_429"
        assert "body_preview" not in log
        await checker.close()

    async def test_fetch_failure_is_unknown_and_logged(self):
        """check_url emits a WARNING when fetch returns None."""
        checker = OffMarketChecker()

        with patch.object(checker, "_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            with structlog.testing.capture_logs() as captured:
                status = await checker.check_url("rightmove", RIGHTMOVE_URL)

        assert status == ListingStatus.UNKNOWN
        unknown_logs = [e for e in captured if e.get("event") == "off_market_unknown_response"]
        assert len(unknown_logs) == 1
        assert unknown_logs[0]["reason"] == "fetch_failed"
        await checker.close()
