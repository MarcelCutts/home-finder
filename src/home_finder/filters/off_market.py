"""Off-market property detection via URL spot-checking.

Visits each active property's listing URL and checks for definitive removal
signals (404, "no longer available" text, redirect to search) and "let agreed"
badges (200 OK with status indicator). Only flags on positive confirmation —
never on 429s, timeouts, or Cloudflare challenges.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol

import httpx

from home_finder.logging import get_logger
from home_finder.scrapers.constants import BROWSER_HEADERS

logger = get_logger(__name__)


class CheckableResponse(Protocol):
    """Minimal interface for HTTP responses used by checker functions.

    Both httpx.Response and _CurlResponseAdapter satisfy this protocol.
    """

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    @property
    def url(self) -> object: ...

    @property
    def headers(self) -> Mapping[str, str]: ...


class ListingStatus(Enum):
    """Result of checking a single listing URL."""

    ACTIVE = "active"
    REMOVED = "removed"
    LET_AGREED = "let_agreed"
    UNKNOWN = "unknown"


# Per-source rate limiting (seconds between requests)
_SOURCE_DELAYS: Final[dict[str, float]] = {
    "zoopla": 2.0,
    "onthemarket": 1.0,
    "openrent": 1.0,
    "rightmove": 0.5,
}

# Circuit breaker: abort source after this many consecutive UNKNOWN results
_CIRCUIT_BREAKER_THRESHOLD: Final = 5

from home_finder.utils.circuit_breaker import ConsecutiveFailureBreaker  # noqa: E402

# Cloudflare challenge indicators
_CLOUDFLARE_PATTERNS: Final = (
    "checking your browser",
    "cloudflare",
    "just a moment",
    "ray id",
    "enable javascript and cookies",
)

# Removal text patterns per source
_REMOVAL_PATTERNS: Final[dict[str, list[re.Pattern[str]]]] = {
    "rightmove": [
        re.compile(r"property has been removed", re.IGNORECASE),
        re.compile(r"no longer available", re.IGNORECASE),
        re.compile(r"no longer on the market", re.IGNORECASE),
    ],
    "zoopla": [
        re.compile(r"no longer available", re.IGNORECASE),
        re.compile(r"this property has been removed", re.IGNORECASE),
    ],
    "openrent": [
        re.compile(r"no longer available", re.IGNORECASE),
        re.compile(r"property not found", re.IGNORECASE),
    ],
    "onthemarket": [
        re.compile(r"no longer available", re.IGNORECASE),
        re.compile(r"property has been removed", re.IGNORECASE),
    ],
}

# Let-agreed text patterns per source.
# Patterns use lookaround to avoid false positives from URL query strings
# (e.g. ?includeLetAgreed=true, ?let-agreed=true).
_LET_AGREED_PATTERNS: Final[dict[str, list[re.Pattern[str]]]] = {
    "rightmove": [
        # High-confidence: PAGE_MODEL JSON tags array
        re.compile(r'"tags"\s*:\s*\[.*?"LET_AGREED"', re.IGNORECASE),
        # Body text: "LET AGREED" or "LET_AGREED" but not includeLetAgreed=true
        re.compile(r'(?<![?&=a-zA-Z])let[\s_]agreed(?![a-zA-Z=&])', re.IGNORECASE),
    ],
    "zoopla": [
        re.compile(r'(?<![?&=a-zA-Z])let(?:ting)?\s+agreed(?![a-zA-Z=&])', re.IGNORECASE),
    ],
    # OpenRent: no let-agreed state — removed listings 404/redirect
    "onthemarket": [
        # Body text but not ?let-agreed=true in URLs
        re.compile(r'(?<![?&=a-zA-Z/-])let[\s-]agreed(?![a-zA-Z=&])', re.IGNORECASE),
        re.compile(r'(?<![?&=a-zA-Z])under\s+offer(?![a-zA-Z=&])', re.IGNORECASE),
    ],
}


def _is_cloudflare_challenge(html: str, headers: Mapping[str, str] | None = None) -> bool:
    """Detect Cloudflare challenge pages (should be treated as UNKNOWN, not REMOVED).

    Checks both the ``cf-mitigated`` response header (the officially documented
    way to detect Cloudflare challenges) and body text patterns as a fallback.
    """
    if headers:
        cf_mitigated = headers.get("cf-mitigated", "").lower()
        if cf_mitigated == "challenge":
            return True
    lower = html.lower()[:3000]
    return any(p in lower for p in _CLOUDFLARE_PATTERNS)


def _check_let_agreed(source: str, html: str) -> bool:
    """Check if the page body contains let-agreed signals for the given source."""
    patterns = _LET_AGREED_PATTERNS.get(source)
    if not patterns:
        return False
    return any(pattern.search(html) for pattern in patterns)


def _check_rightmove(response: CheckableResponse) -> ListingStatus:
    """Check Rightmove listing status."""
    if response.status_code in (404, 410):
        return ListingStatus.REMOVED

    if response.status_code == 200:
        html = response.text
        if _is_cloudflare_challenge(html, response.headers):
            return ListingStatus.UNKNOWN
        # Redirect to search results page
        url_str = str(response.url)
        if "/property-to-rent/find" in url_str and "/properties/" not in url_str:
            return ListingStatus.REMOVED
        for pattern in _REMOVAL_PATTERNS["rightmove"]:
            if pattern.search(html[:5000]):
                return ListingStatus.REMOVED
        if _check_let_agreed("rightmove", html):
            return ListingStatus.LET_AGREED
        return ListingStatus.ACTIVE

    if response.status_code == 429 or response.status_code >= 500:
        return ListingStatus.UNKNOWN

    return ListingStatus.UNKNOWN


def _check_zoopla(response: CheckableResponse) -> ListingStatus:
    """Check Zoopla listing status."""
    if response.status_code in (404, 410):
        return ListingStatus.REMOVED

    if response.status_code == 200:
        html = response.text
        if _is_cloudflare_challenge(html, response.headers):
            return ListingStatus.UNKNOWN
        for pattern in _REMOVAL_PATTERNS["zoopla"]:
            if pattern.search(html[:5000]):
                return ListingStatus.REMOVED
        if _check_let_agreed("zoopla", html):
            return ListingStatus.LET_AGREED
        return ListingStatus.ACTIVE

    if response.status_code == 429 or response.status_code >= 500:
        return ListingStatus.UNKNOWN

    return ListingStatus.UNKNOWN


def _check_openrent(response: CheckableResponse) -> ListingStatus:
    """Check OpenRent listing status.

    OpenRent has no let-agreed state — removed listings 404 or redirect.
    """
    if response.status_code in (404, 410):
        return ListingStatus.REMOVED

    if response.status_code == 200:
        html = response.text
        # Redirect to search page (property removed)
        url_str = str(response.url)
        if "/properties-to-rent" in url_str or "homepage" in html.lower()[:1000]:
            return ListingStatus.REMOVED
        if _is_cloudflare_challenge(html, response.headers):
            return ListingStatus.UNKNOWN
        for pattern in _REMOVAL_PATTERNS["openrent"]:
            if pattern.search(html[:5000]):
                return ListingStatus.REMOVED
        return ListingStatus.ACTIVE

    if response.status_code == 429 or response.status_code >= 500:
        return ListingStatus.UNKNOWN

    return ListingStatus.UNKNOWN


def _check_onthemarket(response: CheckableResponse) -> ListingStatus:
    """Check OnTheMarket listing status."""
    if response.status_code in (404, 410):
        return ListingStatus.REMOVED

    if response.status_code == 200:
        html = response.text
        if _is_cloudflare_challenge(html, response.headers):
            return ListingStatus.UNKNOWN
        # Redirect to search (no /details/ in URL)
        url_str = str(response.url)
        if "/details/" not in url_str and "/to-rent/" in url_str:
            return ListingStatus.REMOVED
        for pattern in _REMOVAL_PATTERNS["onthemarket"]:
            if pattern.search(html[:5000]):
                return ListingStatus.REMOVED
        if _check_let_agreed("onthemarket", html):
            return ListingStatus.LET_AGREED
        return ListingStatus.ACTIVE

    if response.status_code == 429 or response.status_code >= 500:
        return ListingStatus.UNKNOWN

    return ListingStatus.UNKNOWN


# Type alias for checker functions
_CheckerFn = Callable[[CheckableResponse], ListingStatus]

# Maps source name to checker function
_SOURCE_CHECKERS: Final[dict[str, _CheckerFn]] = {
    "rightmove": _check_rightmove,
    "zoopla": _check_zoopla,
    "openrent": _check_openrent,
    "onthemarket": _check_onthemarket,
}

# Sources that need curl_cffi for TLS fingerprinting
_CURL_SOURCES: Final = {"zoopla", "onthemarket", "openrent", "rightmove"}


@dataclass
class CheckResult:
    """Result of checking a single source URL."""

    source: str
    url: str
    status: ListingStatus
    property_id: str


@dataclass
class BatchResult:
    """Aggregated result of check_batch() with per-source metadata."""

    results: list[CheckResult]
    by_source: dict[str, dict[str, int]]
    circuit_breakers_tripped: list[str]


@dataclass
class OffMarketChecker:
    """Check listing URLs for off-market signals.

    Uses the same HTTP client selection as the scraper codebase:
    curl_cffi for Zoopla/OTM/OpenRent, httpx for Rightmove.
    """

    proxy_url: str = ""
    _httpx_client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    async def _get_httpx_client(self) -> httpx.AsyncClient:
        if self._httpx_client is None:
            self._httpx_client = httpx.AsyncClient(
                headers=BROWSER_HEADERS,
                follow_redirects=True,
                timeout=30,
                proxy=self.proxy_url or None,
            )
        return self._httpx_client

    async def _fetch_with_curl(self, url: str) -> CheckableResponse | None:
        """Fetch a URL using curl_cffi with Chrome impersonation."""
        try:
            from curl_cffi.requests import AsyncSession

            async with AsyncSession(proxy=self.proxy_url or None) as session:
                response = await session.get(
                    url,
                    impersonate="chrome",
                    headers=BROWSER_HEADERS,
                    timeout=30,
                    allow_redirects=True,
                )
                return _CurlResponseAdapter(response)
        except Exception as e:
            logger.debug("curl_fetch_failed", url=url, error=str(e))
            return None

    async def _fetch_with_httpx(self, url: str) -> CheckableResponse | None:
        """Fetch a URL using httpx."""
        try:
            client = await self._get_httpx_client()
            return await client.get(url)
        except Exception as e:
            logger.debug("httpx_fetch_failed", url=url, error=str(e))
            return None

    async def _fetch(self, source: str, url: str) -> CheckableResponse | None:
        """Route to the correct HTTP client for the source."""
        if source in _CURL_SOURCES:
            return await self._fetch_with_curl(url)
        return await self._fetch_with_httpx(url)

    async def check_url(self, source: str, url: str) -> ListingStatus:
        """Check a single listing URL for removal/let-agreed signals."""
        checker = _SOURCE_CHECKERS.get(source)
        if checker is None:
            return ListingStatus.UNKNOWN

        t0 = time.monotonic()
        response = await self._fetch(source, url)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        if response is None:
            logger.warning(
                "off_market_unknown_response",
                source=source,
                url=url,
                reason="fetch_failed",
                response_time_ms=elapsed_ms,
            )
            return ListingStatus.UNKNOWN

        status = checker(response)

        log_kwargs: dict[str, object] = {
            "source": source,
            "url": url,
            "status": status.value,
            "status_code": response.status_code,
            "response_time_ms": elapsed_ms,
            "final_url": str(response.url),
        }
        logger.debug("off_market_check_result", **log_kwargs)

        if status == ListingStatus.UNKNOWN:
            headers = dict(response.headers)
            logger.warning(
                "off_market_unknown_response",
                source=source,
                url=url,
                status_code=response.status_code,
                cf_mitigated=headers.get("cf-mitigated", ""),
                server=headers.get("server", ""),
                body_preview=response.text[:300],
                response_time_ms=elapsed_ms,
            )

        return status

    async def check_batch(
        self,
        checks: list[tuple[str, str, str]],
    ) -> BatchResult:
        """Check multiple listing URLs with rate limiting and circuit breaker.

        Different sources run concurrently; within each source, requests are
        sequential with per-source rate-limiting delays.

        Args:
            checks: List of (property_id, source, url) tuples.

        Returns:
            BatchResult containing all CheckResults and per-source metadata.
        """
        # Group by source for rate limiting and circuit breaker
        by_source: dict[str, list[tuple[str, str]]] = {}
        for prop_id, source, url in checks:
            by_source.setdefault(source, []).append((prop_id, url))

        tripped_breakers: list[str] = []

        async def _check_source(
            source: str, items: list[tuple[str, str]]
        ) -> tuple[list[CheckResult], dict[str, int]]:
            source_results: list[CheckResult] = []
            breaker = ConsecutiveFailureBreaker(threshold=_CIRCUIT_BREAKER_THRESHOLD, name=source)
            delay = _SOURCE_DELAYS.get(source, 1.0)
            counts: dict[str, int] = {s.value: 0 for s in ListingStatus}
            source_t0 = time.monotonic()

            logger.info(
                "off_market_source_started",
                source=source,
                urls=len(items),
                delay_s=delay,
            )

            for i, (prop_id, url) in enumerate(items):
                if breaker.is_tripped:
                    tripped_breakers.append(source)
                    logger.warning(
                        "off_market_circuit_breaker",
                        source=source,
                        skipped=len(items) - i,
                        threshold=_CIRCUIT_BREAKER_THRESHOLD,
                    )
                    for remaining_id, remaining_url in items[i:]:
                        source_results.append(
                            CheckResult(
                                source=source,
                                url=remaining_url,
                                status=ListingStatus.UNKNOWN,
                                property_id=remaining_id,
                            )
                        )
                        counts["unknown"] += 1
                    break

                status = await self.check_url(source, url)
                source_results.append(
                    CheckResult(
                        source=source,
                        url=url,
                        status=status,
                        property_id=prop_id,
                    )
                )
                counts[status.value] += 1

                if status == ListingStatus.UNKNOWN:
                    breaker.record_failure()
                else:
                    breaker.record_success()

                # Progress log every 50 items
                checked = i + 1
                if checked % 50 == 0:
                    elapsed = time.monotonic() - source_t0
                    logger.info(
                        "off_market_progress",
                        source=source,
                        checked=checked,
                        total=len(items),
                        active=counts["active"],
                        removed=counts["removed"],
                        let_agreed=counts["let_agreed"],
                        unknown=counts["unknown"],
                        elapsed_s=round(elapsed, 1),
                        rate=round(checked / elapsed, 1) if elapsed > 0 else 0,
                    )

                if i < len(items) - 1:
                    await asyncio.sleep(delay)

            source_elapsed = round(time.monotonic() - source_t0, 1)
            logger.info(
                "off_market_source_complete",
                source=source,
                checked=len(source_results),
                active=counts["active"],
                removed=counts["removed"],
                let_agreed=counts["let_agreed"],
                unknown=counts["unknown"],
                elapsed_s=source_elapsed,
            )

            return source_results, counts

        # Run all sources concurrently (rate limiting is per-source)
        source_names = list(by_source.keys())
        source_tasks = [_check_source(source, items) for source, items in by_source.items()]
        all_source_outputs = await asyncio.gather(*source_tasks)

        results: list[CheckResult] = []
        source_breakdown: dict[str, dict[str, int]] = {}
        for source, (source_results, source_counts) in zip(
            source_names, all_source_outputs, strict=True
        ):
            results.extend(source_results)
            source_breakdown[source] = source_counts

        return BatchResult(
            results=results,
            by_source=source_breakdown,
            circuit_breakers_tripped=tripped_breakers,
        )

    async def __aenter__(self) -> OffMarketChecker:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close HTTP clients."""
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
            self._httpx_client = None


class _CurlResponseAdapter:
    """Adapt curl_cffi response to satisfy CheckableResponse protocol."""

    def __init__(self, response: object) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code  # type: ignore[attr-defined, no-any-return]

    @property
    def text(self) -> str:
        return self._response.text  # type: ignore[attr-defined, no-any-return]

    @property
    def url(self) -> object:
        return self._response.url  # type: ignore[attr-defined]

    @property
    def headers(self) -> Mapping[str, str]:
        raw = self._response.headers  # type: ignore[attr-defined]
        return dict(raw) if raw else {}
