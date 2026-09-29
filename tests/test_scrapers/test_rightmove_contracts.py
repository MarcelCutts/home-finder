"""Retained search contract, visible parse failures and honest legacy detail support."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from bs4 import BeautifulSoup

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import Property
from home_finder.pipeline.capture_replay import _ReplayDetailFetcher
from home_finder.pipeline.scraping import _run_scrape
from home_finder.scrapers.rightmove import RightmoveScraper
from home_finder.scrapers.rightmove_payloads import (
    MAX_PAYLOAD_CHARACTERS,
    MAX_SEARCH_LISTINGS,
    detail_property,
    parse_structured_search,
)


@pytest.fixture
def next_payload(fixtures_path: Path):
    soup = BeautifulSoup((fixtures_path / "rightmove_next_search.html").read_text(), "html.parser")
    return json.loads(soup.find("script", id="__NEXT_DATA__").string)


def as_soup(payload):
    return BeautifulSoup(
        '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>",
        "html.parser",
    )


def test_structured_search_retains_summary_coordinates_and_rent(next_payload) -> None:
    result = parse_structured_search(as_soup(next_payload))
    assert result is not None and result.parse_errors == 0
    assert len(result.properties) == 2
    first, second = result.properties
    assert first.source_id == "100001" and str(first.url).endswith("/properties/100001")
    assert first.description == "A one bedroom flat with a shared garden."
    assert first.price_pcm == 2500 and first.bedrooms == 1
    assert first.latitude == 51.547 and first.longitude == -0.06
    assert first.postcode == "E8" and first.image_url is not None
    assert second.price_pcm == 1600


def test_search_feature_objects_retain_plain_source_words(next_payload):
    item = next_payload["props"]["pageProps"]["searchResults"]["properties"][0]
    item["keyFeatures"] = [
        {
            "order": 1,
            "description": "Available October",
            "htmlDescription": "<b>Available October</b>",
        },
        {"order": 2, "description": "Fitted appliances", "htmlDescription": "Fitted appliances"},
    ]
    result = parse_structured_search(as_soup(next_payload))
    assert result.parse_errors == 0 and len(result.properties) == 2
    assert result.properties[0].listing_metadata.features.value == (
        "Available October",
        "Fitted appliances",
    )


def test_malformed_feature_reports_field_without_echoing_payload(next_payload):
    item = next_payload["props"]["pageProps"]["searchResults"]["properties"][0]
    item["keyFeatures"] = [{"description": {"secret": "private source value"}}]
    result = parse_structured_search(as_soup(next_payload))
    assert result.parse_errors == 1
    assert result.error_reasons == {"features.value:string_type": 1}
    assert "private" not in json.dumps(result.error_reasons)
    assert len(result.properties) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("bedrooms", None),
        ("id", True),
        ("displayAddress", ""),
        ("propertyUrl", "https://example.org/properties/100001"),
        ("propertyUrl", "/properties/999"),
        ("price", {"amount": 1000, "currencyCode": "EUR", "frequency": "monthly"}),
        ("price", {"amount": 1000, "currencyCode": "GBP", "frequency": "unknown"}),
        ("price", {"amount": True, "currencyCode": "GBP", "frequency": "monthly"}),
    ],
)
def test_bad_listing_is_counted_without_discarding_valid_neighbour(next_payload, field, value):
    next_payload["props"]["pageProps"]["searchResults"]["properties"][0][field] = value
    result = parse_structured_search(as_soup(next_payload))
    assert result is not None and result.parse_errors == 1
    assert [p.source_id for p in result.properties] == ["100002"]


def test_explicit_weekly_rent_and_studio_are_supported(next_payload) -> None:
    item = next_payload["props"]["pageProps"]["searchResults"]["properties"][0]
    item.update(bedrooms=0, price={"amount": 300, "currencyCode": "GBP", "frequency": "weekly"})
    result = parse_structured_search(as_soup(next_payload))
    assert result is not None and result.parse_errors == 0
    assert result.properties[0].price_pcm == 1300 and result.properties[0].bedrooms == 0


@pytest.mark.parametrize("count,errors", [(0, 0), (None, 1), (5, 1), (False, 1), ("0", 1)])
def test_only_explicit_zero_can_establish_empty_structured_search(next_payload, count, errors):
    payload = next_payload["props"]["pageProps"]["searchResults"]
    payload.update(properties=[], resultCount=count)
    result = parse_structured_search(as_soup(next_payload))
    assert result is not None and result.parse_errors == errors and result.properties == []


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        "<title>Verify you are human</title>",
        '<script id="__NEXT_DATA__">{broken</script>',
        '<script id="__NEXT_DATA__">{"props": null}</script>',
        '<div data-test="propertyCard"><span>changed shape</span></div>',
    ],
)
def test_unknown_or_broken_page_never_looks_like_healthy_empty_inventory(html):
    scraper = RightmoveScraper()
    assert scraper._parse_search_results(BeautifulSoup(html, "html.parser"), "unused") is None


def test_search_payload_bounds(next_payload) -> None:
    payload = next_payload["props"]["pageProps"]["searchResults"]
    payload["properties"] *= MAX_SEARCH_LISTINGS
    result = parse_structured_search(as_soup(next_payload))
    assert result is not None and result.parse_errors == 1 and not result.properties
    soup = BeautifulSoup(
        '<script id="__NEXT_DATA__">' + " " * (MAX_PAYLOAD_CHARACTERS + 1) + "</script>",
        "html.parser",
    )
    result = parse_structured_search(soup)
    assert result is not None and result.parse_errors == 1


@pytest.mark.parametrize("partial", [False, True])
async def test_actual_scrape_to_storage_retains_observations_beyond_selection_cap(
    tmp_path: Path, next_payload, monkeypatch: pytest.MonkeyPatch, partial: bool
) -> None:
    if partial:
        next_payload["props"]["pageProps"]["searchResults"]["properties"][1]["bedrooms"] = None
    seen_urls = []

    def crawler(**kwargs):
        instance = MagicMock()
        handlers = []
        instance.router.default_handler.side_effect = handlers.append

        async def run(urls):
            seen_urls.extend(urls)
            await handlers[0](
                SimpleNamespace(soup=as_soup(next_payload), request=SimpleNamespace(url=urls[0]))
            )

        instance.run.side_effect = run
        return instance

    monkeypatch.setattr("home_finder.scrapers.rightmove.BeautifulSoupCrawler", crawler)
    settings = Settings(
        _env_file=None,
        database_path=":memory:",
        data_dir=str(tmp_path),
        search_areas="e8",
        min_price=1200,
        max_price=2600,
        min_bedrooms=1,
        max_bedrooms=2,
    )
    async with PropertyStorage(":memory:") as storage:
        result = await _run_scrape(
            settings, storage, max_per_scraper=1, only_scrapers={"rightmove"}
        )
        assert result is not None
        selected, metrics = result
        assert len(selected) == 1 and len(seen_urls) == 1
        assert "sortType=6" in seen_urls[0] and "OUTCODE%5E762" in seen_urls[0]
        assert metrics[0].parse_errors == int(partial)
        assert metrics[0].is_healthy is not partial
        async with storage._read_connection() as connection:
            cursor = await connection.execute(
                "SELECT unique_id, description, latitude FROM source_listings ORDER BY unique_id"
            )
            rows = await cursor.fetchall()
        assert len(rows) == (1 if partial else 2)
        assert rows[0]["description"] == "A one bedroom flat with a shared garden."
        assert rows[0]["latitude"] == 51.547


@pytest.mark.parametrize(
    "description",
    [
        'Kitchen cupboard marked "}" in the listing.',
        'Text with {unbalanced brace and "escaped quotes".',
        "Text with backslash \\ and a closing brace }.",
    ],
)
def test_legacy_json_strings_cannot_break_object_boundary(description) -> None:
    data = {"propertyData": {"id": 123, "text": {"description": description}}}
    html = "<script>window.PAGE_MODEL = " + json.dumps(data) + ";someOtherCode();</script>"
    assert detail_property(html, "123")["text"]["description"] == description


@pytest.mark.parametrize(
    "html,reason",
    [
        ('window.__PAGE_MODEL = {"data":"[]","encoding":"on"}', "page_model_node_limit"),
        ('window.PAGE_MODEL = {"data":"[]","encoding":"on"}', "unsupported_page_model_variant"),
        ('window.PAGE_MODEL = {"propertyData":{}}', "missing_property_data"),
        ('window.PAGE_MODEL = {"propertyData":{"id":999}}', "mismatched_property_id"),
        ("<html></html>", "missing_page_model"),
        (" " * (MAX_PAYLOAD_CHARACTERS + 1), "page_model_size_limit"),
    ],
)
def test_unsupported_or_wrong_target_detail_fails_explicitly(html, reason) -> None:
    with pytest.raises(ValueError, match=reason):
        detail_property(html, "123")


async def test_retained_http_error_does_not_reach_detail_parser(monkeypatch):
    response = httpx.Response(
        429,
        content=b'window.PAGE_MODEL = {"propertyData":{"id":123}}',
        request=httpx.Request("GET", "https://www.rightmove.co.uk/properties/123"),
    )
    fetcher = _ReplayDetailFetcher(response)
    parser = MagicMock(side_effect=AssertionError("must not parse an HTTP error"))
    monkeypatch.setattr("home_finder.scrapers.detail_fetcher.detail_property", parser)
    prop = Property(
        source="rightmove",
        source_id="123",
        url=str(response.url),
        title="Fixture",
        address="Example E8",
        price_pcm=1500,
        bedrooms=1,
    )
    assert await fetcher.fetch_detail_page(prop) is None
    parser.assert_not_called()


async def test_missing_model_is_logged_at_warning(monkeypatch, sample_property):
    from home_finder.scrapers.detail_fetcher import DetailFetcher

    fetcher = DetailFetcher()
    monkeypatch.setattr(
        fetcher,
        "_httpx_get_with_retry",
        AsyncMock(
            return_value=httpx.Response(
                200,
                text="<html>unrecognised</html>",
                request=httpx.Request("GET", str(sample_property.url)),
            )
        ),
    )
    logger = MagicMock()
    monkeypatch.setattr("home_finder.scrapers.detail_fetcher.logger", logger)
    assert await fetcher._fetch_rightmove(sample_property) is None
    assert logger.warning.call_args.kwargs["error"] == "missing_page_model"


@pytest.mark.parametrize(
    "headers,prefix",
    [
        ({"cf-mitigated": "challenge"}, ""),
        ({}, "<title>Just a moment</title>"),
    ],
)
async def test_challenge_precedes_otherwise_parseable_detail(headers, prefix, monkeypatch):
    url = "https://www.rightmove.co.uk/properties/123"
    response = httpx.Response(
        200,
        headers=headers,
        text=prefix + 'window.PAGE_MODEL = {"propertyData":{"id":123,"text":{"description":"x"}}}',
        request=httpx.Request("GET", url),
    )
    fetcher = _ReplayDetailFetcher(response)
    logger = MagicMock()
    monkeypatch.setattr("home_finder.scrapers.detail_fetcher.logger", logger)
    prop = Property(
        source="rightmove",
        source_id="123",
        url=url,
        title="Fixture",
        address="Example E8",
        price_pcm=1500,
        bedrooms=1,
    )
    assert await fetcher.fetch_detail_page(prop) is None
    assert logger.warning.call_args.args[0] == "rightmove_challenge"
