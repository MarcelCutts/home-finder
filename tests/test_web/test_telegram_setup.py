"""Real webhook startup and callback acknowledgement with only the Bot API replaced."""

from unittest.mock import AsyncMock, Mock

import aiogram
import pytest
import structlog.testing
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.web.app import create_app
from home_finder.web.telegram_webhook import _answer_callback


@pytest.fixture
def bot(monkeypatch):
    fake = Mock(
        set_webhook=AsyncMock(),
        answer_callback_query=AsyncMock(),
        session=Mock(close=AsyncMock()),
    )
    monkeypatch.setattr(aiogram, "Bot", Mock(return_value=fake))
    return fake


@pytest.mark.parametrize("failed", [False, True], ids=["acknowledged", "provider-error"])
async def test_startup_registers_canonical_webhook_and_closes_bot_session(bot, tmp_path, failed):
    settings = Settings(
        database_path=str(tmp_path / "web.db"),
        telegram_bot_token=SecretStr("123456:fixture"),
        web_base_url="https://homes.example/",
        telegram_webhook_secret="fixture-webhook-secret",
    )
    if failed:
        bot.set_webhook.side_effect = RuntimeError("Provider unavailable")
    app = create_app(settings)
    with structlog.testing.capture_logs() as logs:
        async with (
            app.router.lifespan_context(app),
            app.state.storage._read_connection() as conn,
        ):
            row = await (await conn.execute("SELECT COUNT(*) FROM properties")).fetchone()
    bot.set_webhook.assert_awaited_once_with(
        url="https://homes.example/telegram/webhook", secret_token="fixture-webhook-secret"
    )
    bot.session.close.assert_awaited_once()
    assert row[0] == 0
    events = {record["event"] for record in logs}
    assert (
        "telegram_webhook_registration_failed" if failed else "telegram_webhook_registered"
    ) in events


@pytest.mark.parametrize("failed", [False, True], ids=["acknowledged", "provider-error"])
async def test_callback_acknowledgement_closes_session_even_when_send_fails(bot, failed):
    settings = Settings(telegram_bot_token=SecretStr("123456:fixture"))
    if failed:
        bot.answer_callback_query.side_effect = RuntimeError("Provider unavailable")
    with structlog.testing.capture_logs() as logs:
        await _answer_callback(settings, "callback-17", "Interested")
    bot.answer_callback_query.assert_awaited_once_with("callback-17", text="Interested")
    bot.session.close.assert_awaited_once()
    assert any(record["event"] == "answer_callback_failed" for record in logs) is failed
