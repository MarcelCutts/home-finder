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
from home_finder.models import MergedProperty, Property, PropertySource
from home_finder.web.telegram_webhook import router


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
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
