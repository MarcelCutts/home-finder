"""OnTheMarket detail envelope drift must not erase enrichment or bypass identity."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from home_finder.models import Property, PropertySource
from home_finder.scrapers.detail_fetcher import DetailFetcher

DETAIL_URL = "https://www.onthemarket.com/details/90000001/"


def _html(payload: object) -> str:
    return (
        f'<html><head><link rel="canonical" href="{DETAIL_URL}"></head><body>'
        f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script></body></html>'
    )


@pytest.fixture
def prop(make_property: Callable[..., Property]) -> Property:
    return make_property(source=PropertySource.ONTHEMARKET, source_id="90000001", url=DETAIL_URL)


@pytest.fixture(params=["legacy", "page_props"])
def envelope(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def current_detail_html(fixtures_path: Path) -> str:
    return (fixtures_path / "onthemarket_detail_next_data.html").read_text()


@pytest.fixture
def detail_payload(current_detail_html: str, envelope: str) -> dict[str, Any]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', current_detail_html, re.S)
    assert match is not None
    payload: dict[str, Any] = json.loads(match.group(1))
    if envelope == "legacy":
        payload["props"] = payload["props"]["pageProps"]
    return payload


async def _fetch(html: str, prop: Property, monkeypatch: pytest.MonkeyPatch):
    fetcher = DetailFetcher(max_gallery_images=10)
    response = SimpleNamespace(status_code=200, headers={}, text=html, url=DETAIL_URL)
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(fetcher, "_curl_get_with_retry", request)
    result = await fetcher.fetch_detail_page(prop)
    request.assert_awaited_once()
    return result


async def test_detail_fixture_retains_media_text_and_minimum_area(
    current_detail_html: str,
    detail_payload: dict[str, Any],
    envelope: str,
    prop: Property,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = current_detail_html if envelope == "page_props" else _html(detail_payload)
    result = await _fetch(html, prop, monkeypatch)

    assert result is not None
    assert result.floorplan_url == "https://images.example.test/floorplans-0-original.jpg"
    assert result.gallery_urls == [
        "https://images.example.test/images-0-largeUrl.jpg",
        "https://images.example.test/images-1-largeUrl.jpg",
    ]
    assert result.description == (
        "A bright example flat with a separate kitchen and two large windows."
    )
    assert result.features == ["Separate kitchen", "Large windows"]
    assert result.floor_area_readings is not None
    assert result.floor_area_readings[0].lower == 650
    assert result.floor_area_readings[0].kind == "minimum"
    assert result.floor_area_sqm is None


@pytest.mark.parametrize("property_data", [None, [], "wrong"])
async def test_invalid_main_listing_cannot_borrow_the_canonical_identity(
    envelope: str, property_data: object, prop: Property, monkeypatch: pytest.MonkeyPatch
) -> None:
    props = {"initialReduxState": {"property": property_data}}
    payload = {"props": {"pageProps": props} if envelope == "page_props" else props}
    assert await _fetch(_html(payload), prop, monkeypatch) is None


@pytest.mark.parametrize("state", [None, [], {}, {"results": {"list": []}}])
async def test_missing_or_malformed_state_does_not_fall_back_to_legacy_detail(
    state: object, prop: Property, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "props": {
            "pageProps": {"initialReduxState": state},
            "initialReduxState": {"property": {"id": "90000001", "description": "Stale data"}},
        }
    }
    assert await _fetch(_html(payload), prop, monkeypatch) is None


async def test_valid_empty_media_remains_valid_detail(
    envelope: str, prop: Property, monkeypatch: pytest.MonkeyPatch
) -> None:
    props = {"initialReduxState": {"property": {"id": "90000001", "images": [], "floorplans": []}}}
    payload = {"props": {"pageProps": props} if envelope == "page_props" else props}
    result = await _fetch(_html(payload), prop, monkeypatch)
    assert result is not None
    assert result.gallery_urls is result.floorplan_url is result.description is None


@pytest.mark.parametrize("overrides", [{"id": "99999999"}, {"canonicalUrl": "/details/99999999/"}])
async def test_supported_envelopes_still_require_the_requested_identity(
    detail_payload: dict[str, Any],
    envelope: str,
    overrides: dict[str, str],
    prop: Property,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    props = detail_payload["props"]
    if envelope == "page_props":
        props = props["pageProps"]
    props["initialReduxState"]["property"].update(overrides)
    assert await _fetch(_html(detail_payload), prop, monkeypatch) is None
