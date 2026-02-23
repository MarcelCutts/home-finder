"""Shared retry helpers for scrapers using tenacity."""

from __future__ import annotations

from tenacity import wait_exponential, wait_random


class RetryableHttpError(Exception):
    """Raised when an HTTP response warrants a retry (429, 5xx)."""

    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} from {url}")


# Pre-built wait strategies matching existing scraper behaviour:
# exponential 2-30s + random jitter 0-1s
SCRAPER_WAIT = wait_exponential(multiplier=2, min=2, max=30) + wait_random(0, 1)
