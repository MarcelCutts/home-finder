"""Tests for Telegram inline status buttons and price drop notifications (Tickets 7 & 10)."""

import pytest
from pydantic import HttpUrl

from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
)
from home_finder.notifiers.telegram import _build_inline_keyboard


@pytest.fixture
def sample_merged() -> MergedProperty:
    prop = Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=HttpUrl("https://openrent.com/property/12345"),
        title="1 Bed Flat",
        price_pcm=1900,
        bedrooms=1,
        address="123 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
    )
    return MergedProperty(
        canonical=prop,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop.url},
        min_price=1900,
        max_price=1900,
    )


class TestInlineKeyboardStatusButtons:
    def test_first_row_has_status_buttons(self, sample_merged: MergedProperty) -> None:
        kb = _build_inline_keyboard(sample_merged)
        first_row = kb.inline_keyboard[0]
        assert len(first_row) == 2
        assert "Interested" in first_row[0].text
        assert "Skip" in first_row[1].text

    def test_callback_data_format(self, sample_merged: MergedProperty) -> None:
        kb = _build_inline_keyboard(sample_merged)
        first_row = kb.inline_keyboard[0]
        assert first_row[0].callback_data is not None
        assert first_row[0].callback_data.startswith("st:")
        assert first_row[0].callback_data.endswith(":interested")
        assert first_row[1].callback_data is not None
        assert first_row[1].callback_data.endswith(":archived")

    def test_callback_data_within_64_bytes(self, sample_merged: MergedProperty) -> None:
        kb = _build_inline_keyboard(sample_merged)
        for btn in kb.inline_keyboard[0]:
            assert btn.callback_data is not None
            assert len(btn.callback_data.encode("utf-8")) <= 64

    def test_source_buttons_after_status(self, sample_merged: MergedProperty) -> None:
        """Source URL buttons should appear after the status row."""
        kb = _build_inline_keyboard(sample_merged)
        # Status row is first, source buttons come after
        assert len(kb.inline_keyboard) >= 2
        # Second row should have source links
        source_row = kb.inline_keyboard[1]
        has_url_button = any(btn.url is not None for btn in source_row)
        assert has_url_button

    def test_with_web_base_url(self, sample_merged: MergedProperty) -> None:
        kb = _build_inline_keyboard(sample_merged, web_base_url="http://localhost:8000")
        # First row: status buttons
        assert "Interested" in kb.inline_keyboard[0][0].text
        # Details button should be present in subsequent rows
        all_texts = [
            btn.text
            for row in kb.inline_keyboard[1:]
            for btn in row
        ]
        assert "Details" in all_texts


class TestPriceDropNotification:
    @pytest.mark.asyncio
    async def test_format_contains_key_info(self) -> None:
        """Price drop notification should contain title, old/new price, drop amount."""
        from unittest.mock import AsyncMock, patch

        from home_finder.notifiers.telegram import TelegramNotifier

        notifier = TelegramNotifier(bot_token="fake:token", chat_id=123)

        with patch.object(notifier, "_get_bot") as mock_bot:
            bot = AsyncMock()
            mock_bot.return_value = bot
            bot.send_message = AsyncMock()

            await notifier.send_price_drop_notification(
                title="Nice Flat",
                postcode="E8 3RH",
                old_price=2000,
                new_price=1800,
                unique_id="openrent:123",
                days_listed=14,
            )

            bot.send_message.assert_called_once()
            call_kwargs = bot.send_message.call_args
            text = call_kwargs.kwargs.get("text") or call_kwargs[1].get("text", "")
            assert "Price Drop" in text
            assert "Nice Flat" in text
            assert "2,000" in text
            assert "1,800" in text
            assert "200" in text  # drop amount
            assert "E8 3RH" in text
            assert "14 days" in text
