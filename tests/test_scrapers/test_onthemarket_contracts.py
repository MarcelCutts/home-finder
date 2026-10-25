"""Offline contracts for OnTheMarket envelopes, health and request bounds."""

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tenacity import wait_none

from home_finder.pipeline.collection import scrape_all_platforms
from home_finder.scrapers.base import ScrapeCancelled
from home_finder.scrapers.onthemarket import OnTheMarketScraper

SEARCH_ARGS = {
    "area": "e8",
    "min_price": 1800,
    "max_price": 2600,
    "min_bedrooms": 0,
    "max_bedrooms": 2,
}


def _html(payload: object) -> str:
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def _search_html(listings: object, *, modern: bool = True, **results: object) -> str:
    envelope = {"initialReduxState": {"results": {"list": listings, **results}}}
    return _html({"props": {"pageProps": envelope} if modern else envelope})


@pytest.fixture
def scraper(monkeypatch: pytest.MonkeyPatch) -> OnTheMarketScraper:
    monkeypatch.setattr("home_finder.scrapers.onthemarket.SCRAPER_WAIT", wait_none())
    monkeypatch.setattr(OnTheMarketScraper, "PAGE_DELAY_SECONDS", 0)
    return OnTheMarketScraper()


@pytest.fixture
def listing() -> dict[str, Any]:
    return {
        "id": "90000001",
        "details-url": "/details/90000001/",
        "address": "Example Road, London E8 1AA",
        "short-price": "£2,200 pcm",
        "bedrooms": 1,
    }


@pytest.fixture
def current_search_html(fixtures_path: Path) -> str:
    return (fixtures_path / "onthemarket_search_next_data.html").read_text()


def test_current_search_fixture(scraper: OnTheMarketScraper, current_search_html: str) -> None:
    result = scraper._parse_next_data(current_search_html)

    assert result.parse_errors == 0
    assert [prop.source_id for prop in result.properties] == ["90000001", "90000002"]
    assert [prop.price_pcm for prop in result.properties] == [2200, 2300]
    first = result.properties[0]
    assert str(first.url) == "https://www.onthemarket.com/details/90000001/"
    assert first.address == "Example Road 1, London E8 1AA"
    assert first.postcode == "E8 1AA"
    assert first.latitude == pytest.approx(51.541)
    assert first.longitude == pytest.approx(-0.051)
    assert str(first.image_url) == "https://images.example.test/property-1-0.jpg"


@pytest.mark.parametrize("max_results", [None, 1])
async def test_current_fixture_scrape_preserves_observations(
    scraper: OnTheMarketScraper, current_search_html: str, max_results: int | None
) -> None:
    with patch.object(
        scraper, "_fetch_page", AsyncMock(side_effect=[current_search_html, _search_html([])])
    ) as fetch:
        result = await scraper.scrape(**SEARCH_ARGS, max_results=max_results)

    assert len(result.properties) == (1 if max_results else 2)
    assert len(result.observations) == 2
    assert result.is_healthy
    assert result.pages_fetched == (1 if max_results else 2)
    assert result.stop_reason == ("result_limit" if max_results else "empty_page")
    assert fetch.await_count == result.pages_fetched


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "page_props"])
async def test_true_empty_ignores_suggestions(
    scraper: OnTheMarketScraper, listing: dict[str, Any], modern: bool
) -> None:
    html = _search_html([], modern=modern, suggestedProperties=[listing])
    with patch.object(scraper, "_fetch_page", AsyncMock(return_value=html)) as fetch:
        result = await scraper.scrape(**SEARCH_ARGS)

    assert result.properties == result.observations == []
    assert result.parse_errors == result.pages_failed == 0
    assert result.pages_fetched == fetch.await_count == 1
    assert result.stop_reason == "empty_page"
    assert result.is_healthy


@pytest.mark.parametrize(
    "payload",
    [None, [], 1, "wrong", {}, {"props": None}, {"props": []}, {"props": {}}],
)
def test_invalid_root_is_parse_error(scraper: OnTheMarketScraper, payload: object) -> None:
    result = scraper._parse_next_data(_html(payload))
    assert result.properties == []
    assert result.parse_errors == 1


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "page_props"])
@pytest.mark.parametrize(
    "state",
    [
        None,
        [],
        "wrong",
        {},
        {"results": None},
        {"results": []},
        {"results": "wrong"},
        {"results": {}},
        {"results": {"list": None}},
        {"results": {"list": {}}},
        {"results": {"list": "wrong"}},
        {"results": {"list": 0}},
    ],
)
def test_invalid_state_is_parse_error(
    scraper: OnTheMarketScraper, state: object, modern: bool
) -> None:
    envelope = {"initialReduxState": state}
    result = scraper._parse_next_data(
        _html({"props": {"pageProps": envelope} if modern else envelope})
    )
    assert result.properties == []
    assert result.parse_errors == 1


@pytest.mark.parametrize("page_props", [None, [], "wrong", {}])
def test_malformed_page_props_does_not_fall_back_to_legacy(
    scraper: OnTheMarketScraper, page_props: object, listing: dict[str, Any]
) -> None:
    result = scraper._parse_next_data(
        _html(
            {
                "props": {
                    "pageProps": page_props,
                    "initialReduxState": {"results": {"list": [listing]}},
                }
            }
        )
    )
    assert result.properties == []
    assert result.parse_errors == 1


def test_modern_empty_does_not_fall_back_to_legacy(
    scraper: OnTheMarketScraper, listing: dict[str, Any]
) -> None:
    result = scraper._parse_next_data(
        _html(
            {
                "props": {
                    "pageProps": {"initialReduxState": {"results": {"list": []}}},
                    "initialReduxState": {"results": {"list": [listing]}},
                }
            }
        )
    )
    assert result.properties == []
    assert result.parse_errors == 0


@pytest.mark.parametrize(
    "html",
    [
        "<html>No data</html>",
        '<script id="__NEXT_DATA__">{broken</script>',
        _html({"props": {"pageProps": {"initialReduxState": {"results": {}}}}}),
    ],
)
async def test_missing_or_malformed_data_is_unhealthy(
    scraper: OnTheMarketScraper, html: str
) -> None:
    with patch.object(scraper, "_fetch_page", AsyncMock(return_value=html)) as fetch:
        result = await scraper.scrape(**SEARCH_ARGS)
    assert result.properties == []
    assert result.parse_errors == 1
    assert result.pages_fetched == fetch.await_count == 1
    assert result.pages_failed == 0
    assert result.stop_reason == "parse_error"
    assert not result.is_healthy


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "page_props"])
def test_suggestions_cannot_replace_missing_list(
    scraper: OnTheMarketScraper, listing: dict[str, Any], modern: bool
) -> None:
    envelope = {"initialReduxState": {"results": {"suggestedProperties": [listing]}}}
    result = scraper._parse_next_data(
        _html({"props": {"pageProps": envelope} if modern else envelope})
    )
    assert result.properties == []
    assert result.parse_errors == 1


@pytest.mark.parametrize("include_valid", [False, True], ids=["all_malformed", "mixed"])
async def test_malformed_rows_make_scrape_unhealthy_without_losing_valid_rows(
    scraper: OnTheMarketScraper, listing: dict[str, Any], include_valid: bool
) -> None:
    bad_rows = [None, [], "wrong", 1, {}, {**listing, "short-price": "POA"}]
    html = _search_html([*bad_rows, *([listing] if include_valid else [])])
    with patch.object(
        scraper, "_fetch_page", AsyncMock(side_effect=[html, _search_html([])])
    ) as fetch:
        result = await scraper.scrape(**SEARCH_ARGS)

    assert [prop.source_id for prop in result.properties] == (["90000001"] if include_valid else [])
    assert result.observations == result.properties
    assert result.parse_errors == len(bad_rows)
    assert result.pages_fetched == fetch.await_count == (2 if include_valid else 1)
    assert result.pages_failed == 0
    assert result.stop_reason == "parse_error"
    assert not result.is_healthy


async def test_cancellation_retains_parse_errors_and_observations(
    scraper: OnTheMarketScraper, listing: dict[str, Any]
) -> None:
    with (
        patch.object(
            scraper,
            "_fetch_page",
            AsyncMock(side_effect=[_search_html([listing, None]), asyncio.CancelledError]),
        ),
        pytest.raises(ScrapeCancelled) as cancelled,
    ):
        await scraper.scrape(**SEARCH_ARGS)

    result = cancelled.value.result
    assert [prop.source_id for prop in result.observations] == ["90000001"]
    assert result.parse_errors == 1
    assert result.pages_fetched == 1
    assert result.stop_reason == "cancelled"
    assert not result.is_healthy


@pytest.mark.parametrize(
    ("status", "attempts", "blocked"),
    [(403, 1, True), (429, 4, True), (503, 4, False), (404, 1, False)],
)
async def test_failed_fetch_bounds_requests_and_stops_pagination(
    scraper: OnTheMarketScraper, status: int, attempts: int, blocked: bool
) -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=MagicMock(status_code=status))
    with patch.object(scraper, "_get_session", AsyncMock(return_value=session)):
        result = await scraper.scrape(**SEARCH_ARGS)
        if blocked:
            url = "https://www.onthemarket.com/to-rent/property/n1/"
            assert await scraper._fetch_page(url) is None

    assert session.get.await_count == attempts
    assert result.pages_failed == 1
    assert result.pages_fetched == result.parse_errors == 0
    assert result.stop_reason == "page_error"
    assert not result.is_healthy
    assert scraper.should_skip_remaining_areas is blocked


@pytest.mark.parametrize(("status", "attempts"), [(403, 1), (429, 4)])
async def test_source_block_skips_remaining_pipeline_areas(
    scraper: OnTheMarketScraper, status: int, attempts: int
) -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=MagicMock(status_code=status))
    with (
        patch("home_finder.pipeline.collection.OnTheMarketScraper", return_value=scraper),
        patch.object(scraper, "_get_session", AsyncMock(return_value=session)),
        patch.object(scraper, "area_delay", AsyncMock()),
    ):
        properties, metrics = await scrape_all_platforms(
            min_price=1800,
            max_price=2600,
            min_bedrooms=0,
            max_bedrooms=2,
            search_areas=["e8", "n1", "e5"],
            only_scrapers={"onthemarket"},
        )

    assert properties == []
    assert session.get.await_count == attempts
    assert len(metrics) == 1
    assert metrics[0].areas_attempted == metrics[0].areas_completed == 1
    assert metrics[0].pages_failed == 1
    assert not metrics[0].is_healthy


async def test_transport_retry_budget_is_per_area(scraper: OnTheMarketScraper) -> None:
    session = MagicMock()
    session.get = AsyncMock(side_effect=OSError("connection failed"))
    with patch.object(scraper, "_get_session", AsyncMock(return_value=session)):
        result = await scraper.scrape(**SEARCH_ARGS)

    assert session.get.await_count == scraper.MAX_RETRIES
    assert result.pages_failed == 1
    assert not result.is_healthy
    assert not scraper.should_skip_remaining_areas


@pytest.mark.parametrize("statuses", [[429], [503], [429, 503]])
async def test_transient_http_failure_can_recover(
    scraper: OnTheMarketScraper, statuses: list[int]
) -> None:
    session = MagicMock()
    session.get = AsyncMock(
        side_effect=[
            *(MagicMock(status_code=status) for status in statuses),
            MagicMock(status_code=200, text=_search_html([])),
        ]
    )
    with patch.object(scraper, "_get_session", AsyncMock(return_value=session)):
        result = await scraper.scrape(**SEARCH_ARGS)

    assert session.get.await_count == len(statuses) + 1
    assert result.is_healthy
    assert not scraper.should_skip_remaining_areas


@pytest.mark.parametrize("failure", [MagicMock(status_code=503), OSError("connection failed")])
async def test_rate_limit_followed_by_other_exhaustion_stops_source(
    scraper: OnTheMarketScraper, failure: object
) -> None:
    session = MagicMock()
    session.get = AsyncMock(side_effect=[MagicMock(status_code=429), failure, failure, failure])
    with patch.object(scraper, "_get_session", AsyncMock(return_value=session)):
        result = await scraper.scrape(**SEARCH_ARGS)
        assert await scraper._fetch_page("https://www.onthemarket.com/to-rent/property/n1/") is None

    assert session.get.await_count == scraper.MAX_RETRIES
    assert result.pages_failed == 1
    assert not result.is_healthy
    assert scraper.should_skip_remaining_areas


async def test_later_page_failure_keeps_received_observations(
    scraper: OnTheMarketScraper, current_search_html: str
) -> None:
    with patch.object(
        scraper, "_fetch_page", AsyncMock(side_effect=[current_search_html, None])
    ) as fetch:
        result = await scraper.scrape(**SEARCH_ARGS)

    assert fetch.await_count == 2
    assert len(result.properties) == len(result.observations) == 2
    assert result.pages_fetched == result.pages_failed == 1
    assert result.stop_reason == "page_error"
    assert not result.is_healthy
