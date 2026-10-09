"""Short and legacy home links preserve status, evidence and callback authorization."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import PropertyImage, UserStatus
from home_finder.models.home_identity import HomeTarget
from home_finder.notifiers.telegram import TelegramNotifier, _build_inline_keyboard
from home_finder.utils.image_cache import (
    get_cache_dir,
    safe_dir_name,
    save_image_bytes,
    url_to_filename,
)
from home_finder.web.routes import router
from home_finder.web.telegram_webhook import router as webhook_router


@pytest.fixture
async def storage(tmp_path):
    async with PropertyStorage(str(tmp_path / "links.db")) as db:
        yield db


@pytest.fixture
def client(storage, tmp_path):
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = Settings(
        _env_file=None,
        telegram_chat_id=123,
        telegram_webhook_secret="test-secret",
        database_path=str(tmp_path / "links.db"),
        web_base_url="https://owner.example",
    )
    app.include_router(router)
    app.include_router(webhook_router)
    return TestClient(app)


def callback(identifier, *, update_id=1, owner=123):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb-test",
            "data": f"st:{identifier}:interested",
            "from": {"id": owner},
            "message": {"chat": {"id": 123}},
        },
    }


async def test_stable_detail_uses_original_images_price_and_status_history(
    storage, client, sample_property, tmp_path
):
    await storage.save_property(sample_property)
    await storage.update_user_status(
        sample_property.unique_id, UserStatus.INTERESTED, note="Saved note"
    )
    await storage.detect_and_record_price_change(
        sample_property.unique_id, sample_property.price_pcm - 100
    )
    image = PropertyImage(
        url=HttpUrl("https://example.test/gallery.jpg"),
        source=sample_property.source,
        image_type="gallery",
    )
    await storage.save_property_images(sample_property.unique_id, [image])
    cached = get_cache_dir(str(tmp_path), sample_property.unique_id) / url_to_filename(
        str(image.url), "gallery", 0
    )
    save_image_bytes(cached, b"fixture")
    target = await storage.homes.resolve(sample_property.unique_id)
    assert target
    result = client.get(f"/property/{target.home_id}")
    assert result.status_code == 200
    assert result.context["status_history"][0]["note"] == "Saved note"
    assert result.context["price_history"][0]["new_price"] == sample_property.price_pcm - 100
    assert f"/images/{safe_dir_name(sample_property.unique_id)}/{cached.name}" in result.text
    assert client.get(f"/property/{sample_property.unique_id}").status_code == 200
    assert client.get(f"/property/{target.home_id}/card").status_code == 200


@pytest.mark.parametrize(
    "source_id", ["1" * 150, "https://rightmove.co.uk/properties/123?legacy=1"]
)
async def test_long_raw_ids_receive_short_callback_and_working_detail_links(
    storage, client, sample_property, source_id
):
    prop = sample_property.model_copy(
        update={"source_id": source_id, "image_url": HttpUrl("https://example.test/gallery.jpg")}
    )
    await storage.save_property(prop)
    target = await storage.homes.resolve(prop.unique_id)
    assert target
    bot = AsyncMock()
    notifier = TelegramNotifier(
        bot_token="fake:token",
        chat_id=123,
        web_base_url="https://owner.example",
        home_resolver=storage.homes.resolve,
    )
    with patch.object(notifier, "_get_bot", return_value=bot):
        assert await notifier.send_property_notification(prop)
    keyboard = bot.send_message.call_args.kwargs["reply_markup"]
    buttons = [b for row in keyboard.inline_keyboard for b in row]
    status_buttons = [b for b in buttons if b.callback_data]
    assert len(status_buttons) == 2
    assert all(target.home_id in b.callback_data for b in status_buttons)
    assert all(len(b.callback_data.encode()) <= 64 for b in status_buttons)
    detail = next(b for b in buttons if b.text == "Details")
    assert detail.web_app.url.endswith(f"/property/{target.home_id}")
    assert client.get(f"/property/{target.home_id}").status_code == 200
    assert (
        f'hx-patch="/property/{target.home_id}/status"'
        in client.get(f"/property/{target.home_id}").text
    )
    with patch("home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock):
        response = client.post(
            "/telegram/webhook",
            json=callback(target.home_id),
            headers={"x-telegram-bot-api-secret-token": "test-secret"},
        )
        assert response.status_code == 200
    assert len(await storage.get_status_history(prop.unique_id)) == 1


async def test_stable_callback_keeps_owner_check_and_update_id_deduplication(
    storage, client, sample_property
):
    await storage.save_property(sample_property)
    target = await storage.homes.resolve(sample_property.unique_id)
    assert target
    headers = {"x-telegram-bot-api-secret-token": "test-secret"}
    with patch(
        "home_finder.web.telegram_webhook._answer_callback", new_callable=AsyncMock
    ) as answer:
        assert (
            client.post(
                "/telegram/webhook", json=callback(target.home_id, owner=999), headers=headers
            ).status_code
            == 200
        )
        assert not await storage.get_status_history(sample_property.unique_id)
        answer.assert_not_called()
        client.post("/telegram/webhook", json=callback(target.home_id), headers=headers)
        assert len(await storage.get_status_history(sample_property.unique_id)) == 1
        await storage.update_user_status(target.home_id, UserStatus.ARCHIVED)
        client.post("/telegram/webhook", json=callback(sample_property.unique_id), headers=headers)
        assert len(await storage.get_status_history(sample_property.unique_id)) == 2
        result = await storage.web.get_property_card(target.home_id)
        assert result["user_status"] == UserStatus.ARCHIVED.value


async def test_web_status_and_reanalysis_resolve_stable_identity_in_write_transaction(
    storage, client, sample_property, sample_quality_analysis
):
    await storage.save_property(sample_property)
    await storage.save_quality_analysis(sample_property.unique_id, sample_quality_analysis)
    target = await storage.homes.resolve(sample_property.unique_id)
    assert target
    response = client.patch(f"/property/{target.home_id}/status", data={"status": "interested"})
    assert response.status_code == 200
    assert (await storage.get_status_history(sample_property.unique_id))[0][
        "to_status"
    ] == "interested"
    assert client.post(f"/property/{target.home_id}/reanalyze").status_code == 200
    assert (
        await storage.pipeline.request_reanalysis([target.home_id, sample_property.unique_id]) == 1
    )
    assert (
        client.patch("/property/h_" + "0" * 32 + "/status", data={"status": "archived"}).status_code
        == 404
    )


async def test_notification_refuses_unresolved_persisted_home(sample_property):
    notifier = TelegramNotifier(
        bot_token="fake:token", chat_id=123, home_resolver=AsyncMock(return_value=None)
    )
    bot = AsyncMock()
    with patch.object(notifier, "_get_bot", return_value=bot):
        assert not await notifier.send_property_notification(sample_property)
    bot.send_message.assert_not_called()


def test_keyboard_rejects_invalid_stable_identity(enriched_merged_property):
    with pytest.raises(ValueError, match="Invalid stable home"):
        _build_inline_keyboard(enriched_merged_property, home_id="h_invalid")


@pytest.mark.parametrize("current", [True, False])
async def test_price_drop_uses_persisted_home_and_refuses_a_stale_target(current):
    property_id = "rightmove:" + "1" * 150
    home_id = "h_" + "a" * 32
    notifier = TelegramNotifier(
        bot_token="fake:token",
        chat_id=123,
        web_base_url="https://owner.example",
        home_resolver=AsyncMock(
            return_value=HomeTarget(home_id, property_id if current else "rightmove:other")
        ),
    )
    bot = AsyncMock()
    with patch.object(notifier, "_get_bot", return_value=bot):
        result = await notifier.send_price_drop_notification(
            title="Saved home", postcode="", old_price=2000, new_price=1900, unique_id=property_id
        )
    assert result is current
    if current:
        keyboard = bot.send_message.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[0][0].url == f"https://owner.example/property/{home_id}"
    else:
        bot.send_message.assert_not_called()
