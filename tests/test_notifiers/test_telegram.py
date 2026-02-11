"""Tests for Telegram notifications."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
    TrackedProperty,
    TransportMode,
    ValueAnalysis,
)
from home_finder.notifiers.telegram import (
    TelegramNotifier,
    _format_star_rating,
    _get_best_image_url,
    format_merged_property_caption,
    format_property_message,
)


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


@pytest.fixture
def sample_merged_property(sample_property: Property) -> MergedProperty:
    """Create a sample merged property with two sources."""
    zoopla_url = HttpUrl("https://www.zoopla.co.uk/to-rent/details/99999")
    return MergedProperty(
        canonical=sample_property,
        sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.OPENRENT: sample_property.url,
            PropertySource.ZOOPLA: zoopla_url,
        },
        images=(
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        ),
        min_price=1850,
        max_price=1900,
    )


@pytest.fixture
def sample_quality_analysis() -> PropertyQualityAnalysis:
    """Create a sample quality analysis with overall_rating."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher=True,
            notes="Nice kitchen",
        ),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True, notes="Bright"),
        space=SpaceAnalysis(living_room_sqm=20.0, is_spacious_enough=True, confidence="high"),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2200,
            difference=-300,
            rating="excellent",
            note="£300 below E8 average",
        ),
        overall_rating=4,
        summary="Bright flat with modern kitchen.",
    )


class TestFormatStarRating:
    """Tests for _format_star_rating."""

    def test_rating_1(self) -> None:
        assert _format_star_rating(1) == "⭐☆☆☆☆"

    def test_rating_2(self) -> None:
        assert _format_star_rating(2) == "⭐⭐☆☆☆"

    def test_rating_3(self) -> None:
        assert _format_star_rating(3) == "⭐⭐⭐☆☆"

    def test_rating_4(self) -> None:
        assert _format_star_rating(4) == "⭐⭐⭐⭐☆"

    def test_rating_5(self) -> None:
        assert _format_star_rating(5) == "⭐⭐⭐⭐⭐"


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
        """Test that star rating appears in message when quality analysis has rating."""
        message = format_property_message(sample_property, quality_analysis=sample_quality_analysis)
        assert "⭐⭐⭐⭐☆" in message


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
