"""Owner-only, bounded delivery metadata without payloads or raw failures."""

import json

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from home_finder.config import Settings
from home_finder.web.app import create_app
from home_finder.web.deliveries import DELIVERY_STATES

PASSWORD = "fixture-owner-password-with-32-characters"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "delivery-view.db"),
        web_owner_password=PASSWORD,
        web_base_url="http://testserver",
    )
    with TestClient(create_app(settings, run_pipeline=False)) as client:
        yield client


async def seed(storage, merged, *, many=False):
    await storage.save_merged_property(merged)
    plan = json.dumps(
        {
            "parts": [
                {"method": "message", "payload": {"text": "private-caption"}},
                {"method": "photo", "payload": {"file": "private-media-path"}},
            ]
        }
    )
    states = ["queued"] * 101 + ["uncertain"] if many else list(DELIVERY_STATES)
    async with storage._transaction() as conn:
        for index, state in enumerate(states):
            await conn.execute(
                """INSERT INTO notification_deliveries(event_key,property_id,kind,state,reason,
                    recipient,resolution_ref,plan_json,next_part,receipts_json,retry_at)
                    VALUES (?,?,'discovery',?,?,'private-recipient','private-reference',?,?,?,?)""",
                (
                    f"discovery:fixture-{index:03}",
                    merged.unique_id,
                    state,
                    "rate_limited"
                    if state == "waiting"
                    else "FileNotFoundError"
                    if state == "blocked"
                    else "private-failure-body <script>secret</script>",
                    plan,
                    1 if state == "uncertain" else 0,
                    "[[101, 102]]" if state == "uncertain" else "[]",
                    "2026-10-15T12:00:00+00:00" if state == "waiting" else None,
                ),
            )


def test_owner_can_inspect_parts_and_safe_actions_without_changing_delivery(
    client, make_merged_property
):
    storage = client.app.state.storage
    merged = make_merged_property()
    client.portal.call(seed, storage, merged)
    before = client.portal.call(storage.deliveries.report)
    assert client.get("/deliveries").status_code == 401
    response = client.get("/deliveries", auth=("owner", PASSWORD))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    soup = BeautifulSoup(response.text, "html.parser")
    assert len(soup.select(".delivery-record")) == len(DELIVERY_STATES)
    assert "1 acknowledged part of 2; next part 2" in response.text
    assert "Part 1: 101, 102" in response.text
    assert "2026-10-15T12:00:00+00:00" in response.text
    assert "A planned media file is missing" in response.text
    assert "Telegram requested a delay" in response.text
    assert "Recorded failure; inspect the local delivery report" in response.text
    assert "--retry-confirmed-undelivered" in response.text
    assert "--message-ids" in response.text
    assert "--evidence-ref" in response.text
    assert "Automatic retries for uncertain parts remain disabled" in response.text
    for private in (
        "private-caption",
        "private-media-path",
        "private-recipient",
        "private-reference",
        "private-failure-body",
    ):
        assert private not in response.text
    for form in soup.find_all("form"):
        assert form.get("method", "get").lower() == "get"
    assert client.portal.call(storage.deliveries.report) == before


def test_state_filter_can_reach_alerts_beyond_the_bounded_first_page(client, make_merged_property):
    from functools import partial

    storage = client.app.state.storage
    client.portal.call(partial(seed, storage, make_merged_property(), many=True))
    first = client.get("/deliveries", auth=("owner", PASSWORD))
    assert len(BeautifulSoup(first.text, "html.parser").select(".delivery-record")) == 100
    response = client.get("/deliveries?state=uncertain", auth=("owner", PASSWORD))
    soup = BeautifulSoup(response.text, "html.parser")
    assert len(soup.select(".delivery-record")) == 1
    assert "discovery:fixture-101" in response.text
    assert soup.select_one("#delivery-state option[selected]")["value"] == "uncertain"
    assert "Queued (101)" in response.text
    assert client.get("/deliveries?state=", auth=("owner", PASSWORD)).status_code == 200
    assert client.get("/deliveries?state=bad", auth=("owner", PASSWORD)).status_code == 422


def test_empty_delivery_view_is_reachable_from_dashboard(client):
    dashboard = client.get("/", auth=("owner", PASSWORD))
    assert BeautifulSoup(dashboard.text, "html.parser").select_one('a[href="/deliveries"]')
    response = client.get("/deliveries", auth=("owner", PASSWORD))
    assert response.status_code == 200
    assert "No unfinished alerts" in response.text
