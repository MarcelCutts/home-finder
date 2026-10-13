"""Collection health must describe received evidence, including partial failures.

Empty and broken pages below are synthetic contract fixtures, not claims that
the current live portals were contacted or their layouts qualified.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from home_finder.scrapers.base import ScrapeCancelled
from home_finder.scrapers.openrent import OpenRentScraper
from home_finder.scrapers.zoopla import ZooplaScraper

FIXTURES = Path(__file__).parents[1] / "fixtures"
SEARCH = {"min_price": 1000, "max_price": 2500, "min_bedrooms": 0, "max_bedrooms": 2, "area": "e8"}


def rsc_page(records):
    payload = "79:" + json.dumps({"regularListingsFormatted": records})
    return f"<script>self.__next_f.push({json.dumps([1, payload])})</script>"


@pytest.fixture(params=["openrent", "zoopla"])
def portal(request, monkeypatch):
    if request.param == "openrent":
        scraper = OpenRentScraper()
        monkeypatch.setattr(scraper, "PAGE_DELAY_SECONDS", 0)
        empty = "<script>var PROPERTYIDS=[]; var prices=[]; var bedrooms=[];</script>"
        broken_card = '<a href="/property-to-rent/london/999999">Incomplete flat</a>'
    else:
        scraper = ZooplaScraper()
        monkeypatch.setattr(scraper, "_warm_up", AsyncMock())
        monkeypatch.setattr(scraper, "_page_delay", AsyncMock())
        empty = rsc_page([])
        broken_card = '<div data-testid="search-result">Incomplete flat</div>'
    valid = (FIXTURES / f"{request.param}_search.html").read_text()
    return scraper, empty, broken_card, valid


async def test_recognized_empty_is_healthy_and_stops(portal, monkeypatch):
    scraper, empty, _, _ = portal
    fetch = AsyncMock(return_value=empty)
    monkeypatch.setattr(scraper, "_fetch_page", fetch)
    result = await scraper.scrape(**SEARCH)
    assert result.is_healthy
    assert result.properties == result.observations == []
    assert result.pages_fetched == 1
    assert result.stop_reason == "empty_page"
    fetch.assert_awaited_once()


@pytest.mark.parametrize(
    ("html", "reason"),
    [
        ("<html></html>", "unrecognized_page"),
        (
            "<html><title>Just a moment...</title>"
            '<script src="/cdn-cgi/challenge-platform/a.js" /></html>',
            "challenge",
        ),
        ("<html><h1>A new layout</h1><p>No properties found</p></html>", "unrecognized_page"),
    ],
)
async def test_unknown_or_challenge_html_is_not_healthy_empty(portal, monkeypatch, html, reason):
    scraper, _, _, _ = portal
    fetch = AsyncMock(return_value=html)
    monkeypatch.setattr(scraper, "_fetch_page", fetch)
    result = await scraper.scrape(**SEARCH)
    assert not result.is_healthy
    assert result.parse_errors > 0
    assert result.pages_failed == 0
    assert result.stop_reason == "parse_error"
    assert result.failure_reasons == [reason]
    fetch.assert_awaited_once()


async def test_incomplete_cards_are_errors_not_zero_matches(portal, monkeypatch):
    scraper, empty, broken_card, _ = portal
    monkeypatch.setattr(scraper, "_fetch_page", AsyncMock(return_value=empty + broken_card))
    result = await scraper.scrape(**SEARCH)
    assert not result.is_healthy
    assert result.parse_errors == 1
    assert result.properties == []


async def test_partial_page_retains_good_listings_and_error_count(portal, monkeypatch):
    scraper, empty, broken_card, valid = portal
    fetch = AsyncMock(side_effect=[valid + broken_card, empty])
    monkeypatch.setattr(scraper, "_fetch_page", fetch)
    result = await scraper.scrape(**SEARCH)
    assert len(result.properties) == len(result.observations) == 3
    assert result.parse_errors == 1
    assert result.pages_fetched == 2
    assert result.failure_reasons == ["invalid_records"]
    assert not result.is_healthy


async def test_cancelled_partial_page_keeps_received_errors(portal, monkeypatch):
    scraper, _, broken_card, valid = portal
    waiting = asyncio.Event()

    async def fetch(*args, **kwargs):
        if not waiting.is_set():
            waiting.set()
            return valid + broken_card
        await asyncio.Future()

    # Signal only once pagination reaches the second fetch, after recording page 1.
    second_page = asyncio.Event()

    async def tracked_fetch(*args, **kwargs):
        if waiting.is_set():
            second_page.set()
        return await fetch(*args, **kwargs)

    monkeypatch.setattr(scraper, "_fetch_page", tracked_fetch)
    task = asyncio.create_task(scraper.scrape(**SEARCH))
    await asyncio.wait_for(second_page.wait(), 2)
    task.cancel()
    with pytest.raises(ScrapeCancelled) as cancelled:
        await task
    assert len(cancelled.value.result.observations) == 3
    assert cancelled.value.result.parse_errors == 1
    assert cancelled.value.result.failure_reasons == ["invalid_records"]
    assert cancelled.value.result.stop_reason == "cancelled"


@pytest.mark.parametrize("records", [None, {}, [{}], [{"listingId": "bad"}], [{"listingId": 1}]])
async def test_zoopla_invalid_rsc_cannot_be_hidden_by_absent_html(records, monkeypatch):
    scraper = ZooplaScraper()
    monkeypatch.setattr(scraper, "_warm_up", AsyncMock())
    monkeypatch.setattr(scraper, "_fetch_page", AsyncMock(return_value=rsc_page(records)))
    result = await scraper.scrape(**SEARCH)
    assert not result.is_healthy
    assert result.parse_errors > 0
    assert result.properties == []


def test_zoopla_rsc_partial_validation_and_conversion_failures_are_counted():
    valid = {
        "listingId": 123,
        "price": "£1850 pcm",
        "title": "1 bed flat",
        "address": "Test Street E8",
        "listingUris": {"detail": "/to-rent/details/123/"},
        "features": [{"iconId": "bed", "content": 1}],
    }
    parsed = ZooplaScraper()._parse_search_page(
        rsc_page([valid, valid, {"listingId": "bad"}, {"listingId": 2}])
    )
    assert [p.source_id for p in parsed.properties] == ["123"]
    assert parsed.parse_errors == 2


def test_zoopla_html_fallback_retains_primary_failure():
    html = (FIXTURES / "zoopla_search.html").read_text()
    parsed = ZooplaScraper()._parse_search_page(rsc_page([{}]) + html)
    assert len(parsed.properties) == 3
    assert parsed.parse_errors == 1


def test_openrent_structured_ids_without_links_are_incomplete():
    from bs4 import BeautifulSoup

    html = "<script>var PROPERTYIDS=[123]; var prices=[1800]; var bedrooms=[1];</script>"
    parsed = OpenRentScraper()._parse_search_page(BeautifulSoup(html, "html.parser"))
    assert parsed.parse_errors == 1
    assert parsed.properties == []
