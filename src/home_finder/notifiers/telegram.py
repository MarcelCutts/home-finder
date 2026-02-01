"""Telegram notification service."""

import asyncio
import html
from typing import TYPE_CHECKING

from home_finder.logging import get_logger
from home_finder.models import Property, TransportMode

if TYPE_CHECKING:
    from aiogram import Bot

logger = get_logger(__name__)


def format_property_message(
    prop: Property,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
) -> str:
    """Format a property as a Telegram message.

    Args:
        prop: Property to format.
        commute_minutes: Commute time in minutes (optional).
        transport_mode: Transport mode used (optional).

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
    ) -> bool:
        """Send a property notification.

        Args:
            prop: Property to notify about.
            commute_minutes: Commute time in minutes (optional).
            transport_mode: Transport mode used (optional).

        Returns:
            True if notification was sent successfully.
        """
        message = format_property_message(
            prop,
            commute_minutes=commute_minutes,
            transport_mode=transport_mode,
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
