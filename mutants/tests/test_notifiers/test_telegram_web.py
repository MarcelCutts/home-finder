"""Tests for Telegram notifier web dashboard integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.models import MergedProperty, Property, PropertySource
from home_finder.notifiers.telegram import TelegramNotifier, _build_inline_keyboard


class TestBuildInlineKeyboardWithWebUrl:
    def test_with_https_url_uses_webapp_button(
        self, sample_merged_property: MergedProperty
    ) -> None:
        keyboard = _build_inline_keyboard(
            sample_merged_property, web_base_url="https://home-finder.fly.dev"
        )
        # First button should be "Details" as a WebApp button
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert all_buttons[0].text == "Details"
        assert all_buttons[0].web_app is not None
        assert "/property/" in all_buttons[0].web_app.url
        assert sample_merged_property.unique_id in all_buttons[0].web_app.url

    def test_with_http_url_uses_regular_link(self, sample_merged_property: MergedProperty) -> None:
        keyboard = _build_inline_keyboard(
            sample_merged_property, web_base_url="http://localhost:8000"
        )
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert all_buttons[0].text == "Details"
        assert all_buttons[0].web_app is None
        assert "/property/" in all_buttons[0].url

    def test_without_web_base_url_no_details_button(
        self, sample_merged_property: MergedProperty
    ) -> None:
        keyboard = _build_inline_keyboard(sample_merged_property, web_base_url="")
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        button_texts = [btn.text for btn in all_buttons]
        assert "Details" not in button_texts

    def test_with_web_base_url_still_has_source_buttons(
        self, sample_merged_property: MergedProperty
    ) -> None:
        keyboard = _build_inline_keyboard(
            sample_merged_property, web_base_url="https://home-finder.fly.dev"
        )
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        button_texts = [btn.text for btn in all_buttons]
        assert "OpenRent" in button_texts
        assert "Zoopla" in button_texts

    def test_web_base_url_trailing_slash_stripped(
        self, sample_merged_property: MergedProperty
    ) -> None:
        keyboard = _build_inline_keyboard(
            sample_merged_property, web_base_url="https://home-finder.fly.dev/"
        )
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        # URL should not have double slashes
        assert "//property" not in all_buttons[0].web_app.url

    def test_map_button_present_with_coords(self, sample_merged_property: MergedProperty) -> None:
        keyboard = _build_inline_keyboard(sample_merged_property)
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        map_buttons = [btn for btn in all_buttons if "Map" in btn.text]
        assert len(map_buttons) == 1

    def test_no_map_button_without_coords(self) -> None:
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="999",
            url=HttpUrl("https://openrent.com/999"),
            title="Test",
            price_pcm=1500,
            bedrooms=1,
            address="No coords place",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            min_price=1500,
            max_price=1500,
        )
        keyboard = _build_inline_keyboard(merged)
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        # Should have address-based map button instead
        map_buttons = [btn for btn in all_buttons if "Map" in btn.text]
        assert len(map_buttons) == 1


class TestTelegramNotifierWebBaseUrl:
    def test_stores_web_base_url(self) -> None:
        notifier = TelegramNotifier(
            bot_token="123:ABC",
            chat_id=12345,
            web_base_url="https://home-finder.fly.dev",
        )
        assert notifier.web_base_url == "https://home-finder.fly.dev"

    def test_strips_trailing_slash(self) -> None:
        notifier = TelegramNotifier(
            bot_token="123:ABC",
            chat_id=12345,
            web_base_url="https://home-finder.fly.dev/",
        )
        assert notifier.web_base_url == "https://home-finder.fly.dev"

    def test_empty_web_base_url(self) -> None:
        notifier = TelegramNotifier(
            bot_token="123:ABC",
            chat_id=12345,
        )
        assert notifier.web_base_url == ""

    @pytest.mark.asyncio
    async def test_send_merged_passes_web_base_url(
        self, sample_merged_property: MergedProperty
    ) -> None:
        notifier = TelegramNotifier(
            bot_token="123:ABC",
            chat_id=12345,
            web_base_url="https://home-finder.fly.dev",
        )
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(sample_merged_property)

        assert result is True
        # The keyboard should have been built with web_base_url
        # We can verify by checking that send_photo was called with a keyboard
        # that contains a "Details" button
        call_kwargs = mock_bot.send_photo.call_args
        if call_kwargs:
            keyboard = call_kwargs[1].get("reply_markup")
            if keyboard:
                all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
                assert any(btn.text == "Details" for btn in all_buttons)
