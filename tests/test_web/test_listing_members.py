"""All reviewed advertisements remain visible, with their own claims and links."""

from html import escape
from unittest.mock import AsyncMock, patch

from home_finder.models import PropertySource
from tests.test_db.test_membership import REVIEW
from tests.test_web import test_home_links as link_fixtures

client = link_fixtures.client
storage = link_fixtures.storage


async def test_same_portal_links_text_and_conflicting_cost_claims_survive_split(
    client, storage, make_property
):
    props = [
        make_property(
            source=PropertySource.RIGHTMOVE,
            source_id=str(identifier),
            url=f"https://www.rightmove.co.uk/properties/{identifier}",
            description=description,
            image_url="https://fixture.test/gallery.jpg",
        )
        for identifier, description in (
            (100001, "First description: electricity included\n<script>alert(1)</script>"),
            (100002, "Second description: electricity excluded"),
        )
    ]
    for prop in props:
        await storage.save_property(prop)
    targets = [await storage.homes.resolve(prop.unique_id) for prop in props]
    await storage.memberships.apply(
        await storage.memberships.prepare_merge(
            targets[0].home_id, (targets[1].home_id,), **REVIEW
        ),
        allow_incomplete_projections=True,
    )
    page = client.get(f"/property/{targets[0].home_id}")
    assert page.status_code == 200
    assert all(
        str(prop.url) in page.text and escape(prop.description) in page.text for prop in props
    )
    assert "Rightmove 1" in page.text and "Rightmove 2" in page.text
    assert "&lt;br&gt;" not in page.text
    assert "<script>alert(1)</script>" not in page.text
    electricity = next(
        item
        for item in page.context["cost_breakdown"]["line_items"]
        if item["label"] == "Electricity"
    )
    assert electricity["state"] == "conflicting"
    assert any("Rightmove 1" in text for text in electricity["evidence"])
    assert any("Rightmove 2" in text for text in electricity["evidence"])
    await storage.memberships.apply(
        await storage.memberships.prepare_split(
            targets[0].home_id, {props[1].unique_id: targets[1].home_id}, **REVIEW
        ),
        allow_incomplete_projections=True,
    )
    page = client.get(f"/property/{targets[0].home_id}")
    assert escape(props[0].description) in page.text
    assert props[1].description not in page.text
    assert str(props[1].url) not in page.text

    # An explicitly empty current membership cannot revive old portal caches.
    stale = await storage.web.get_property_detail(targets[0].home_id)
    stale.update(listing_members=(), member_links=[])
    with patch.object(storage.web, "get_property_detail", AsyncMock(return_value=stale)):
        page = client.get(f"/property/{targets[0].home_id}")
    assert page.context["best_description"] == ""
    assert all(str(prop.url) not in page.text for prop in props)
    electricity = next(
        item
        for item in page.context["cost_breakdown"]["line_items"]
        if item["label"] == "Electricity"
    )
    assert electricity["evidence"] == []
