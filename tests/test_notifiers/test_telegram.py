"""Tests for Telegram notifications."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.models import (
    Property,
    PropertySource,
    TrackedProperty,
    TransportMode,
)
from home_finder.notifiers.telegram import TelegramNotifier, format_property_message


@pytest.fixture
def sample_property() -> Property:
    """Create a sample property."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=HttpUrl("https://openrent.com/property/12345"),
        title="1 Bed Flat, Mare Street",
        price_pcm=1900,
        bedrooms=1,
        address="123 Mare Street, Hackney, E8 3RH",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        first_seen=datetime(2025, 1, 20, 14, 30),
    )


@pytest.fixture
def sample_tracked_property(sample_property: Property) -> TrackedProperty:
    """Create a sample tracked property with commute info."""
    return TrackedProperty(
        property=sample_property,
        commute_minutes=18,
        transport_mode=TransportMode.CYCLING,
    )


class TestFormatPropertyMessage:
    """Tests for message formatting."""

    def test_format_basic_property(self, sample_property: Property) -> None:
        """Test formatting a basic property."""
        message = format_property_message(sample_property)

        assert "1 Bed Flat" in message
        assert "£1,900" in message
        assert "1 bed" in message.lower()
        assert "E8 3RH" in message
        assert "openrent.com" in message

    def test_format_property_with_commute(
        self, sample_tracked_property: TrackedProperty
    ) -> None:
        """Test formatting a property with commute info."""
        message = format_property_message(
            sample_tracked_property.property,
            commute_minutes=sample_tracked_property.commute_minutes,
            transport_mode=sample_tracked_property.transport_mode,
        )

        assert "18 min" in message
        assert "cycling" in message.lower() or "bike" in message.lower()

    def test_format_property_contains_link(self, sample_property: Property) -> None:
        """Test that message contains clickable link."""
        message = format_property_message(sample_property)

        # Should contain the URL
        assert "https://openrent.com/property/12345" in message

    def test_format_property_html_safe(self, sample_property: Property) -> None:
        """Test that message is safe for HTML parsing."""
        # Create property with characters that need escaping
        prop_with_special = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Flat <nice> & cozy",
            price_pcm=2000,
            bedrooms=1,
            address="Test & Co. Ltd <Building>",
        )

        message = format_property_message(prop_with_special)

        # Special chars should be escaped for HTML
        assert "&lt;" in message or "<" not in message.replace("<a ", "").replace("<b>", "").replace("</b>", "").replace("</a>", "")
        assert "&amp;" in message or "& " not in message


class TestTelegramNotifier:
    """Tests for TelegramNotifier."""

    def test_init(self) -> None:
        """Test initializing notifier."""
        notifier = TelegramNotifier(
            bot_token="123456:ABC-DEF",
            chat_id=12345678,
        )
        assert notifier.chat_id == 12345678

    @pytest.mark.asyncio
    async def test_send_notification(self, sample_property: Property) -> None:
        """Test sending a notification."""
        notifier = TelegramNotifier(
            bot_token="123456:ABC-DEF",
            chat_id=12345678,
        )

        # Mock the bot
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_property_notification(sample_property)

        assert result is True
        mock_bot.send_message.assert_called_once()

        # Check the call arguments
        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 12345678
        assert "£1,900" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_notification_with_commute(
        self, sample_tracked_property: TrackedProperty
    ) -> None:
        """Test sending notification with commute info."""
        notifier = TelegramNotifier(
            bot_token="123456:ABC-DEF",
            chat_id=12345678,
        )

        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_property_notification(
                sample_tracked_property.property,
                commute_minutes=sample_tracked_property.commute_minutes,
                transport_mode=sample_tracked_property.transport_mode,
            )

        assert result is True

        call_kwargs = mock_bot.send_message.call_args[1]
        assert "18 min" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_notification_failure(self, sample_property: Property) -> None:
        """Test handling notification failure."""
        notifier = TelegramNotifier(
            bot_token="123456:ABC-DEF",
            chat_id=12345678,
        )

        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(side_effect=Exception("API Error"))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_property_notification(sample_property)

        assert result is False

    @pytest.mark.asyncio
    async def test_send_batch_notifications(self, sample_property: Property) -> None:
        """Test sending multiple notifications."""
        notifier = TelegramNotifier(
            bot_token="123456:ABC-DEF",
            chat_id=12345678,
        )

        properties = [sample_property, sample_property, sample_property]

        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            results = await notifier.send_batch_notifications(properties)

        assert len(results) == 3
        assert all(results)
        assert mock_bot.send_message.call_count == 3
