"""Alerts for a fully enriched home: several portals, a gallery, a price range and concerns.

The sends use the standalone helper (no delivery repository) with a fake bot; the
durable pipeline path is tested in ``test_delivery_recovery.py``. The assessment
is defined here so the album rule (a high fit score and three or more photos)
does not depend on a shared fixture.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_finder.models import (
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
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


@pytest.fixture
def high_fit_assessment() -> PropertyQualityAnalysis:
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher="yes"),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
        space=SpaceAnalysis(living_room_sqm=22.0, is_spacious_enough=True, hosting_layout="good"),
        bedroom=BedroomAnalysis(
            primary_is_double="yes", office_separation="dedicated_room", can_fit_desk="yes"
        ),
        flooring_noise=FlooringNoiseAnalysis(hosting_noise_risk="low", has_double_glazing="yes"),
        condition_concerns=False,
        overall_rating=4,
        summary="Bright, well-maintained flat with modern kitchen.",
    )


def _bot() -> AsyncMock:
    bot = AsyncMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock(message_id=1)])
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))
    bot.send_venue = AsyncMock(return_value=MagicMock(message_id=3))
    return bot


async def test_album_carries_the_caption_then_the_keyboard_and_venue(
    enriched_merged_property: MergedProperty, high_fit_assessment: PropertyQualityAnalysis
):
    notifier = TelegramNotifier(
        bot_token="fake:test-token", chat_id=12345, web_base_url="http://localhost:8000"
    )
    bot = _bot()

    with patch.object(notifier, "_get_bot", return_value=bot):
        sent = await notifier.send_merged_property_notification(
            enriched_merged_property,
            commute_minutes=15,
            transport_mode=TransportMode.CYCLING,
            quality_analysis=high_fit_assessment,
        )

    assert sent is True
    media = bot.send_media_group.call_args.kwargs["media"]
    assert len(media) == 3
    caption = media[0].caption
    assert all(text in caption for text in ("Fit ", "£1,800", "E8 3RH", "15 min"))
    assert all(item.caption is None for item in media[1:])
    # Albums cannot carry a keyboard, so it follows in a separate message.
    assert "reply_markup" in bot.send_message.call_args.kwargs
    assert bot.send_venue.call_args.kwargs["latitude"] == 51.5465


async def test_failed_album_is_not_followed_by_another_format(
    enriched_merged_property: MergedProperty, high_fit_assessment: PropertyQualityAnalysis
):
    """An album that may have partly arrived must not be resent as text."""
    notifier = TelegramNotifier(bot_token="fake:test-token", chat_id=12345)
    bot = _bot()
    bot.send_media_group.side_effect = Exception("Media group failed")

    with patch.object(notifier, "_get_bot", return_value=bot):
        sent = await notifier.send_merged_property_notification(
            enriched_merged_property, quality_analysis=high_fit_assessment
        )

    assert sent is False
    bot.send_media_group.assert_awaited_once()
    bot.send_message.assert_not_called()
    bot.send_photo.assert_not_called()
    bot.send_venue.assert_not_called()


def test_condition_concerns_are_listed_in_the_message(enriched_merged_property: MergedProperty):
    analysis = PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(notes="Dated kitchen"),
        condition=ConditionAnalysis(
            overall_condition="fair",
            has_visible_damp="yes",
            maintenance_concerns=["Damp near bathroom window", "Worn carpet in hallway"],
            confidence="high",
        ),
        light_space=LightSpaceAnalysis(natural_light="fair", notes=""),
        space=SpaceAnalysis(),
        condition_concerns=True,
        concern_severity="moderate",
        overall_rating=2,
        summary="Dated flat with visible damp issues.",
    )

    message = format_merged_property_message(enriched_merged_property, quality_analysis=analysis)

    assert "Concerns" in message
    assert "Damp near bathroom window" in message


def test_message_lists_every_portal_the_home_is_on(enriched_merged_property: MergedProperty):
    message = format_merged_property_message(enriched_merged_property)

    assert "Listed on:" in message
    assert "OpenRent" in message
    assert "Zoopla" in message


def test_caption_shows_the_price_range_when_portal_quotes_differ(
    enriched_merged_property: MergedProperty,
):
    assert enriched_merged_property.price_varies

    caption = format_merged_property_caption(enriched_merged_property)

    assert "£1,800" in caption
    assert "£1,850" in caption
