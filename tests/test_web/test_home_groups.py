"""Current grouping and original saved-state actions remain distinct in the UI."""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.models import UserStatus
from home_finder.web.filters import PropertyFilter
from tests.test_db.test_membership import REVIEW
from tests.test_web import test_home_links as link_fixtures
from tests.test_web.test_home_links import callback

client = link_fixtures.client
storage = link_fixtures.storage


@pytest.fixture
async def grouped(storage, sample_property):
    first = sample_property.model_copy(
        update={"image_url": HttpUrl("https://example.test/fixture.jpg")}
    )
    second = first.model_copy(update={"source_id": "second", "title": "Second saved record"})
    await storage.save_property(first)
    await storage.save_property(second)
    a = await storage.homes.resolve(first.unique_id)
    b = await storage.homes.resolve(second.unique_id)
    await storage.update_user_status(a.home_id, UserStatus.INTERESTED, note="First note")
    await storage.update_user_status(b.home_id, UserStatus.ARCHIVED, note="Second note")
    await storage.memberships.apply(
        await storage.memberships.prepare_merge(a.home_id, (b.home_id,), **REVIEW),
        allow_incomplete_projections=True,
    )
    return first, second, a, b


async def test_group_cards_counts_and_status_filters_keep_conflicts_visible(
    storage, client, grouped
):
    _, _, a, _ = grouped
    cards, count = await storage.web.get_properties_paginated(PropertyFilter())
    assert count == len(cards) == 1
    assert cards[0]["status_conflict"]
    assert cards[0]["group_record_count"] == 2
    assert await storage.web.get_property_count() == 1
    for status in ("interested", "archived"):
        filters = PropertyFilter(status=status)
        assert await storage.web.get_filter_count(filters) == 1
        assert (await storage.web.get_properties_paginated(filters))[1] == 1
    assert await storage.web.get_filter_count(PropertyFilter(status="new")) == 0
    page = client.get(f"/property/{a.home_id}/card")
    assert page.status_code == 200
    assert "Mixed saved statuses" in page.text
    assert "card-quick-actions" not in page.text


async def test_old_detail_link_selects_original_status_and_history(client, storage, grouped):
    first, second, a, b = grouped
    assert await storage.homes.resolve(b.home_id) == a
    assert await storage.web.get_property_detail(b.home_id) is not None
    page = client.get(f"/property/{b.home_id}")
    assert page.status_code == 200
    prop = page.context["prop"]
    assert prop["home_id"] == a.home_id
    assert prop["unique_id"] == first.unique_id
    assert prop["action_home_id"] == b.home_id
    assert page.context["user_status"] == "archived"
    assert page.context["status_history"][0]["note"] == "Second note"
    assert f'hx-patch="/property/{b.home_id}/status"' in page.text
    assert "Saved records in this group" in page.text
    assert "Second saved record" in page.text
    response = client.patch(f"/property/{b.home_id}/status", data={"status": "interested"})
    assert response.status_code == 200
    assert len(await storage.get_status_history(second.unique_id)) == 2
    assert len(await storage.get_status_history(first.unique_id)) == 1


async def test_ambiguous_personal_action_is_a_conflict_not_an_anchor_edit(client, storage, grouped):
    _, _, a, b = grouped
    async with storage._transaction() as conn:
        await conn.executemany(
            "INSERT INTO home_aliases VALUES ('fixture:ambiguous', ?, 'legacy', 'old')",
            [(a.home_id,), (b.home_id,)],
        )
    page = client.get("/property/fixture:ambiguous")
    assert page.status_code == 200
    assert "multiple saved records" in page.text
    assert "hx-patch=" not in page.text
    for headers in ({}, {"HX-Request": "true"}):
        response = client.patch(
            "/property/fixture:ambiguous/status", data={"status": "new"}, headers=headers
        )
        assert response.status_code == 409
    with patch(
        "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
    ) as answer:
        response = client.post(
            "/telegram/webhook",
            json=callback("fixture:ambiguous"),
            headers={"x-telegram-bot-api-secret-token": "test-secret"},
        )
        assert response.status_code == 200
        assert "specific saved record" in answer.call_args.args[-1]
    assert len(await storage.get_status_history(a.home_id)) == 1
    assert len(await storage.get_status_history(b.home_id)) == 1


async def test_old_telegram_buttons_edit_original_record_and_keep_update_replay(
    client, storage, grouped
):
    _, second, a, b = grouped
    headers = {"x-telegram-bot-api-secret-token": "test-secret"}
    with patch("home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock):
        assert (
            client.post("/telegram/webhook", json=callback(b.home_id), headers=headers).status_code
            == 200
        )
        await storage.update_user_status(b.home_id, UserStatus.ARCHIVED)
        client.post("/telegram/webhook", json=callback(second.unique_id), headers=headers)
    assert len(await storage.get_status_history(b.home_id)) == 3
    assert len(await storage.get_status_history(a.home_id)) == 1
    page = await storage.web.get_property_detail(second.unique_id)
    assert page["user_status"] == "archived"
