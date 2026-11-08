"""Status evidence is about the requested listing, not neighbouring page text."""

import json

import pytest

from home_finder.filters.availability import ListingStatus, assess_listing_page

URLS = {
    "rightmove": "https://www.rightmove.co.uk/properties/123",
    "zoopla": "https://www.zoopla.co.uk/to-rent/details/123/",
    "onthemarket": "https://www.onthemarket.com/details/123/",
    "openrent": "https://www.openrent.com/property-to-rent/london/flat/123",
}


def page(source, status="available", *, identifier=123, recommendations=None):
    record = {"id": identifier, "status": status, "description": "A room with a literal } brace"}
    if source == "rightmove":
        value = {"propertyData": record, "recommendations": recommendations}
        return f"<script>window.PAGE_MODEL = {json.dumps(value)};</script>"
    if source == "zoopla":
        value = {"props": {"pageProps": {"listing": record, "recommendations": recommendations}}}
    elif source == "onthemarket":
        value = {
            "props": {"initialReduxState": {"property": record, "recommendations": recommendations}}
        }
    else:
        availability = {"available": "InStock", "removed": "OutOfStock"}[status]
        value = {
            "@type": "Apartment",
            "url": URLS[source].replace("123", str(identifier)),
            "offers": {"@type": "Offer", "availability": f"https://schema.org/{availability}"},
        }
        return f'<script type="application/ld+json">{json.dumps(value)}</script>'
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(value)}</script>'


def assess(source, html, **kwargs):
    return assess_listing_page(
        source,
        html=html,
        status_code=kwargs.pop("status_code", 200),
        final_url=kwargs.pop("final_url", URLS[source]),
        requested_url=URLS[source],
        **kwargs,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("available", ListingStatus.ACTIVE),
        ("removed", ListingStatus.REMOVED),
        ("let agreed", ListingStatus.LET_AGREED),
    ],
)
def test_onthemarket_page_props_redux_state_sets_target_status(status, expected):
    value = {
        "props": {
            "pageProps": {
                "initialReduxState": {
                    "property": {"id": 123, "status": status},
                    "recommendations": [{"id": 456, "status": "removed"}],
                }
            }
        }
    }
    html = f'<script id="__NEXT_DATA__">{json.dumps(value)}</script>'
    assert assess("onthemarket", html).status == expected


@pytest.mark.parametrize(
    "state",
    [
        None,
        [],
        {},
        {"property": None},
        {"property": []},
        {"property": {}},
        {"property": {"id": 123, "images": [], "floorplans": []}},
        {"property": {"id": 456, "status": "removed"}},
        {"results": {"suggestedProperties": [{"id": 123, "status": "removed"}]}},
    ],
)
def test_unusable_onthemarket_page_props_state_cannot_fall_back_to_legacy_state(state):
    """A present ``pageProps`` envelope is authoritative even when it has no target record."""
    value = {
        "props": {
            "pageProps": {"initialReduxState": state},
            "initialReduxState": {"property": {"id": 123, "status": "removed"}},
        }
    }
    html = f'<script id="__NEXT_DATA__">{json.dumps(value)}</script>'
    assert assess("onthemarket", html).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize("source", URLS)
@pytest.mark.parametrize(
    "status,expected", [("available", ListingStatus.ACTIVE), ("removed", ListingStatus.REMOVED)]
)
def test_explicit_target_status_sets_active_or_removed(source, status, expected):
    result = assess(source, page(source, status))
    assert result.status == expected
    assert result.reason == "target_structured_status"


@pytest.mark.parametrize("source", ["rightmove", "zoopla", "onthemarket"])
@pytest.mark.parametrize("status", ["LET_AGREED", "Letting agreed", "under-offer"])
def test_let_agreed_spellings_set_let_agreed(source, status):
    assert assess(source, page(source, status)).status == ListingStatus.LET_AGREED


@pytest.mark.parametrize("source", URLS)
def test_recommendations_navigation_scripts_and_description_are_not_status(source):
    html = page(source, recommendations=[{"id": 456, "tags": ["LET_AGREED"]}])
    html += (
        '<nav>Homepage <a href="?includeLetAgreed=true">Let agreed</a></nav>'
        "<aside>This property has been removed</aside>"
        '<script>const other = "Let agreed";</script>'
        "<p>The previous tenant let agreed their next flat.</p>"
    )
    assert assess(source, html).status == ListingStatus.ACTIVE


@pytest.mark.parametrize("source", URLS)
@pytest.mark.parametrize(
    "body",
    [
        "",
        "<main>Nice flat</main>",
        "Let agreed",
        "<nav>Homepage</nav>",
        "This property is no longer available.",
    ],
)
def test_unbound_or_incomplete_page_is_unknown(source, body):
    assert assess(source, body).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize("source", URLS)
def test_other_listing_cannot_set_target_status(source):
    assert assess(source, page(source, "removed", identifier=456)).status == ListingStatus.UNKNOWN
    assert (
        assess(source, page(source), final_url=URLS[source].replace("123", "456")).status
        == ListingStatus.UNKNOWN
    )


@pytest.mark.parametrize("source", URLS)
@pytest.mark.parametrize("status_code", [404, 429, 403, 500])
def test_error_statuses_without_target_status_do_not_prove_removal(source, status_code):
    assert assess(source, "not found", status_code=status_code).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize("source", URLS)
@pytest.mark.parametrize(
    ("status", "expected", "reason"),
    [
        ("removed", ListingStatus.REMOVED, "target_structured_status"),
        ("available", ListingStatus.UNKNOWN, "http_404_unconfirmed"),
    ],
)
def test_404_counts_only_when_the_target_record_says_removed(source, status, expected, reason):
    result = assess(source, page(source, status), status_code=404)
    assert (result.status, result.reason) == (expected, reason)


@pytest.mark.parametrize("source", URLS)
def test_target_410_is_removed_unless_challenged_or_redirected(source):
    assert assess(source, "", status_code=410).status == ListingStatus.REMOVED
    result = assess(
        source, page(source, "removed"), status_code=410, headers={"CF-Mitigated": "Challenge"}
    )
    assert result.status == ListingStatus.UNKNOWN
    assert result.reason == "challenge"
    assert (
        assess(source, "", status_code=410, final_url="https://example.com/123").status
        == ListingStatus.UNKNOWN
    )


@pytest.mark.parametrize("source", URLS)
def test_generic_redirect_is_not_removal(source):
    result = assess(
        source,
        "Browse properties",
        final_url=URLS[source].split("/")[0] + "//" + URLS[source].split("/")[2] + "/search",
    )
    assert result.status == ListingStatus.UNKNOWN
    assert result.reason == "target_url_unconfirmed"


def test_primary_heading_requires_canonical_target_and_excludes_related_homes():
    canonical = f'<link rel="canonical" href="{URLS["openrent"]}">'
    assert (
        assess(
            "openrent", canonical + "<main><h1>This property is no longer available</h1></main>"
        ).status
        == ListingStatus.REMOVED
    )
    for wrapper in (
        "aside",
        'section class="similar-properties"',
        'div data-testid="recommendations"',
        'article data-property-id="456"',
        "section hidden",
    ):
        closing = wrapper.split()[0]
        html = f"{canonical}<main><{wrapper}><h1>Let agreed</h1></{closing}></main>"
        assert assess("openrent", html).status == ListingStatus.UNKNOWN
    assert assess("openrent", "<main><h1>Let agreed</h1></main>").status == ListingStatus.UNKNOWN


def test_malformed_mismatched_and_conflicting_primary_data():
    for record in (
        {"id": 123, "status": "available", "tags": ["LET_AGREED"]},
        {"id": 123, "propertyId": 456, "status": "removed"},
        {"status": "removed"},
        {"id": 123, "tags": "LET_AGREED"},
        {"id": 123, "letAgreed": "false"},
    ):
        html = f"<script>window.PAGE_MODEL = {json.dumps({'propertyData': record})}</script>"
        assert assess("rightmove", html).status == ListingStatus.UNKNOWN
    assert (
        assess("rightmove", '<script>window.PAGE_MODEL = {"propertyData":</script>').status
        == ListingStatus.UNKNOWN
    )
    assert (
        assess("rightmove", "<script>window.PAGE_MODEL = [];</script>").status
        == ListingStatus.UNKNOWN
    )


def test_rightmove_let_agreed_tag_or_flag_sets_let_agreed():
    for fields in ({"tags": ["LET_AGREED"]}, {"lettings": {"letAgreed": True}}):
        record = {"id": 123, **fields}
        html = f"<script>window.PAGE_MODEL = {json.dumps({'propertyData': record})}</script>"
        assert assess("rightmove", html).status == ListingStatus.LET_AGREED


def test_unrelated_linked_offer_cannot_remove_the_target():
    data = {
        "url": URLS["openrent"],
        "offers": {
            "url": URLS["openrent"].replace("123", "456"),
            "availability": "https://schema.org/OutOfStock",
        },
    }
    html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
    assert assess("openrent", html).status == ListingStatus.UNKNOWN


@pytest.mark.parametrize(
    ("source", "url", "payload"),
    [
        (
            "onthemarket",
            "https://www.onthemarket.com/details/123/",
            '<script id="__NEXT_DATA__">'
            '{"props":{"pageProps":{"initialReduxState":{"property":'
            '{"id":"123","propertyLabels":["Rental development"],'
            '"lettingDetails":{"items":["Availability date: 20 Nov 2030"]}}}}}}</script>',
        ),
        (
            "zoopla",
            "https://www.zoopla.co.uk/to-rent/details/123/",
            "<script>self.__next_f.push("
            + json.dumps(
                [1, 'a:["$","$L1",null,{"taxonomy":{"listingId":"123","listingStatus":"to_rent"}}]']
            )
            + ")</script><main>Available from 19 November 2030</main>",
        ),
        (
            "openrent",
            "https://www.openrent.co.uk/property-to-rent/london/flat/123",
            "<main><table><tr><td>Available From</td><td>20 November 2030</td></tr></table></main>",
        ),
    ],
)
def test_observed_move_in_dates_and_labels_are_not_explicit_status(source, url, payload):
    """Labels, move-in dates and Zoopla's streamed taxonomy say nothing about availability."""
    html = f'<link rel="canonical" href="{url}">' + payload
    evidence = assess_listing_page(
        source, html=html, status_code=200, requested_url=url, final_url=url
    )
    assert evidence.status == ListingStatus.UNKNOWN
    assert evidence.reason == "no_target_status"
