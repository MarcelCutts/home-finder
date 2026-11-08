"""Rightmove's indexed ``window.__PAGE_MODEL`` keeps the plain page model's status rules.

The indexed form stores values in one table, and containers refer to entries by
position. Publication flags are not availability. An indexed model that cannot
be decoded, or that names another listing, leaves the page unknown whatever
else the page says.
"""

import json
from pathlib import Path

import pytest

from home_finder.filters.availability import ListingStatus, assess_listing_page

URL = "https://www.rightmove.co.uk/properties/123"


@pytest.fixture
def table():
    """Synthetic reduction of the observed indexed propertyData/status/tags shape."""
    return [
        {"propertyData": 1, "recommendations": 7},
        {"id": 2, "status": 3, "tags": 6},
        "123",
        {"published": 4, "archived": 5},
        True,
        False,
        [],
        [],
    ]


def indexed_html(table, **envelope):
    payload = {"data": json.dumps(table), "encoding": "on", **envelope}
    return f"<script>window.__PAGE_MODEL = {json.dumps(payload)};</script>"


def assess(html, url=URL):
    return assess_listing_page(
        "rightmove", html=html, status_code=200, requested_url=url, final_url=url
    )


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("LET_AGREED", ListingStatus.LET_AGREED),
        ("removed", ListingStatus.REMOVED),
        ("available", ListingStatus.ACTIVE),
    ],
)
def test_indexed_model_tag_sets_target_status(table, word, expected):
    table[6] = [8]
    table.append(word)
    evidence = assess(indexed_html(table))
    assert evidence.status == expected
    assert evidence.reason == "target_structured_status"


def test_sanitised_detail_fixture_has_no_target_status(fixtures_path: Path):
    """The observed page's status record holds only publication flags."""
    html = (fixtures_path / "rightmove_indexed_detail.html").read_text()
    evidence = assess(html, url=URL.replace("123", "100001"))
    assert evidence.status == ListingStatus.UNKNOWN
    assert evidence.reason == "no_target_status"


@pytest.mark.parametrize(("published", "archived"), [(True, False), (False, True), (None, None)])
def test_publication_flags_do_not_invent_lifecycle_status(table, published, archived):
    table[4], table[5] = published, archived
    evidence = assess(indexed_html(table))
    assert evidence.status == ListingStatus.UNKNOWN
    assert evidence.reason == "no_target_status"


def test_suggestion_cannot_establish_status_even_with_the_target_id(table):
    table[7] = [8]
    table.extend([{"id": 2, "tags": 9}, [10], "removed"])
    evidence = assess(indexed_html(table))
    assert evidence.status == ListingStatus.UNKNOWN
    assert evidence.reason == "no_target_status"


def test_mismatched_indexed_model_cannot_borrow_plain_model_status(table):
    table[2] = "456"
    table[6] = [8]
    table.append("removed")
    plain = '<script>window.PAGE_MODEL = {"propertyData":{"id":"123","status":"removed"}}</script>'
    evidence = assess(indexed_html(table) + plain)
    assert evidence.status == ListingStatus.UNKNOWN
    assert evidence.reason == "conflicting_status"


@pytest.mark.parametrize(
    "html",
    [
        "<script>window.__PAGE_MODEL = {broken</script>",
        indexed_html([{"propertyData": 1}, {"id": 2}, "123"], encoding="off"),
        indexed_html([{"propertyData": 1}, {"id": 2}, "123"], data=[]),
        indexed_html([{"propertyData": 1}, {"id": 2, "back": 0}, "123"]),
        indexed_html([{"propertyData": 1}, {"tags": 2}, [3], "removed"]),
    ],
)
def test_malformed_indexed_model_cannot_borrow_a_matching_heading(html):
    fallback = f'<link rel="canonical" href="{URL}"><main><h1>Let agreed</h1></main>'
    assert assess(fallback).status == ListingStatus.LET_AGREED
    assert assess(html + fallback).status == ListingStatus.UNKNOWN


def test_conflicting_indexed_models_remain_unknown(table):
    table[6] = [8]
    table.append("LET_AGREED")
    first = indexed_html(table)
    table[8] = "available"
    assert assess(first + indexed_html(table)).status == ListingStatus.UNKNOWN


def test_primary_identity_aliases_must_all_match(table):
    table[1]["listingId"] = 8
    table[6] = [9]
    table.extend(["456", "removed"])
    assert assess(indexed_html(table)).status == ListingStatus.UNKNOWN
