"""Tests for Telegram webhook callback handler."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import MergedProperty, Property, PropertySource, UserStatus
from home_finder.web.telegram_webhook import router


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=123,
        telegram_webhook_secret="test-secret-123",
        search_areas="e8",
        database_path=":memory:",
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def app(storage: PropertyStorage, settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = settings
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def prop_a() -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=HttpUrl("https://openrent.com/12345"),
        title="1 bed in E8",
        price_pcm=1900,
        bedrooms=1,
        address="10 Mare Street",
        postcode="E8 3RH",
    )


@pytest.fixture
def merged_a(prop_a: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_a,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
    )


def _callback_update(callback_data: str, callback_id: str = "cb-123") -> dict:
    """Build a minimal Telegram Update with a callback_query."""
    return {
        "update_id": 1,
        "callback_query": {
            "id": callback_id,
            "data": callback_data,
            "from": {"id": 123, "is_bot": False, "first_name": "Marcel"},
            "message": {"chat": {"id": 123}},
        },
    }


VALID_HEADERS = {"x-telegram-bot-api-secret-token": "test-secret-123"}


class TestWebhookSecurity:
    def test_rejects_missing_secret(self, client: TestClient) -> None:
        resp = client.post("/telegram/webhook", json={"update_id": 1})
        assert resp.status_code == 403

    def test_rejects_wrong_secret(self, client: TestClient) -> None:
        resp = client.post(
            "/telegram/webhook",
            json={"update_id": 1},
            headers={"x-telegram-bot-api-secret-token": "wrong"},
        )
        assert resp.status_code == 403

    def test_accepts_correct_secret(self, client: TestClient) -> None:
        resp = client.post(
            "/telegram/webhook",
            json={"update_id": 1},
            headers=VALID_HEADERS,
        )
        assert resp.status_code == 200

    def test_returns_404_when_webhook_not_configured(self, storage: PropertyStorage) -> None:
        """When telegram_webhook_secret is empty, endpoint returns 404."""
        no_secret_settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            telegram_webhook_secret="",
            search_areas="e8",
            database_path=":memory:",
        )
        app = FastAPI()
        app.state.storage = storage
        app.state.settings = no_secret_settings
        app.include_router(router)
        c = TestClient(app)
        resp = c.post("/telegram/webhook", json={"update_id": 1}, headers=VALID_HEADERS)
        assert resp.status_code == 404


class TestCallbackHandler:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("sender", 999),
            ("chat", 999),
            ("sender", "123"),
            ("chat", True),
            ("sender", None),
            ("chat", None),
        ],
    )
    async def test_wrong_owner_or_chat_has_no_effect(
        self,
        client,
        storage,
        merged_a,
        field,
        value,
    ):
        await storage.save_merged_property(merged_a)
        update = _callback_update(f"st:{merged_a.unique_id}:interested")
        if field == "sender":
            update["callback_query"]["from"]["id"] = value
        else:
            update["callback_query"]["message"]["chat"]["id"] = value
        with patch(
            "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
        ) as answer:
            assert (
                client.post("/telegram/webhook", json=update, headers=VALID_HEADERS).status_code
                == 200
            )
            answer.assert_not_called()
        assert await storage.get_status_history(merged_a.unique_id) == []

    async def test_group_requires_explicit_owner(self, client, storage, settings, merged_a):
        await storage.save_merged_property(merged_a)
        settings.telegram_chat_id = -456
        update = _callback_update(f"st:{merged_a.unique_id}:interested")
        update["callback_query"]["message"]["chat"]["id"] = -456
        with patch(
            "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
        ) as answer:
            assert (
                client.post("/telegram/webhook", json=update, headers=VALID_HEADERS).status_code
                == 200
            )
            answer.assert_not_called()
            assert await storage.get_status_history(merged_a.unique_id) == []
            settings.telegram_owner_user_id = 123
            assert (
                client.post("/telegram/webhook", json=update, headers=VALID_HEADERS).status_code
                == 200
            )
        assert len(await storage.get_status_history(merged_a.unique_id)) == 1

    async def test_replay_cannot_undo_later_owner_decision(self, client, storage, merged_a):
        await storage.save_merged_property(merged_a)
        update = _callback_update(f"st:{merged_a.unique_id}:interested")
        with patch(
            "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
        ) as answer:
            assert (
                client.post("/telegram/webhook", json=update, headers=VALID_HEADERS).status_code
                == 200
            )
            await storage.update_user_status(merged_a.unique_id, UserStatus.ARCHIVED)
            assert (
                client.post("/telegram/webhook", json=update, headers=VALID_HEADERS).status_code
                == 200
            )
            answer.assert_awaited_once()
        history = await storage.get_status_history(merged_a.unique_id)
        assert [event["to_status"] for event in history] == ["interested", "archived"]

    @pytest.mark.parametrize("value", [None, True, "1", -1, 2**63])
    async def test_invalid_update_id_has_no_effect(self, client, storage, merged_a, value):
        await storage.save_merged_property(merged_a)
        update = _callback_update(f"st:{merged_a.unique_id}:interested")
        update["update_id"] = value
        with patch(
            "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
        ) as answer:
            assert (
                client.post("/telegram/webhook", json=update, headers=VALID_HEADERS).status_code
                == 200
            )
            answer.assert_not_called()
        assert await storage.get_status_history(merged_a.unique_id) == []

    @pytest.mark.parametrize("value", [None, [], 1, "x" * 65, "st:../path:interested", "\ud800"])
    async def test_invalid_callback_data_has_no_effect(self, client, storage, merged_a, value):
        import json

        await storage.save_merged_property(merged_a)
        update = _callback_update(value)
        with patch("home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock):
            assert (
                client.post(
                    "/telegram/webhook",
                    content=json.dumps(update),
                    headers=VALID_HEADERS,
                ).status_code
                == 200
            )
        assert await storage.get_status_history(merged_a.unique_id) == []

    @pytest.mark.parametrize("body", ["{broken", "[]", '"string"', "null", "[" * 2000])
    def test_malformed_json_is_rejected(self, client, body):
        assert (
            client.post(
                "/telegram/webhook",
                content=body,
                headers=VALID_HEADERS,
            ).status_code
            == 400
        )

    @pytest.mark.asyncio
    async def test_interested_updates_status(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)

        with patch("home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock):
            resp = client.post(
                "/telegram/webhook",
                json=_callback_update(f"st:{merged_a.unique_id}:interested"),
                headers=VALID_HEADERS,
            )

        assert resp.status_code == 200

        # Verify DB was updated
        conn = await storage._get_connection()
        row = await conn.execute_fetchall(
            "SELECT user_status FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        assert row[0][0] == "interested"

    @pytest.mark.asyncio
    async def test_archived_updates_status(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)

        with patch("home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock):
            resp = client.post(
                "/telegram/webhook",
                json=_callback_update(f"st:{merged_a.unique_id}:archived"),
                headers=VALID_HEADERS,
            )

        assert resp.status_code == 200

        conn = await storage._get_connection()
        row = await conn.execute_fetchall(
            "SELECT user_status FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        assert row[0][0] == "archived"

    @pytest.mark.asyncio
    async def test_unknown_property_returns_ok(self, client: TestClient) -> None:
        """Unknown property still returns 200 (Telegram expects it) but answers with error."""
        with patch(
            "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
        ) as mock_answer:
            resp = client.post(
                "/telegram/webhook",
                json=_callback_update("st:nonexistent:99999:interested"),
                headers=VALID_HEADERS,
            )

        assert resp.status_code == 200
        mock_answer.assert_called_once()
        assert "not found" in mock_answer.call_args[0][2].lower()

    @pytest.mark.asyncio
    async def test_unknown_status_returns_ok(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)

        with patch(
            "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
        ) as mock_answer:
            resp = client.post(
                "/telegram/webhook",
                json=_callback_update(f"st:{merged_a.unique_id}:bogus"),
                headers=VALID_HEADERS,
            )

        assert resp.status_code == 200
        mock_answer.assert_called_once()
        assert "unknown status" in mock_answer.call_args[0][2].lower()

    def test_non_callback_update_ignored(self, client: TestClient) -> None:
        """Regular message updates are acknowledged but ignored."""
        resp = client.post(
            "/telegram/webhook",
            json={"update_id": 1, "message": {"text": "hello"}},
            headers=VALID_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_status_event_recorded(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """Status change via Telegram records source='telegram' in events."""
        await storage.save_merged_property(merged_a)

        with patch("home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock):
            client.post(
                "/telegram/webhook",
                json=_callback_update(f"st:{merged_a.unique_id}:interested"),
                headers=VALID_HEADERS,
            )

        history = await storage.get_status_history(merged_a.unique_id)
        assert len(history) == 1
        assert history[0]["to_status"] == "interested"
        assert history[0]["source"] == "telegram"
