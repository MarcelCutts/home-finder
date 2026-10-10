"""Wrong-advert responses must not escape as detail data or retained evidence."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import enrich_merged_properties
from home_finder.models import MergedProperty, PropertySource
from home_finder.scrapers.detail_fetcher import DetailFetcher

URLS = {
    PropertySource.RIGHTMOVE: "https://www.rightmove.co.uk/properties/100001",
    PropertySource.ZOOPLA: "https://www.zoopla.co.uk/to-rent/details/100001/",
    PropertySource.OPENRENT: "https://www.openrent.com/property/100001",
    PropertySource.ONTHEMARKET: "https://www.onthemarket.com/details/100001/",
}
ALIASES = {
    PropertySource.RIGHTMOVE: "https://rightmove.co.uk/properties/100001/?channel=RES_LET#view",
    PropertySource.ZOOPLA: "https://zoopla.co.uk/details/100001?search=public",
    PropertySource.OPENRENT: "https://www.openrent.co.uk/property-to-rent/london/flat/100001",
    PropertySource.ONTHEMARKET: "https://onthemarket.com/details/100001?view=full",
}


def page(source, *, record=None, head=None):
    """Synthetic contract; main IDs and related advert IDs are deliberately distinct."""
    if head is None:
        head = f'<link rel="canonical" href="{URLS[source]}">'
    if record is None:
        record = {"id": "100001", "description": "Main advert description."}
    if source == PropertySource.RIGHTMOVE:
        record = {"text": {"description": "Main advert description."}, **record}
        body = "<script>window.PAGE_MODEL = " + json.dumps({"propertyData": record}) + ";</script>"
    elif source == PropertySource.OPENRENT:
        body = '<div class="description">Main advert description.</div>'
    else:
        if source == PropertySource.ZOOPLA:
            record = {"detailedDescription": "Main advert description.", **record}
            data = {"props": {"pageProps": {"listing": record}}}
        else:
            data = {"props": {"initialReduxState": {"property": record}}}
        body = '<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>"
    return f'<html><head>{head}</head><body>{body}<a href="/details/999/">Nearby</a></body></html>'


def stub_response(fetcher, monkeypatch, html, url):
    response = SimpleNamespace(status_code=200, headers={}, text=html, url=url)
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(fetcher, "_httpx_get_with_retry", request)
    monkeypatch.setattr(fetcher, "_curl_get_with_retry", request)
    return request


@pytest.mark.parametrize("source", list(PropertySource))
async def test_redirect_to_another_advert_is_refused_even_with_matching_body(
    make_property, monkeypatch, source
):
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    with structlog.testing.capture_logs() as logs:
        async with DetailFetcher() as fetcher:
            request = stub_response(
                fetcher, monkeypatch, page(source), URLS[source].replace("100001", "222")
            )
            assert await fetcher.fetch_detail_page(prop) is None
    request.assert_awaited_once()
    assert logs[-1]["event"] == "detail_identity_refused"
    assert logs[-1]["reason"] == "response_listing_mismatch"


@pytest.mark.parametrize("source", list(PropertySource))
@pytest.mark.parametrize("target", ["foreign_host", "generic_page", "missing", "credentials"])
async def test_nonlisting_final_targets_cannot_borrow_the_requested_identity(
    make_property, monkeypatch, source, target
):
    url = URLS[source]
    invalid = {
        "foreign_host": url.replace(url.split("/")[2], "unrelated.example"),
        "generic_page": url.rsplit("/", 2)[0] + "/properties-to-rent/",
        "missing": "",
        "credentials": url.replace("https://", "https://secret:password@"),
    }[target]
    prop = make_property(source=source, source_id="100001", url=url)
    with structlog.testing.capture_logs() as logs:
        async with DetailFetcher() as fetcher:
            stub_response(fetcher, monkeypatch, page(source), invalid)
            assert await fetcher.fetch_detail_page(prop) is None
    assert logs[-1]["reason"] == "unrecognized_response_url"
    assert "password" not in json.dumps(logs) and "unrelated.example" not in json.dumps(logs)


@pytest.mark.parametrize("source", list(PropertySource))
async def test_inconsistent_request_is_refused_before_http(make_property, monkeypatch, source):
    prop = make_property(source=source, source_id="222", url=URLS[source])
    with structlog.testing.capture_logs() as logs:
        async with DetailFetcher() as fetcher:
            request = stub_response(fetcher, monkeypatch, page(source), URLS[source])
            assert await fetcher.fetch_detail_page(prop) is None
    request.assert_not_awaited()
    assert logs[-1]["reason"] == "unresolved_request_identity"


@pytest.mark.parametrize("source", list(PropertySource))
async def test_aliases_and_related_ids_preserve_the_main_listing(
    make_property, monkeypatch, source
):
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    record = {
        "id": "100001",
        "description": "Main advert description.",
        "similarProperties": [{"id": "999", "canonicalUrl": "/details/999/"}],
        "images": [],
    }
    with structlog.testing.capture_logs() as logs:
        async with DetailFetcher() as fetcher:
            stub_response(fetcher, monkeypatch, page(source, record=record), ALIASES[source])
            detail = await fetcher.fetch_detail_page(prop)
    assert detail.description == "Main advert description."
    verified = [entry for entry in logs if entry["event"] == "detail_identity_verified"]
    assert len(verified) == 1
    assert (
        verified[0]["response_url_sha256"] == hashlib.sha256(ALIASES[source].encode()).hexdigest()
    )
    assert "canonical" in verified[0]["markers"] and "?" not in json.dumps(logs)


@pytest.mark.parametrize("source", list(PropertySource))
async def test_missing_body_identity_does_not_make_new_evidence(make_property, monkeypatch, source):
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    html = page(source, record={"description": "An advert without identity."}, head="")
    with structlog.testing.capture_logs() as logs:
        async with DetailFetcher() as fetcher:
            stub_response(fetcher, monkeypatch, html, URLS[source])
            assert await fetcher.fetch_detail_page(prop) is None
    assert logs[-1]["reason"] == "missing_page_identity"


@pytest.mark.parametrize("source", list(PropertySource))
@pytest.mark.parametrize("marker", ["canonical", "og:url"])
async def test_conflicting_page_marker_overrules_a_matching_main_id(
    make_property, monkeypatch, source, marker
):
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    wrong = URLS[source].replace("100001", "222") + "?private=secret"
    head = f'<link rel="canonical" href="{URLS[source]}">'
    head += (
        f'<link rel="canonical" href="{wrong}">'
        if marker == "canonical"
        else f'<meta property="og:url" content="{wrong}">'
    )
    with structlog.testing.capture_logs() as logs:
        async with DetailFetcher() as fetcher:
            stub_response(fetcher, monkeypatch, page(source, head=head), URLS[source])
            assert await fetcher.fetch_detail_page(prop) is None
    assert logs[-1]["reason"] == "page_listing_mismatch"
    assert "private" not in json.dumps(logs) and "secret" not in json.dumps(logs)


@pytest.mark.parametrize(
    "source", [PropertySource.RIGHTMOVE, PropertySource.ZOOPLA, PropertySource.ONTHEMARKET]
)
@pytest.mark.parametrize("wrong_id", ["222", None, True, 100001.0])
async def test_conflicting_embedded_id_cannot_borrow_matching_response_and_canonical(
    make_property, monkeypatch, source, wrong_id
):
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    async with DetailFetcher() as fetcher:
        stub_response(fetcher, monkeypatch, page(source, record={"id": wrong_id}), URLS[source])
        assert await fetcher.fetch_detail_page(prop) is None


@pytest.mark.parametrize("field", ["id", "listingId"])
@pytest.mark.parametrize("payload", ["description", "area"])
async def test_zoopla_rejects_conflicting_id_on_the_selected_rsc_taxonomy(
    make_property, monkeypatch, field, payload
):
    source = PropertySource.ZOOPLA
    taxonomy = {field: "222"}
    taxonomy.update(
        {"epcRating": "D", "numBaths": 1, "detailedDescription": "Wrong advert description."}
        if payload == "description"
        else {"sizeSqft": 600}
    )
    chunk = "1:" + json.dumps({"taxonomy": taxonomy})
    html = (
        f'<html><head><link rel="canonical" href="{URLS[source]}"></head>'
        "<body><script>self.__next_f.push([1," + json.dumps(chunk) + "]);</script></body></html>"
    )
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    async with DetailFetcher() as fetcher:
        stub_response(fetcher, monkeypatch, html, URLS[source])
        assert await fetcher.fetch_detail_page(prop) is None


async def test_onthemarket_embedded_canonical_must_also_agree(make_property, monkeypatch):
    source = PropertySource.ONTHEMARKET
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    html = page(source, record={"id": "100001", "canonicalUrl": "/details/222/"})
    async with DetailFetcher() as fetcher:
        stub_response(fetcher, monkeypatch, html, URLS[source])
        assert await fetcher.fetch_detail_page(prop) is None


async def test_zoopla_main_record_id_and_listing_id_must_both_agree(make_property, monkeypatch):
    source = PropertySource.ZOOPLA
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    async with DetailFetcher() as fetcher:
        html = page(source, record={"id": "100001", "listingId": "222"})
        stub_response(fetcher, monkeypatch, html, URLS[source])
        assert await fetcher.fetch_detail_page(prop) is None


@pytest.mark.parametrize(
    "head,accepted",
    [
        ("<LINK REL='alternate CANONICAL' HREF='/property/100001'>", True),
        ("<meta content='/property/100001?view=public&amp;x=1' property='og:url'>", True),
        ("<link rel='canonical' href='?view=public'>", False),
        ("<link rel='canonical' href='#photos'>", False),
        ("<link rel='canonical'>", False),
        ("<link rel='canonical' href='http://[invalid'>", False),
        ("<link rel='canonical' href='/property/100001'>" * 17, False),
    ],
)
async def test_html_fallback_requires_an_explicit_page_identity_path(
    make_property, monkeypatch, head, accepted
):
    source = PropertySource.OPENRENT
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    async with DetailFetcher() as fetcher:
        stub_response(fetcher, monkeypatch, page(source, head=head), URLS[source])
        detail = await fetcher.fetch_detail_page(prop)
    assert (detail is not None) is accepted


@pytest.mark.parametrize("source", [PropertySource.ZOOPLA, PropertySource.ONTHEMARKET])
async def test_main_record_must_be_an_object(make_property, monkeypatch, source):
    prop = make_property(source=source, source_id="100001", url=URLS[source])
    async with DetailFetcher() as fetcher:
        payload = (
            {"props": {"pageProps": {"listing": []}}}
            if source == PropertySource.ZOOPLA
            else {"props": {"initialReduxState": {"property": []}}}
        )
        html = '<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>"
        stub_response(fetcher, monkeypatch, html, URLS[source])
        assert await fetcher.fetch_detail_page(prop) is None


async def test_wrong_otm_response_reaches_neither_retention_nor_enrichment(
    make_property, monkeypatch, tmp_path
):
    source = PropertySource.ONTHEMARKET
    prop = make_property(source=source, source_id="100001", url=URLS[source], image_url=None)
    merged = MergedProperty(
        canonical=prop,
        sources=(source,),
        source_urls={source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )
    wrong = URLS[source].replace("100001", "222")
    html = page(
        source,
        head=f'<link rel="canonical" href="{wrong}">',
        record={
            "id": "222",
            "description": "Wrong advert description.",
            "images": [{"original": "https://media.onthemarket.com/222.jpg"}],
        },
    )
    async with PropertyStorage(str(tmp_path / "test.db")) as storage, DetailFetcher() as fetcher:
        stub_response(fetcher, monkeypatch, html, wrong)
        download = AsyncMock(side_effect=AssertionError("Wrong advert image must not be fetched"))
        monkeypatch.setattr(fetcher, "download_image_bytes", download)
        result = await enrich_merged_properties(
            [merged], fetcher, storage=storage, data_dir=str(tmp_path)
        )
        assert result.enriched == [] and len(result.failed) == 1
        download.assert_not_awaited()
        assert "Wrong advert" not in result.failed[0].model_dump_json()
