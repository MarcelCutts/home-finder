"""Tests for Telegram notifications."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    ListingRedFlags,
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
    TrackedProperty,
    TransportMode,
    ViewingNotes,
)
from home_finder.notifiers.telegram import (
    TelegramNotifier,
    _format_followup_detail,
    _format_quality_block,
    _format_star_rating,
    _format_value_info,
    _format_viewing_notes,
    _get_best_image_url,
    _get_gallery_urls,
    format_merged_property_caption,
    format_merged_property_message,
    format_property_message,
)


class TestFormatStarRating:
    """Tests for _format_star_rating."""

    @pytest.mark.parametrize(
        ("rating", "expected"),
        [
            (1, "⭐☆☆☆☆"),
            (2, "⭐⭐☆☆☆"),
            (3, "⭐⭐⭐☆☆"),
            (4, "⭐⭐⭐⭐☆"),
            (5, "⭐⭐⭐⭐⭐"),
        ],
    )
    def test_star_rating(self, rating: int, expected: str) -> None:
        assert _format_star_rating(rating) == expected


class TestFormatPropertyMessage:
    """Tests for message formatting."""

    def test_format_basic_property(self, sample_property: Property) -> None:
        """Test formatting a basic property."""
        message = format_property_message(sample_property)

        assert "1 Bed Flat" in message
        assert "£1,900" in message
        assert "1 bed" in message.lower()
        assert "E8 3RH" in message
        assert "OpenRent" in message

    def test_format_property_with_commute(self, sample_tracked_property: TrackedProperty) -> None:
        """Test formatting a property with commute info."""
        message = format_property_message(
            sample_tracked_property.property,
            commute_minutes=sample_tracked_property.commute_minutes,
            transport_mode=sample_tracked_property.transport_mode,
        )

        assert "18 min" in message
        assert "🚴" in message

    def test_format_property_contains_source(self, sample_property: Property) -> None:
        """Test that message contains source name."""
        message = format_property_message(sample_property)

        # Should contain the source name
        assert "OpenRent" in message

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
        assert "&lt;" in message or "<" not in message.replace("<a ", "").replace(
            "<b>", ""
        ).replace("</b>", "").replace("</a>", "")
        assert "&amp;" in message or "& " not in message

    def test_format_property_with_star_rating(
        self,
        sample_property: Property,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that star rating appears merged with price on same line."""
        message = format_property_message(sample_property, quality_analysis=sample_quality_analysis)
        assert "⭐⭐⭐⭐☆" in message
        # Stars should be on the same line as price (merged format)
        for line in message.split("\n"):
            if "⭐⭐⭐⭐☆" in line:
                assert "£" in line
                break

    def test_format_property_with_highlights_lowlights(
        self,
        sample_property: Property,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that highlights and lowlights appear in full message."""
        message = format_property_message(sample_property, quality_analysis=sample_quality_analysis)
        assert "✅" in message
        assert "Gas hob" in message
        assert "⛔" in message
        assert "No garden" in message

    def test_format_property_with_expandable_blockquote(
        self,
        sample_property: Property,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that full message uses expandable blockquote for summary."""
        message = format_property_message(sample_property, quality_analysis=sample_quality_analysis)
        assert "<blockquote expandable>" in message

    def test_format_property_with_viewing_notes(
        self,
        sample_property: Property,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that viewing notes appear in full message."""
        message = format_property_message(sample_property, quality_analysis=sample_quality_analysis)
        assert "Check water pressure" in message
        assert "Ask about sound insulation" in message
        assert "Test internet speed" in message


class TestFormatMergedPropertyCaption:
    """Tests for format_merged_property_caption."""

    def test_caption_under_1024_chars(
        self,
        sample_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        caption = format_merged_property_caption(
            sample_merged_property,
            commute_minutes=14,
            transport_mode=TransportMode.CYCLING,
            quality_analysis=sample_quality_analysis,
        )
        assert len(caption) <= 1024

    def test_caption_contains_key_fields(
        self,
        sample_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        caption = format_merged_property_caption(
            sample_merged_property,
            commute_minutes=14,
            transport_mode=TransportMode.CYCLING,
            quality_analysis=sample_quality_analysis,
        )
        assert "1 Bed Flat" in caption
        assert "⭐⭐⭐⭐☆" in caption
        assert "£" in caption
        assert "1 bed" in caption.lower()
        assert "14 min" in caption

    def test_caption_uses_one_line_over_summary(
        self,
        sample_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that caption prefers one_line field over full summary."""
        caption = format_merged_property_caption(
            sample_merged_property,
            quality_analysis=sample_quality_analysis,
        )
        # one_line should be used in caption, not the full summary
        assert "Bright modern flat with gas kitchen" in caption

    def test_caption_shows_highlight_lowlight_chips(
        self,
        sample_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that caption shows highlight/lowlight chip lines."""
        caption = format_merged_property_caption(
            sample_merged_property,
            quality_analysis=sample_quality_analysis,
        )
        assert "✅" in caption
        assert "⛔" in caption

    def test_caption_falls_back_to_summary_without_one_line(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Test that caption falls back to summary when one_line is absent."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Decent flat overall.",
        )
        caption = format_merged_property_caption(
            sample_merged_property,
            quality_analysis=analysis,
        )
        assert "Decent flat overall." in caption

    def test_caption_without_quality(self, sample_merged_property: MergedProperty) -> None:
        caption = format_merged_property_caption(sample_merged_property)
        assert "£" in caption
        assert len(caption) <= 1024


class TestGetBestImageUrl:
    """Tests for _get_best_image_url."""

    def test_returns_canonical_image(self, sample_property: Property) -> None:
        prop_with_img = sample_property.model_copy(
            update={"image_url": HttpUrl("https://example.com/main.jpg")}
        )
        merged = MergedProperty(
            canonical=prop_with_img,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_with_img.url},
            min_price=1900,
            max_price=1900,
        )
        assert _get_best_image_url(merged) == "https://example.com/main.jpg"

    def test_falls_back_to_gallery(self, sample_merged_property: MergedProperty) -> None:
        # sample_merged_property has no canonical image_url but has gallery images
        merged_no_canonical_img = MergedProperty(
            canonical=sample_merged_property.canonical.model_copy(update={"image_url": None}),
            sources=sample_merged_property.sources,
            source_urls=sample_merged_property.source_urls,
            images=sample_merged_property.images,
            min_price=sample_merged_property.min_price,
            max_price=sample_merged_property.max_price,
        )
        url = _get_best_image_url(merged_no_canonical_img)
        assert url is not None
        assert "img1.jpg" in url

    def test_returns_none_when_no_images(self, sample_property: Property) -> None:
        prop_no_img = sample_property.model_copy(update={"image_url": None})
        merged = MergedProperty(
            canonical=prop_no_img,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_no_img.url},
            min_price=1900,
            max_price=1900,
        )
        assert _get_best_image_url(merged) is None


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

    @pytest.mark.asyncio
    async def test_send_merged_with_image_sends_photo(
        self,
        sample_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that send_merged_property_notification uses send_photo when image available."""
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(
                sample_merged_property, quality_analysis=sample_quality_analysis
            )

        assert result is True
        mock_bot.send_photo.assert_called_once()
        call_kwargs = mock_bot.send_photo.call_args[1]
        assert call_kwargs["chat_id"] == 12345678
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_send_merged_without_image_sends_message(self, sample_property: Property) -> None:
        """Test fallback to send_message when no image available."""
        merged_no_img = MergedProperty(
            canonical=sample_property.model_copy(update={"image_url": None}),
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: sample_property.url},
            min_price=1900,
            max_price=1900,
        )
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(merged_no_img)

        assert result is True
        mock_bot.send_message.assert_called_once()
        mock_bot.send_photo.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_merged_sends_venue_when_coords(
        self, sample_merged_property: MergedProperty
    ) -> None:
        """Test that venue is sent when coordinates are available."""
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            await notifier.send_merged_property_notification(sample_merged_property)

        mock_bot.send_venue.assert_called_once()
        venue_kwargs = mock_bot.send_venue.call_args[1]
        assert venue_kwargs["latitude"] == 51.5465
        assert venue_kwargs["longitude"] == -0.0553

    @pytest.mark.asyncio
    async def test_send_merged_no_venue_when_no_coords(self, sample_property: Property) -> None:
        """Test that venue is NOT sent when coordinates are absent."""
        prop_no_coords = sample_property.model_copy(
            update={"latitude": None, "longitude": None, "image_url": None}
        )
        merged = MergedProperty(
            canonical=prop_no_coords,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_no_coords.url},
            min_price=1900,
            max_price=1900,
        )
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            await notifier.send_merged_property_notification(merged)

        mock_bot.send_venue.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_merged_media_group_for_many_images(self, sample_property: Property) -> None:
        """Test that send_media_group is used when 3+ gallery images available."""
        images = tuple(
            PropertyImage(
                url=HttpUrl(f"https://example.com/img{i}.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            )
            for i in range(5)
        )
        merged = MergedProperty(
            canonical=sample_property,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: sample_property.url},
            images=images,
            min_price=1900,
            max_price=1900,
        )
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_media_group = AsyncMock(return_value=[MagicMock(message_id=1)])
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=3))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(merged)

        assert result is True
        mock_bot.send_media_group.assert_called_once()
        # Follow-up message with inline keyboard but NO repeated header
        mock_bot.send_message.assert_called_once()
        followup_kwargs = mock_bot.send_message.call_args[1]
        assert "reply_markup" in followup_kwargs
        # Without quality_analysis, follow-up should be the minimal pointer
        assert "View links" in followup_kwargs["text"]
        # send_photo should NOT be called (media group used instead)
        mock_bot.send_photo.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_merged_media_group_followup_has_detail_not_header(
        self,
        sample_property: Property,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Test that media group follow-up contains detail, not repeated header."""
        images = tuple(
            PropertyImage(
                url=HttpUrl(f"https://example.com/img{i}.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            )
            for i in range(5)
        )
        merged = MergedProperty(
            canonical=sample_property,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: sample_property.url},
            images=images,
            min_price=1900,
            max_price=1900,
        )
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_media_group = AsyncMock(return_value=[MagicMock(message_id=1)])
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=3))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(
                merged, quality_analysis=sample_quality_analysis
            )

        assert result is True
        followup_text = mock_bot.send_message.call_args[1]["text"]
        # Follow-up should have detail breakdown
        assert "🍳" in followup_text
        assert "💡" in followup_text
        # Follow-up should NOT repeat the header info (that's in the caption)
        assert "🏠" not in followup_text
        assert "1 Bed Flat" not in followup_text

    @pytest.mark.asyncio
    async def test_send_merged_single_photo_for_few_images(self, sample_property: Property) -> None:
        """Test that send_photo is used when only 1-2 gallery images available."""
        images = (
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        )
        merged = MergedProperty(
            canonical=sample_property,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: sample_property.url},
            images=images,
            min_price=1900,
            max_price=1900,
        )
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1))
        mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            result = await notifier.send_merged_property_notification(merged)

        assert result is True
        mock_bot.send_photo.assert_called_once()
        mock_bot.send_media_group.assert_not_called()


class TestGetGalleryUrls:
    """Tests for _get_gallery_urls."""

    def test_returns_gallery_images(self, sample_property: Property) -> None:
        images = tuple(
            PropertyImage(
                url=HttpUrl(f"https://example.com/img{i}.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            )
            for i in range(3)
        )
        merged = MergedProperty(
            canonical=sample_property,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: sample_property.url},
            images=images,
            min_price=1900,
            max_price=1900,
        )
        urls = _get_gallery_urls(merged)
        assert len(urls) == 3
        assert all("example.com" in u for u in urls)

    def test_limits_to_max_images(self, sample_property: Property) -> None:
        images = tuple(
            PropertyImage(
                url=HttpUrl(f"https://example.com/img{i}.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            )
            for i in range(15)
        )
        merged = MergedProperty(
            canonical=sample_property,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: sample_property.url},
            images=images,
            min_price=1900,
            max_price=1900,
        )
        urls = _get_gallery_urls(merged, max_images=10)
        assert len(urls) == 10

    def test_skips_non_gallery_images(self, sample_property: Property) -> None:
        images = (
            PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=PropertySource.OPENRENT,
                image_type="floorplan",
            ),
        )
        merged = MergedProperty(
            canonical=sample_property.model_copy(update={"image_url": None}),
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: sample_property.url},
            images=images,
            min_price=1900,
            max_price=1900,
        )
        urls = _get_gallery_urls(merged)
        assert urls == []

    def test_falls_back_to_canonical_image(self, sample_property: Property) -> None:
        prop_with_img = sample_property.model_copy(
            update={"image_url": HttpUrl("https://example.com/thumb.jpg")}
        )
        merged = MergedProperty(
            canonical=prop_with_img,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_with_img.url},
            min_price=1900,
            max_price=1900,
        )
        urls = _get_gallery_urls(merged)
        assert urls == ["https://example.com/thumb.jpg"]


class TestFormatQualityBlock:
    """Tests for _format_quality_block."""

    def test_full_mode_expandable_blockquote(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        """Test that full mode uses expandable blockquote."""
        lines = _format_quality_block(sample_quality_analysis, full=True)
        text = "\n".join(lines)
        assert "<blockquote expandable>" in text

    def test_full_mode_includes_highlights(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        lines = _format_quality_block(sample_quality_analysis, full=True)
        text = "\n".join(lines)
        assert "✅ Gas hob · Good light · Spacious living room" in text

    def test_full_mode_includes_lowlights(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        lines = _format_quality_block(sample_quality_analysis, full=True)
        text = "\n".join(lines)
        assert "⛔ No garden · Street noise" in text

    def test_full_mode_includes_viewing_notes(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        lines = _format_quality_block(sample_quality_analysis, full=True)
        text = "\n".join(lines)
        assert "👁" in text
        assert "Check water pressure" in text
        assert "❓" in text
        assert "Ask about sound insulation" in text
        assert "🔍" in text
        assert "Test internet speed" in text

    def test_condensed_mode_uses_one_line(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        """Test that condensed mode prefers one_line over summary."""
        lines = _format_quality_block(sample_quality_analysis, full=False)
        text = "\n".join(lines)
        assert "Bright modern flat with gas kitchen" in text
        # Should NOT contain the full summary
        assert "Bright flat with modern kitchen." not in text

    def test_condensed_mode_falls_back_to_summary(self) -> None:
        """Test condensed mode falls back to summary when one_line is absent."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Decent flat overall.",
        )
        lines = _format_quality_block(analysis, full=False)
        text = "\n".join(lines)
        assert "Decent flat overall." in text

    def test_condensed_mode_shows_highlight_chips(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        lines = _format_quality_block(sample_quality_analysis, full=False)
        text = "\n".join(lines)
        assert "✅" in text
        assert "⛔" in text

    def test_condensed_mode_critical_alerts(self) -> None:
        """Test that condensed mode still shows critical EPC and red flags."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            listing_extraction=ListingExtraction(epc_rating="F"),
            listing_red_flags=ListingRedFlags(
                red_flag_count=3, too_few_photos=True, missing_room_photos=["kitchen"]
            ),
        )
        lines = _format_quality_block(analysis, full=False)
        text = "\n".join(lines)
        assert "⚠️ EPC F" in text
        assert "🚩 3 red flags" in text

    def test_no_highlights_when_absent(self) -> None:
        """Test that highlight/lowlight lines are omitted when not available."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Basic flat.",
        )
        lines = _format_quality_block(analysis, full=True)
        text = "\n".join(lines)
        assert "✅" not in text
        assert "⛔" not in text


class TestFormatViewingNotes:
    """Tests for _format_viewing_notes."""

    def test_formats_all_note_types(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        lines = _format_viewing_notes(sample_quality_analysis)
        assert len(lines) == 3
        assert "👁" in lines[0]
        assert "❓" in lines[1]
        assert "🔍" in lines[2]

    def test_returns_empty_when_no_notes(self) -> None:
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
        )
        assert _format_viewing_notes(analysis) == []

    def test_partial_notes(self) -> None:
        """Test with only some note types populated."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            viewing_notes=ViewingNotes(
                check_items=["Check damp"],
            ),
        )
        lines = _format_viewing_notes(analysis)
        assert len(lines) == 1
        assert "Check damp" in lines[0]


class TestMergedHeaderFormat:
    """Tests for the merged star rating + price header line format."""

    def test_rating_and_price_on_same_line(
        self,
        sample_merged_property: MergedProperty,
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Stars, price, and beds should all be on the same line."""
        message = format_merged_property_message(
            sample_merged_property,
            quality_analysis=sample_quality_analysis,
        )
        for line in message.split("\n"):
            if "⭐" in line:
                assert "£" in line
                assert "bed" in line.lower()
                break
        else:
            pytest.fail("No line found with star rating")

    def test_no_rating_still_shows_price(
        self, sample_merged_property: MergedProperty
    ) -> None:
        """Without quality analysis, price line should still work."""
        message = format_merged_property_message(sample_merged_property)
        assert "£" in message
        assert "bed" in message.lower()


class TestStatusMessageSilent:
    """Tests for silent status messages."""

    @pytest.mark.asyncio
    async def test_status_message_is_silent(self) -> None:
        """Test that status messages use disable_notification=True."""
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            await notifier.send_status_message("Pipeline started")

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["disable_notification"] is True


class TestLinkPreviewOptions:
    """Tests for LinkPreviewOptions usage."""

    @pytest.mark.asyncio
    async def test_send_property_uses_link_preview_options(
        self, sample_property: Property
    ) -> None:
        """Test that send_property_notification uses LinkPreviewOptions."""
        notifier = TelegramNotifier(bot_token="123456:ABC-DEF", chat_id=12345678)
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        with patch.object(notifier, "_get_bot", return_value=mock_bot):
            await notifier.send_property_notification(sample_property)

        call_kwargs = mock_bot.send_message.call_args[1]
        assert "link_preview_options" in call_kwargs
        assert call_kwargs["link_preview_options"].is_disabled is True
        # Old parameter should not be present
        assert "disable_web_page_preview" not in call_kwargs


class TestFormatValueInfoBrief:
    """Tests for _format_value_info brief mode."""

    def test_brief_returns_short_benchmark(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        """Brief mode should return rating + benchmark note, no essay."""
        result = _format_value_info(sample_quality_analysis, brief=True)
        assert result is not None
        # Should contain the benchmark note
        assert "£300 below E8 average" in result
        # Should NOT contain the full quality_adjusted_note (if any)
        assert "Excellent" in result or "excellent" in result.lower()

    def test_brief_without_benchmark(self) -> None:
        """Brief with no benchmark note should just show rating."""
        from home_finder.models import ValueAnalysis

        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            value=ValueAnalysis(
                quality_adjusted_rating="good",
                quality_adjusted_note="Long essay about value...",
            ),
        )
        result = _format_value_info(analysis, brief=True)
        assert result is not None
        assert "Good value" in result
        # Should NOT include the quality_adjusted_note essay
        assert "Long essay" not in result

    def test_full_includes_quality_note(self) -> None:
        """Full mode (default) should include quality_adjusted_note."""
        from home_finder.models import ValueAnalysis

        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            value=ValueAnalysis(
                quality_adjusted_rating="fair",
                quality_adjusted_note="Full analysis of value considerations.",
                note="£200 above average",
            ),
        )
        result = _format_value_info(analysis, brief=False)
        assert result is not None
        assert "Full analysis of value considerations" in result

    def test_condensed_caption_uses_brief_value(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Condensed caption should use brief value (no essay)."""
        from home_finder.models import ValueAnalysis

        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            value=ValueAnalysis(
                quality_adjusted_rating="fair",
                quality_adjusted_note="This is a very long essay about value " * 10,
                note="£200 above average",
            ),
        )
        caption = format_merged_property_caption(
            sample_merged_property, quality_analysis=analysis
        )
        assert "£200 above average" in caption
        assert "very long essay" not in caption


class TestFormatFollowupDetail:
    """Tests for _format_followup_detail."""

    def test_contains_detail_breakdown(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        """Follow-up should contain kitchen, light, space, condition detail."""
        result = _format_followup_detail(quality_analysis=sample_quality_analysis)
        assert "🍳" in result
        assert "💡" in result
        assert "📐" in result
        assert "🔧" in result

    def test_does_not_contain_header(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        """Follow-up should NOT contain title, price, address, or header emoji."""
        result = _format_followup_detail(quality_analysis=sample_quality_analysis)
        assert "🏠" not in result
        assert "💰" not in result
        assert "🛏" not in result
        assert "📍" not in result

    def test_does_not_repeat_highlights(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        """Follow-up should NOT repeat highlight/lowlight chips (those are in caption)."""
        result = _format_followup_detail(quality_analysis=sample_quality_analysis)
        assert "✅" not in result
        assert "⛔" not in result

    def test_includes_viewing_notes(
        self, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        result = _format_followup_detail(quality_analysis=sample_quality_analysis)
        assert "👁" in result
        assert "Check water pressure" in result

    def test_value_in_expandable_blockquote(self) -> None:
        """Full value commentary should be in an expandable blockquote."""
        from home_finder.models import ValueAnalysis

        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            value=ValueAnalysis(
                quality_adjusted_rating="fair",
                quality_adjusted_note="Detailed value commentary here.",
                note="£200 above average",
            ),
        )
        result = _format_followup_detail(quality_analysis=analysis)
        assert "<blockquote expandable>" in result
        assert "Detailed value commentary here." in result

    def test_returns_empty_without_quality(self) -> None:
        """Should return empty string when no quality analysis provided."""
        assert _format_followup_detail(quality_analysis=None) == ""
