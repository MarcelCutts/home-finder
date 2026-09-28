"""Access checks against the complete application, including mounted assets."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.web.access import MAX_BODY_BYTES
from home_finder.web.app import create_app

PASSWORD = "local-test-owner-password-32-characters"


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        database_path=str(tmp_path / "properties.db"),
        web_owner_password=PASSWORD,
        web_base_url="http://testserver",
    )
    with TestClient(create_app(settings, run_pipeline=False)) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/?area=E8",
        "/count",
        "/property/openrent_123",
        "/images/id/image.jpg",
        "/area/E8",
        "/static/style.css",
        "/openapi.json",
        "/diagnostics",
    ],
)
def test_anonymous_reads_are_rejected(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("password", ["", "short", "秘密" * 12])
def test_unconfigured_access_fails_closed(tmp_path: Path, password: str) -> None:
    settings = Settings(database_path=str(tmp_path / "properties.db"), web_owner_password=password)
    with TestClient(create_app(settings, run_pipeline=False)) as client:
        assert client.get("/").status_code == 503
        assert client.get("/health").json() == {"status": "ok"}
    settings.web_owner_password = SecretStr(PASSWORD)
    with TestClient(create_app(settings, run_pipeline=False)) as recovered:
        assert recovered.get("/", auth=("owner", PASSWORD)).status_code == 200


def test_health_does_not_expose_pipeline_state(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_owner_can_read_the_dashboard(client: TestClient) -> None:
    response = client.get("/", auth=("owner", PASSWORD))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_cross_site_mutation_is_rejected(client: TestClient) -> None:
    response = client.patch(
        "/property/openrent_123/status",
        auth=("owner", PASSWORD),
        headers={"Origin": "https://attacker.example"},
        data={"status": "interested"},
    )
    assert response.status_code == 403


@pytest.fixture
def property_id(client, make_merged_property, make_quality_analysis):
    merged = make_merged_property()
    storage = client.app.state.storage
    client.portal.call(storage.save_merged_property, merged)
    client.portal.call(storage.save_quality_analysis, merged.unique_id, make_quality_analysis())
    return merged.unique_id


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null", "Referer": "http://testserver/"},
        {"Origin": "http://testserver.attacker.example"},
        {"Origin": "https://testserver"},
        {"Origin": "http://testserver:8000"},
        {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "https://attacker.example", "X-Forwarded-Host": "attacker.example"},
    ],
)
def test_invalid_origin_cannot_change_status(client, property_id, headers):
    response = client.patch(
        f"/property/{property_id}/status",
        auth=("owner", PASSWORD),
        headers=headers,
        data={"status": "interested"},
    )
    assert response.status_code == 403
    assert client.portal.call(client.app.state.storage.get_status_history, property_id) == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://testserver"},
        {"Origin": "http://testserver:80"},
        {"Referer": "http://testserver/property/example"},
    ],
)
def test_owner_can_change_status_through_htmx(client, property_id, headers):
    response = client.patch(
        f"/property/{property_id}/status",
        auth=("owner", PASSWORD),
        headers={**headers, "HX-Request": "true"},
        data={"status": "interested"},
    )
    assert response.status_code == 200
    assert "Interested" in response.text
    assert "nonce-" in response.headers["content-security-policy"]
    history = client.portal.call(client.app.state.storage.get_status_history, property_id)
    assert [event["to_status"] for event in history] == ["interested"]


@pytest.mark.parametrize("authorization", ["", "Basic broken", "Bearer fake"])
def test_invalid_credentials_cannot_mutate(client, property_id, authorization):
    response = client.patch(
        f"/property/{property_id}/status",
        data={"status": "interested"},
        headers={"Authorization": authorization, "Origin": "http://testserver"},
    )
    assert response.status_code == 401
    assert client.portal.call(client.app.state.storage.get_status_history, property_id) == []


def test_cached_images_require_owner_and_use_private_cache(client):
    from home_finder.utils.image_cache import get_cache_dir, save_image_bytes

    cache_dir = get_cache_dir(client.app.state.settings.data_dir, "openrent:123")
    save_image_bytes(cache_dir / "image.jpg", b"test-image")
    path = "/images/openrent_123/image.jpg"
    assert client.get(path).status_code == 401
    response = client.get(path, auth=("owner", PASSWORD))
    assert response.status_code == 200
    assert response.content == b"test-image"
    assert response.headers["cache-control"].startswith("private,")


def test_owner_diagnostics_remain_available(client):
    response = client.get("/diagnostics", auth=("owner", PASSWORD))
    assert response.status_code == 200
    assert response.json()["pipeline_running"] is False


def test_failed_authentication_is_rate_limited_and_recovers(client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("home_finder.web.access.monotonic", lambda: now[0])
    for _ in range(10):
        assert client.get("/", auth=("owner", "wrong")).status_code == 401
    response = client.get("/", auth=("owner", PASSWORD))
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert client.get("/health").status_code == 200
    now[0] += 61
    assert client.get("/", auth=("owner", PASSWORD)).status_code == 200


def test_mutations_are_rate_limited_and_coalesce(client, property_id, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("home_finder.web.access.monotonic", lambda: now[0])
    for _ in range(30):
        response = client.post(
            f"/property/{property_id}/reanalyze",
            auth=("owner", PASSWORD),
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200
    response = client.post(
        f"/property/{property_id}/reanalyze",
        auth=("owner", PASSWORD),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    now[0] += 61
    assert (
        client.post(
            f"/property/{property_id}/reanalyze",
            auth=("owner", PASSWORD),
            headers={"Origin": "http://testserver"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize("length", [None, "1", str(MAX_BODY_BYTES + 1), "9" * 5000])
def test_oversized_requests_rejected_before_form_parsing(client, property_id, length):
    headers = {"Origin": "http://testserver", "Content-Type": "application/x-www-form-urlencoded"}
    if length is not None:
        headers["Content-Length"] = length
    response = client.patch(
        f"/property/{property_id}/status",
        auth=("owner", PASSWORD),
        headers=headers,
        content=iter([b"status=interested&ignored=", b"x" * MAX_BODY_BYTES]),
    )
    assert response.status_code == 413
    assert client.portal.call(client.app.state.storage.get_status_history, property_id) == []


def test_concurrent_owner_mutations_commit_separate_events(client, property_id):
    def change_status(_):
        return client.patch(
            f"/property/{property_id}/status",
            auth=("owner", PASSWORD),
            headers={"Origin": "http://testserver"},
            data={"status": "interested"},
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(change_status, range(4))) == [200] * 4
    assert len(client.portal.call(client.app.state.storage.get_status_history, property_id)) == 4


def test_webhook_needs_secret_in_full_app_even_with_owner_credentials(client):
    client.app.state.settings.telegram_webhook_secret = "test-secret"
    with patch(
        "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
    ) as answer:
        assert (
            client.post(
                "/telegram/webhook",
                auth=("owner", PASSWORD),
                json={"update_id": 1},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
                json={"update_id": 1},
            ).status_code
            == 200
        )
        answer.assert_not_called()


def test_concurrent_callback_retries_commit_once(client, property_id):
    settings = client.app.state.settings
    settings.telegram_webhook_secret = "test-secret"
    settings.telegram_chat_id = 123
    update = {
        "update_id": 1,
        "callback_query": {
            "id": "cb-123",
            "data": f"st:{property_id}:interested",
            "from": {"id": 123},
            "message": {"chat": {"id": 123}},
        },
    }

    def callback(_):
        return client.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        ).status_code

    with patch(
        "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
    ) as answer:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(callback, range(4))) == [200] * 4
        answer.assert_awaited_once()
    history = client.portal.call(client.app.state.storage.get_status_history, property_id)
    assert [event["to_status"] for event in history] == ["interested"]


def test_canonical_origin_works_behind_https_proxy(client, property_id):
    client.app.state.settings.web_base_url = "https://owner.example"
    response = client.patch(
        f"/property/{property_id}/status",
        auth=("owner", PASSWORD),
        headers={"Origin": "https://owner.example"},
        data={"status": "interested"},
    )
    assert response.status_code == 200
