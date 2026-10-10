"""Off-market property detection via URL spot-checking.

Checks target-bound structured status or primary status headings. Bare 404s,
redirects, incomplete pages and challenges remain unknown; 410 is a source URL
removal signal, not evidence that the home has been let.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

import httpx

from home_finder.filters.availability import ListingStatus as ListingStatus
from home_finder.filters.availability import assess_listing_page
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
        if source not in _SOURCE_DELAYS:
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

        evidence = assess_listing_page(
            source,
            status_code=response.status_code,
            html=response.text,
            final_url=str(response.url),
            requested_url=url,
            headers=response.headers,
        )
        status = evidence.status

        log_kwargs: dict[str, object] = {
            "source": source,
            "url": url,
            "status": status.value,
            "reason": evidence.reason,
            "status_code": response.status_code,
            "response_time_ms": elapsed_ms,
            "final_url": str(response.url),
        }
        logger.debug("off_market_check_result", **log_kwargs)

        if status == ListingStatus.UNKNOWN:
            headers = {key.lower(): value for key, value in response.headers.items()}
            logger.warning(
                "off_market_unknown_response",
                source=source,
                url=url,
                status_code=response.status_code,
                cf_mitigated=headers.get("cf-mitigated", ""),
                server=headers.get("server", ""),
                reason=evidence.reason,
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
