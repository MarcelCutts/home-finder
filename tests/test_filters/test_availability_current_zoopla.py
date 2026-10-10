"""Explicit offers on current Zoopla pages must be bound to the requested listing."""

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from home_finder.filters.availability import ListingStatus, assess_listing_page

URL = "https://www.zoopla.co.uk/to-rent/details/123/"


@pytest.fixture
def current_html(fixtures_path: Path) -> str:
    return (fixtures_path / "zoopla_availability_current.html").read_text()


@pytest.fixture
def payload(current_html):
    script = BeautifulSoup(current_html, "html.parser").find("script")
    return json.loads(script.string)


def html(payload):
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


def assess(body, *, source="zoopla", url=URL, final_url=URL):
    return assess_listing_page(
        source, html=body, status_code=200, requested_url=url, final_url=final_url
    )


def test_current_fixture_recognizes_target_bound_in_stock_offer(current_html):
    evidence = assess(current_html)
    assert evidence.status == ListingStatus.ACTIVE
    assert evidence.reason == "target_structured_status"


@pytest.mark.parametrize("shape", ["object", "array", "graph"])
def test_explicit_current_node_can_use_supported_document_containers(payload, shape):
    data = {"object": payload, "array": [payload], "graph": {"@graph": [payload]}}[shape]
    assert assess(html(data)).status == ListingStatus.ACTIVE


@pytest.mark.parametrize(
    "identity",
    [None, {}, {"@id": URL}, [], URL.replace("123", "456"), "https://other.example/details/123/"],
)
def test_main_page_identity_must_be_an_exact_source_listing(payload, identity):
    payload["mainEntityOfPage"] = identity
    payload["url"] = URL
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize("url", [None, URL.replace("123", "456"), "https://other.example/123"])
def test_conflicting_top_level_url_does_not_borrow_main_entity(payload, url):
    payload["url"] = url
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


def test_missing_identity_cannot_borrow_action_url(payload):
    del payload["mainEntityOfPage"]
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


def test_offer_cannot_belong_to_another_listing(payload):
    payload["offers"]["url"] = URL.replace("123", "456")
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize(
    "offer", [None, {}, [], "InStock", {"availability": "https://schema.org/InStock"}]
)
def test_missing_or_malformed_offer_is_not_active(payload, offer):
    payload["offers"] = offer
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize("word", [None, "InStock", "https://other.example/InStock", "to_rent"])
def test_only_explicit_supported_schema_statuses_count(payload, word):
    payload["offers"]["availability"] = word
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


def test_nested_recommendation_cannot_supply_main_offer(payload):
    assert assess(html({"recommendations": [payload]})).status == ListingStatus.UNKNOWN


def test_ambiguous_graph_main_entities_remain_unknown(payload):
    other = {**payload, "mainEntityOfPage": URL.replace("123", "456")}
    assert assess(html({"@graph": [payload, other]})).status == ListingStatus.UNKNOWN


def test_conflicting_graph_offers_remain_unknown(payload):
    other = {
        **payload,
        "offers": {**payload["offers"], "availability": "https://schema.org/OutOfStock"},
    }
    assert assess(html({"@graph": [payload, other]})).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize("kind", [None, "Product", "BreadcrumbList", ["RealEstateListing"]])
def test_new_identity_path_is_scoped_to_the_observed_record_type(payload, kind):
    payload["@type"] = kind
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


def test_new_identity_path_is_zoopla_specific(payload):
    url = "https://www.onthemarket.com/details/123/"
    payload["mainEntityOfPage"] = url
    assert (
        assess(html(payload), source="onthemarket", url=url, final_url=url).status
        == ListingStatus.UNKNOWN
    )


def test_conflicting_offers_remain_unknown(payload):
    offer = payload["offers"]
    payload["offers"] = [offer, {**offer, "availability": "https://schema.org/OutOfStock"}]
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


def test_malformed_offer_cannot_hide_beside_a_valid_one(payload):
    payload["offers"] = [payload["offers"], None]
    assert assess(html(payload)).status == ListingStatus.UNKNOWN


def test_redirect_to_another_advert_cannot_borrow_offer(payload):
    evidence = assess(html(payload), final_url=URL.replace("123", "456"))
    assert evidence.status == ListingStatus.UNKNOWN
    assert evidence.reason == "target_url_unconfirmed"
