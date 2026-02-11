"""Send a test notification to preview the enriched Telegram format.

Usage:
    uv run python scripts/test_notification.py
"""

import asyncio
from datetime import datetime

from pydantic import HttpUrl

from home_finder.config import Settings
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
    TransportMode,
    ValueAnalysis,
)
from home_finder.notifiers.telegram import TelegramNotifier


def _build_dummy_merged() -> MergedProperty:
    openrent_url = HttpUrl("https://www.openrent.com/property-to-rent/12345")
    zoopla_url = HttpUrl("https://www.zoopla.co.uk/to-rent/details/12345")

    canonical = Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=openrent_url,
        title="Stunning 2 Bed Flat in Dalston",
        price_pcm=2100,
        bedrooms=2,
        address="42 Dalston Lane, Hackney",
        postcode="E8 3AH",
        latitude=51.5462,
        longitude=-0.0750,
        image_url=HttpUrl(
            "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80"
        ),
        first_seen=datetime(2026, 2, 1, 10, 0),
    )

    return MergedProperty(
        canonical=canonical,
        sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.OPENRENT: openrent_url,
            PropertySource.ZOOPLA: zoopla_url,
        },
        images=(
            PropertyImage(
                url=HttpUrl(
                    "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80"
                ),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        ),
        min_price=2050,
        max_price=2100,
    )


def _build_dummy_analysis() -> PropertyQualityAnalysis:
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher=True,
            has_washing_machine=True,
            notes="Recently refurbished with quartz worktops",
        ),
        condition=ConditionAnalysis(
            overall_condition="good",
            confidence="high",
        ),
        light_space=LightSpaceAnalysis(
            natural_light="good",
            window_sizes="large",
            feels_spacious=True,
            ceiling_height="high",
            notes="South-facing living room",
        ),
        space=SpaceAnalysis(
            living_room_sqm=22.0,
            is_spacious_enough=True,
            confidence="high",
        ),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2350,
            difference=-250,
            rating="excellent",
            note="£250 below E8 average",
            quality_adjusted_rating="good",
            quality_adjusted_note="Good value considering modern kitchen and condition",
        ),
        overall_rating=4,
        summary="Bright, spacious flat with a modern kitchen and high ceilings. "
        "Great natural light from south-facing windows.",
    )


async def main() -> None:
    settings = Settings()
    token = settings.telegram_bot_token.get_secret_value()
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        print("Error: Set HOME_FINDER_TELEGRAM_BOT_TOKEN and HOME_FINDER_TELEGRAM_CHAT_ID in .env")
        return

    notifier = TelegramNotifier(bot_token=token, chat_id=chat_id)
    merged = _build_dummy_merged()
    analysis = _build_dummy_analysis()

    try:
        ok = await notifier.send_merged_property_notification(
            merged,
            commute_minutes=14,
            transport_mode=TransportMode.CYCLING,
            quality_analysis=analysis,
        )
        print(f"Notification sent: {ok}")
    finally:
        await notifier.close()


if __name__ == "__main__":
    asyncio.run(main())
