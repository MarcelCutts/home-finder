"""Integration tests for Telegram notification formatting with enriched properties."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    TransportMode,
)
from home_finder.notifiers.telegram import (
    TelegramNotifier,
    format_merged_property_caption,
    format_merged_property_message,
)


@pytest.mark.integration
class TestTelegramNotificationE2E:
    """Test notification formatting and sending with fully enriched properties."""

    async def test_send_enriched_notification(
        self,
        enriched_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ):
        """Media group sent with caption containing star rating, price, postcode."""
        notifier = TelegramNotifier(
            bot_token="fake:test-token",
            chat_id=12345,
            web_base_url="http://localhost:8000",
        )
        mock_bot = AsyncMock()
        mock_bot.send_media_group = AsyncMock(return_value=[MagicMock(message_id=1)])
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=3))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(
                enriched_merged_property,
                commute_minutes=15,
                transport_mode=TransportMode.CYCLING,
                quality_analysis=sample_quality_analysis,
            )

        assert result is True
        # 3 gallery images → media group (not single send_photo)
        mock_bot.send_media_group.assert_called_once()
        media = mock_bot.send_media_group.call_args[1]["media"]
        assert len(media) == 3
        # Caption on first photo
        caption = media[0].caption
        assert "⭐⭐⭐⭐☆" in caption  # 4-star rating
        assert "£" in caption
        assert "E8 3RH" in caption
        assert "15 min" in caption

        # Follow-up message with inline keyboard
        mock_bot.send_message.assert_called_once()

        # Venue should be sent with coordinates
        mock_bot.send_venue.assert_called_once()
        venue_kwargs = mock_bot.send_venue.call_args[1]
        assert venue_kwargs["latitude"] == 51.5465

    async def test_caption_under_1024_chars(
        self,
        enriched_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ):
        """Full caption with all data should stay under Telegram 1024 char limit."""
        caption = format_merged_property_caption(
            enriched_merged_property,
            commute_minutes=15,
            transport_mode=TransportMode.CYCLING,
            quality_analysis=sample_quality_analysis,
        )
        assert len(caption) <= 1024

    async def test_inline_keyboard_structure(
        self,
        enriched_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ):
        """With web_base_url: Details + source buttons + Map. Without: source buttons + Map."""
        notifier_with_web = TelegramNotifier(
            bot_token="fake:test-token",
            chat_id=12345,
            web_base_url="http://localhost:8000",
        )
        mock_bot = AsyncMock()
        mock_bot.send_media_group = AsyncMock(return_value=[MagicMock(message_id=1)])
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=3))

        with patch.object(notifier_with_web, "_get_bot", return_value=mock_bot):
            await notifier_with_web.send_merged_property_notification(
                enriched_merged_property,
                quality_analysis=sample_quality_analysis,
            )

        # Keyboard is in the follow-up message (media groups don't support keyboards)
        reply_markup = mock_bot.send_message.call_args[1]["reply_markup"]
        all_buttons = [btn for row in reply_markup.inline_keyboard for btn in row]
        button_texts = [btn.text for btn in all_buttons]

        assert "Details" in button_texts
        assert "OpenRent" in button_texts
        assert "Zoopla" in button_texts
        assert "Map 📍" in button_texts

        # Without web_base_url — no Details button
        notifier_no_web = TelegramNotifier(
            bot_token="fake:test-token",
            chat_id=12345,
        )
        mock_bot2 = AsyncMock()
        mock_bot2.send_media_group = AsyncMock(return_value=[MagicMock(message_id=1)])
        mock_bot2.send_message = AsyncMock(return_value=MagicMock(message_id=2))
        mock_bot2.send_venue = AsyncMock(return_value=MagicMock(message_id=3))

        with patch.object(notifier_no_web, "_get_bot", return_value=mock_bot2):
            await notifier_no_web.send_merged_property_notification(
                enriched_merged_property,
                quality_analysis=sample_quality_analysis,
            )

        reply_markup2 = mock_bot2.send_message.call_args[1]["reply_markup"]
        all_buttons2 = [btn for row in reply_markup2.inline_keyboard for btn in row]
        button_texts2 = [btn.text for btn in all_buttons2]
        assert "Details" not in button_texts2

    async def test_condition_concerns_shown(
        self,
        enriched_merged_property: MergedProperty,
    ):
        """Condition concerns should appear in the message text."""
        analysis_with_concerns = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(notes="Dated kitchen"),
            condition=ConditionAnalysis(
                overall_condition="fair",
                has_visible_damp=True,
                maintenance_concerns=["Damp near bathroom window", "Worn carpet in hallway"],
                confidence="high",
            ),
            light_space=LightSpaceAnalysis(natural_light="fair", notes=""),
            space=SpaceAnalysis(confidence="low"),
            condition_concerns=True,
            concern_severity="moderate",
            overall_rating=2,
            summary="Dated flat with visible damp issues.",
        )

        message = format_merged_property_message(
            enriched_merged_property,
            quality_analysis=analysis_with_concerns,
        )

        assert "Concerns" in message
        assert "Damp near bathroom window" in message

    async def test_photo_fallback_to_text(
        self,
        enriched_merged_property: MergedProperty,
    ):
        """When send_media_group fails, should fallback to send_message."""
        notifier = TelegramNotifier(
            bot_token="fake:test-token",
            chat_id=12345,
        )
        mock_bot = AsyncMock()
        mock_bot.send_media_group = AsyncMock(side_effect=Exception("Media group failed"))
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(
                enriched_merged_property,
            )

        assert result is True
        mock_bot.send_message.assert_called_once()

    async def test_multi_source_listed_on(
        self,
        enriched_merged_property: MergedProperty,
    ):
        """Multi-source property should show 'Listed on:' text."""
        message = format_merged_property_message(enriched_merged_property)
        assert "Listed on:" in message
        assert "OpenRent" in message
        assert "Zoopla" in message

    async def test_price_range_when_varies(
        self,
        enriched_merged_property: MergedProperty,
    ):
        """Price range format should appear when min_price != max_price."""
        assert enriched_merged_property.price_varies

        caption = format_merged_property_caption(enriched_merged_property)
        assert "£1,800" in caption
        assert "£1,850" in caption
