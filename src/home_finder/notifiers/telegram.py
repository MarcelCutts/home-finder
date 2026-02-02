"""Telegram notification service."""

import asyncio
import html
from typing import TYPE_CHECKING

from home_finder.filters.quality import PropertyQualityAnalysis
from home_finder.logging import get_logger
from home_finder.models import Property, TransportMode

if TYPE_CHECKING:
    from aiogram import Bot

logger = get_logger(__name__)


def _format_kitchen_info(analysis: PropertyQualityAnalysis) -> str:
    """Format kitchen analysis for display."""
    kitchen = analysis.kitchen
    items = []

    if kitchen.has_gas_hob is True:
        items.append("Gas hob")
    elif kitchen.has_gas_hob is False:
        items.append("Electric hob")

    if kitchen.has_dishwasher is True:
        items.append("Dishwasher")
    if kitchen.has_washing_machine is True:
        items.append("Washer")
    if kitchen.has_dryer is True:
        items.append("Dryer")

    quality_str = ""
    if kitchen.appliance_quality:
        quality_str = f" ({kitchen.appliance_quality} quality)"

    if items:
        return ", ".join(items) + quality_str
    return "Not visible in photos"


def _format_light_space_info(analysis: PropertyQualityAnalysis) -> str:
    """Format light/space analysis for display."""
    light = analysis.light_space
    parts = [f"Light: {light.natural_light.capitalize()}"]
    if light.feels_spacious is True:
        parts.append("Feels spacious")
    elif light.feels_spacious is False:
        parts.append("Compact")
    # None = unknown, don't add anything
    return " | ".join(parts)


def _format_space_info(analysis: PropertyQualityAnalysis) -> str:
    """Format space analysis for display."""
    space = analysis.space
    if space.living_room_sqm:
        sqm = f"~{space.living_room_sqm:.0f}m²"
        if space.is_spacious_enough is True:
            return f"{sqm} (good for office + hosting)"
        elif space.is_spacious_enough is False:
            return f"{sqm} (may be tight for office + hosting)"
        return sqm  # Unknown spaciousness
    if space.is_spacious_enough is True:
        return "Size unknown (likely spacious)"
    elif space.is_spacious_enough is False:
        return "May be compact"
    return "Size unknown"


def _format_value_info(analysis: PropertyQualityAnalysis) -> str | None:
    """Format value assessment for display.

    Prefers the quality-adjusted rating from Claude if available,
    falls back to the simple price-based rating.
    """
    value = analysis.value
    if not value:
        return None

    # Emoji based on rating
    emoji_map = {
        "excellent": "💰",
        "good": "✓",
        "fair": "~",
        "poor": "⚠️",
    }

    # Prefer quality-adjusted rating from Claude
    if value.quality_adjusted_rating:
        emoji = emoji_map.get(value.quality_adjusted_rating, "")
        note = value.quality_adjusted_note or value.note
        return f"{emoji} {value.quality_adjusted_rating.capitalize()} value ({note})"

    # Fall back to simple price comparison
    if value.rating:
        emoji = emoji_map.get(value.rating, "")
        return f"{emoji} {value.rating.capitalize()} value ({value.note})"

    return None


def format_property_message(
    prop: Property,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    quality_analysis: PropertyQualityAnalysis | None = None,
) -> str:
    """Format a property as a Telegram message.

    Args:
        prop: Property to format.
        commute_minutes: Commute time in minutes (optional).
        transport_mode: Transport mode used (optional).
        quality_analysis: Quality analysis result (optional).

    Returns:
        Formatted message string with HTML markup.
    """
    # Escape HTML special characters in user-provided content
    title = html.escape(prop.title)
    address = html.escape(prop.address)
    postcode = html.escape(prop.postcode or "")

    # Build the message
    lines = [
        f"<b>{title}</b>",
        "",
        f"<b>Price:</b> £{prop.price_pcm:,}/month",
        f"<b>Bedrooms:</b> {prop.bedrooms}",
        f"<b>Address:</b> {address}",
    ]

    if postcode:
        lines.append(f"<b>Postcode:</b> {postcode}")

    # Add commute info if available
    if commute_minutes is not None:
        mode_str = ""
        if transport_mode:
            mode_map = {
                TransportMode.CYCLING: "by bike",
                TransportMode.PUBLIC_TRANSPORT: "by transit",
                TransportMode.DRIVING: "by car",
                TransportMode.WALKING: "walking",
            }
            mode_str = f" {mode_map.get(transport_mode, '')}"
        lines.append(f"<b>Commute:</b> {commute_minutes} min{mode_str}")

    # Add quality analysis if available
    if quality_analysis:
        lines.append("")

        # Condition concerns banner (if any)
        if quality_analysis.condition_concerns:
            severity = quality_analysis.concern_severity or "unknown"
            lines.append(f"⚠️ <b>CONDITION CONCERNS</b> ({severity})")
            for concern in quality_analysis.condition.maintenance_concerns:
                lines.append(f"  • {html.escape(concern)}")
            lines.append("")

        # Claude's summary
        lines.append(f"<b>Summary:</b> {html.escape(quality_analysis.summary)}")

        # Kitchen info
        lines.append(f"<b>Kitchen:</b> {_format_kitchen_info(quality_analysis)}")

        # Light & space
        lines.append(f"<b>Light/Space:</b> {_format_light_space_info(quality_analysis)}")

        # Living room size
        lines.append(f"<b>Living room:</b> {_format_space_info(quality_analysis)}")

        # Overall condition
        lines.append(f"<b>Condition:</b> {quality_analysis.condition.overall_condition}")

        # Value assessment
        value_info = _format_value_info(quality_analysis)
        if value_info:
            lines.append(f"<b>Value:</b> {value_info}")

    # Add source
    source_names = {
        "openrent": "OpenRent",
        "rightmove": "Rightmove",
        "zoopla": "Zoopla",
        "onthemarket": "OnTheMarket",
    }
    source_name = source_names.get(prop.source.value, prop.source.value)
    lines.append(f"<b>Source:</b> {source_name}")

    # Add link
    lines.append("")
    lines.append(f'<a href="{prop.url}">View Property</a>')

    return "\n".join(lines)


class TelegramNotifier:
    """Send property notifications via Telegram."""

    def __init__(self, *, bot_token: str, chat_id: int) -> None:
        """Initialize the notifier.

        Args:
            bot_token: Telegram bot token from @BotFather.
            chat_id: Chat ID to send notifications to.
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._bot: Bot | None = None

    def _get_bot(self) -> "Bot":
        """Get or create the bot instance."""
        if self._bot is None:
            from aiogram import Bot
            from aiogram.client.default import DefaultBotProperties
            from aiogram.enums import ParseMode

            self._bot = Bot(
                token=self.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
        return self._bot

    async def send_property_notification(
        self,
        prop: Property,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
        quality_analysis: PropertyQualityAnalysis | None = None,
    ) -> bool:
        """Send a property notification.

        Args:
            prop: Property to notify about.
            commute_minutes: Commute time in minutes (optional).
            transport_mode: Transport mode used (optional).
            quality_analysis: Quality analysis result (optional).

        Returns:
            True if notification was sent successfully.
        """
        message = format_property_message(
            prop,
            commute_minutes=commute_minutes,
            transport_mode=transport_mode,
            quality_analysis=quality_analysis,
        )

        try:
            bot = self._get_bot()
            await bot.send_message(
                chat_id=self.chat_id,
                text=message,
                disable_web_page_preview=False,  # Show link preview
            )
            logger.info(
                "notification_sent",
                property_id=prop.unique_id,
                chat_id=self.chat_id,
            )
            return True
        except Exception as e:
            logger.error(
                "notification_failed",
                property_id=prop.unique_id,
                error=str(e),
            )
            return False

    async def send_batch_notifications(
        self,
        properties: list[Property],
        *,
        delay_seconds: float = 1.0,
    ) -> list[bool]:
        """Send notifications for multiple properties.

        Args:
            properties: List of properties to notify about.
            delay_seconds: Delay between messages to avoid rate limiting.

        Returns:
            List of success/failure for each property.
        """
        results = []
        for i, prop in enumerate(properties):
            if i > 0:
                await asyncio.sleep(delay_seconds)
            result = await self.send_property_notification(prop)
            results.append(result)
        return results

    async def send_status_message(self, message: str) -> bool:
        """Send a status message.

        Args:
            message: Status message to send.

        Returns:
            True if message was sent successfully.
        """
        try:
            bot = self._get_bot()
            await bot.send_message(
                chat_id=self.chat_id,
                text=html.escape(message),
            )
            return True
        except Exception as e:
            logger.error("status_message_failed", error=str(e))
            return False

    async def close(self) -> None:
        """Close the bot session."""
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None
