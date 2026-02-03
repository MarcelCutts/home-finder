"""Telegram notification service."""

import asyncio
import html
from typing import TYPE_CHECKING

from home_finder.filters.quality import PropertyQualityAnalysis
from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, TransportMode

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup

logger = get_logger(__name__)

SOURCE_NAMES: dict[str, str] = {
    "openrent": "OpenRent",
    "rightmove": "Rightmove",
    "zoopla": "Zoopla",
    "onthemarket": "OnTheMarket",
}


def _format_star_rating(rating: int) -> str:
    """Return filled + empty stars for a 1-5 rating."""
    filled = min(max(rating, 1), 5)
    return "⭐" * filled + "☆" * (5 - filled)


def _format_kitchen_info(analysis: PropertyQualityAnalysis) -> str:
    """Format kitchen analysis for display."""
    kitchen = analysis.kitchen
    items = []

    if kitchen.hob_type == "gas":
        items.append("Gas hob")
    elif kitchen.hob_type in ("electric", "induction"):
        items.append(f"{kitchen.hob_type.capitalize()} hob")

    if kitchen.has_dishwasher is True:
        items.append("Dishwasher")
    if kitchen.has_washing_machine is True:
        items.append("Washer")

    quality_str = ""
    if kitchen.overall_quality and kitchen.overall_quality != "unknown":
        quality_str = f" ({kitchen.overall_quality})"

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

    # Show both benchmark and quality-adjusted value when available
    if value.quality_adjusted_rating and value.note:
        emoji = emoji_map.get(value.quality_adjusted_rating, "")
        parts = [value.note]
        if value.quality_adjusted_note:
            parts.append(value.quality_adjusted_note)
        return f"{emoji} {value.quality_adjusted_rating.capitalize()} value — {', '.join(parts)}"

    # Quality-adjusted only (no benchmark data)
    if value.quality_adjusted_rating:
        emoji = emoji_map.get(value.quality_adjusted_rating, "")
        note = value.quality_adjusted_note or ""
        suffix = f" — {note}" if note else ""
        return f"{emoji} {value.quality_adjusted_rating.capitalize()} value{suffix}"

    # Fall back to simple price comparison
    if value.rating:
        emoji = emoji_map.get(value.rating, "")
        return f"{emoji} {value.rating.capitalize()} value — {value.note}"

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

        # Star rating
        if quality_analysis.overall_rating is not None:
            lines.append(f"<b>Rating:</b> {_format_star_rating(quality_analysis.overall_rating)}")

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
    source_name = SOURCE_NAMES.get(prop.source.value, prop.source.value)
    lines.append(f"<b>Source:</b> {source_name}")

    # Add link
    lines.append("")
    lines.append(f'<a href="{prop.url}">View Property</a>')

    return "\n".join(lines)


def format_merged_property_message(
    merged: MergedProperty,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    quality_analysis: PropertyQualityAnalysis | None = None,
) -> str:
    """Format a merged property as a Telegram message.

    Shows multi-source information when property is listed on multiple platforms.

    Args:
        merged: Merged property to format.
        commute_minutes: Commute time in minutes (optional).
        transport_mode: Transport mode used (optional).
        quality_analysis: Quality analysis result (optional).

    Returns:
        Formatted message string with HTML markup.
    """
    prop = merged.canonical
    title = html.escape(prop.title)
    address = html.escape(prop.address)
    postcode = html.escape(prop.postcode or "")

    # Build the message
    lines = [
        f"<b>{title}</b>",
        "",
    ]

    # Show price range if varies across platforms
    if merged.price_varies:
        lines.append(f"<b>Price:</b> £{merged.min_price:,}-£{merged.max_price:,}/month")
    else:
        lines.append(f"<b>Price:</b> £{prop.price_pcm:,}/month")

    lines.append(f"<b>Bedrooms:</b> {prop.bedrooms}")
    lines.append(f"<b>Address:</b> {address}")

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

        # Star rating
        if quality_analysis.overall_rating is not None:
            lines.append(f"<b>Rating:</b> {_format_star_rating(quality_analysis.overall_rating)}")

        # Condition concerns banner
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

    # Show image count and floorplan availability
    if merged.images or merged.floorplan:
        image_parts = []
        if merged.images:
            image_parts.append(f"{len(merged.images)} images")
        if merged.floorplan:
            image_parts.append("floorplan")
        lines.append(f"<b>Photos:</b> {' + '.join(image_parts)}")

    # Source information
    if len(merged.sources) > 1:
        # Multiple sources - show "Listed on: X, Y" with links
        source_links = []
        for source in merged.sources:
            name = SOURCE_NAMES.get(source.value, source.value)
            url = merged.source_urls.get(source)
            if url:
                source_links.append(f'<a href="{url}">{name}</a>')
            else:
                source_links.append(name)
        lines.append(f"<b>Listed on:</b> {', '.join(source_links)}")
    else:
        # Single source
        source_name = SOURCE_NAMES.get(prop.source.value, prop.source.value)
        lines.append(f"<b>Source:</b> {source_name}")
        lines.append("")
        lines.append(f'<a href="{prop.url}">View Property</a>')

    return "\n".join(lines)


def format_merged_property_caption(
    merged: MergedProperty,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    quality_analysis: PropertyQualityAnalysis | None = None,
) -> str:
    """Format a condensed caption for send_photo (1024 char limit).

    Includes title, star rating, price, beds, address, commute, summary, value.
    Source links are omitted (they go in inline keyboard buttons).
    """
    prop = merged.canonical
    title = html.escape(prop.title)
    address = html.escape(prop.address)

    lines = [f"<b>{title}</b>", ""]

    # Star rating
    if quality_analysis and quality_analysis.overall_rating is not None:
        lines.append(_format_star_rating(quality_analysis.overall_rating))

    # Price
    if merged.price_varies:
        lines.append(f"💷 £{merged.min_price:,}-£{merged.max_price:,}/mo")
    else:
        lines.append(f"💷 £{prop.price_pcm:,}/mo")

    lines.append(f"🛏 {prop.bedrooms} bed | 📍 {address}")

    # Commute
    if commute_minutes is not None:
        mode_str = ""
        if transport_mode:
            mode_map = {
                TransportMode.CYCLING: "🚴",
                TransportMode.PUBLIC_TRANSPORT: "🚇",
                TransportMode.DRIVING: "🚗",
                TransportMode.WALKING: "🚶",
            }
            mode_str = f"{mode_map.get(transport_mode, '')} "
        lines.append(f"{mode_str}{commute_minutes} min commute")

    # Quality summary
    if quality_analysis:
        lines.append("")
        lines.append(html.escape(quality_analysis.summary))

        value_info = _format_value_info(quality_analysis)
        if value_info:
            lines.append(f"Value: {value_info}")

    caption = "\n".join(lines)
    # Telegram caption limit is 1024 chars
    if len(caption) > 1024:
        caption = caption[:1021] + "..."
    return caption


def _build_inline_keyboard(
    merged: MergedProperty,
) -> "InlineKeyboardMarkup":
    """Build an inline keyboard markup with source URL buttons and map button.

    Returns a dict suitable for passing as reply_markup to aiogram.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    buttons: list[InlineKeyboardButton] = []

    for source in merged.sources:
        name = SOURCE_NAMES.get(source.value, source.value)
        url = merged.source_urls.get(source)
        if url:
            buttons.append(InlineKeyboardButton(text=name, url=str(url)))

    # Map button using coordinates
    prop = merged.canonical
    if prop.latitude is not None and prop.longitude is not None:
        map_url = f"https://www.google.com/maps?q={prop.latitude},{prop.longitude}"
        buttons.append(InlineKeyboardButton(text="Map 📍", url=map_url))

    # Arrange in rows of 2
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_best_image_url(merged: MergedProperty) -> str | None:
    """Return the best image URL for photo notifications.

    Prefers enriched gallery images (direct CDN URLs) over canonical image_url
    (search result thumbnails), since some sites serve thumbnails that Telegram
    cannot fetch.
    """
    # Prefer enriched gallery images (reliable CDN URLs)
    for img in merged.images:
        if img.image_type == "gallery":
            return str(img.url)
    # Fall back to canonical image_url from search results
    if merged.canonical.image_url:
        return str(merged.canonical.image_url)
    return None


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
            # Build inline keyboard with source link + map
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            buttons: list[InlineKeyboardButton] = [
                InlineKeyboardButton(
                    text=SOURCE_NAMES.get(prop.source.value, prop.source.value),
                    url=str(prop.url),
                )
            ]
            if prop.latitude is not None and prop.longitude is not None:
                map_url = f"https://www.google.com/maps?q={prop.latitude},{prop.longitude}"
                buttons.append(InlineKeyboardButton(text="Map 📍", url=map_url))

            keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

            await bot.send_message(
                chat_id=self.chat_id,
                text=message,
                reply_markup=keyboard,
                disable_web_page_preview=True,
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

    async def send_merged_property_notification(
        self,
        merged: MergedProperty,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
        quality_analysis: PropertyQualityAnalysis | None = None,
    ) -> bool:
        """Send a merged property notification with photo, inline keyboard, and venue.

        If an image is available, sends as a photo with condensed caption and
        inline keyboard buttons. Otherwise falls back to a text message.
        If coordinates are available, follows up with a venue pin.

        Args:
            merged: Merged property to notify about.
            commute_minutes: Commute time in minutes (optional).
            transport_mode: Transport mode used (optional).
            quality_analysis: Quality analysis result (optional).

        Returns:
            True if notification was sent successfully.
        """
        try:
            bot = self._get_bot()
            keyboard = _build_inline_keyboard(merged)
            image_url = _get_best_image_url(merged)

            sent_photo = False
            if image_url:
                caption = format_merged_property_caption(
                    merged,
                    commute_minutes=commute_minutes,
                    transport_mode=transport_mode,
                    quality_analysis=quality_analysis,
                )
                try:
                    await bot.send_photo(
                        chat_id=self.chat_id,
                        photo=image_url,
                        caption=caption,
                        reply_markup=keyboard,
                    )
                    sent_photo = True
                except Exception as photo_err:
                    logger.warning(
                        "send_photo_failed_falling_back_to_text",
                        property_id=merged.unique_id,
                        image_url=image_url,
                        error=str(photo_err),
                    )

            if not sent_photo:
                message = format_merged_property_message(
                    merged,
                    commute_minutes=commute_minutes,
                    transport_mode=transport_mode,
                    quality_analysis=quality_analysis,
                )
                await bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    reply_markup=keyboard,
                )

            # Send venue pin if coordinates available
            prop = merged.canonical
            if prop.latitude is not None and prop.longitude is not None:
                await bot.send_venue(
                    chat_id=self.chat_id,
                    latitude=prop.latitude,
                    longitude=prop.longitude,
                    title=prop.address,
                    address=prop.postcode or prop.address,
                )

            logger.info(
                "notification_sent",
                property_id=merged.unique_id,
                chat_id=self.chat_id,
                sources=[s.value for s in merged.sources],
                has_image=image_url is not None,
            )
            return True
        except Exception as e:
            logger.error(
                "notification_failed",
                property_id=merged.unique_id,
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
