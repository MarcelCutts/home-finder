"""Telegram notification service."""

import asyncio
import html
import random
import urllib.parse
from typing import TYPE_CHECKING, Final

from home_finder.logging import get_logger
from home_finder.models import (
    SOURCE_NAMES,
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    TransportMode,
)

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup

logger = get_logger(__name__)

TRANSPORT_MODE_EMOJI: Final[dict[TransportMode, str]] = {
    TransportMode.CYCLING: "🚴",
    TransportMode.PUBLIC_TRANSPORT: "🚇",
    TransportMode.DRIVING: "🚗",
    TransportMode.WALKING: "🚶",
}


def _html_link(url: str, text: str) -> str:
    """Build an HTML <a> tag with properly escaped URL and text."""
    return f'<a href="{html.escape(str(url), quote=True)}">{html.escape(text)}</a>'


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

    if kitchen.has_dishwasher == "yes":
        items.append("Dishwasher")
    if kitchen.has_washing_machine == "yes":
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
    light_label = "N/A" if light.natural_light == "unknown" else light.natural_light.capitalize()
    parts = [f"Light: {light_label}"]
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


def _format_value_info(analysis: PropertyQualityAnalysis, *, brief: bool = False) -> str | None:
    """Format value assessment for display.

    Args:
        analysis: The quality analysis.
        brief: If True, return only the rating + benchmark note
               (no full quality commentary). Ideal for captions.

    Prefers the quality-adjusted rating from Claude if available,
    falls back to the simple price-based rating.
    """
    value = analysis.value
    if not value:
        return None

    # Emoji based on rating
    emoji_map = {
        "excellent": "📊",
        "good": "📊",
        "fair": "📊",
        "poor": "⚠️",
    }

    if brief:
        # Short form for captions: rating + benchmark note only
        rating = value.quality_adjusted_rating or value.rating
        if not rating:
            return None
        emoji = emoji_map.get(rating, "")
        if value.note:
            return f"{emoji} {rating.capitalize()} value — {html.escape(value.note)}"
        return f"{emoji} {rating.capitalize()} value"

    # Show both benchmark and quality-adjusted value when available
    if value.quality_adjusted_rating and value.note:
        emoji = emoji_map.get(value.quality_adjusted_rating, "")
        parts = [html.escape(value.note)]
        if value.quality_adjusted_note:
            parts.append(html.escape(value.quality_adjusted_note))
        return f"{emoji} {value.quality_adjusted_rating.capitalize()} value — {', '.join(parts)}"

    # Quality-adjusted only (no benchmark data)
    if value.quality_adjusted_rating:
        emoji = emoji_map.get(value.quality_adjusted_rating, "")
        note = html.escape(value.quality_adjusted_note) if value.quality_adjusted_note else ""
        suffix = f" — {note}" if note else ""
        return f"{emoji} {value.quality_adjusted_rating.capitalize()} value{suffix}"

    # Fall back to simple price comparison
    if value.rating:
        emoji = emoji_map.get(value.rating, "")
        return f"{emoji} {value.rating.capitalize()} value — {html.escape(value.note)}"

    return None


def _format_header_lines(
    *,
    title: str,
    price_pcm: int,
    bedrooms: int,
    address: str,
    postcode: str,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    overall_rating: int | None = None,
    price_varies: bool = False,
    min_price: int = 0,
    max_price: int = 0,
    brief: bool = False,
) -> list[str]:
    """Build common header lines: title, rating+price, location, commute.

    Args:
        brief: If True, use postcode only and merge commute into location line.
               Used for captions (1024 char limit). Full mode shows full address.
    """
    lines = [f"<b>{html.escape(title)}</b>", ""]

    # Merge star rating with price/beds on one line for density
    info_parts: list[str] = []
    if overall_rating is not None:
        info_parts.append(_format_star_rating(overall_rating))
    if price_varies:
        info_parts.append(f"£{min_price:,}-£{max_price:,}/mo")
    else:
        info_parts.append(f"£{price_pcm:,}/mo")
    info_parts.append(f"{bedrooms} bed")
    lines.append(" · ".join(info_parts))

    escaped_postcode = html.escape(postcode)

    if brief:
        # Caption mode: postcode + commute on one compact line
        location_parts: list[str] = []
        if escaped_postcode:
            location_parts.append(f"📍 {escaped_postcode}")
        if commute_minutes is not None:
            mode_emoji = TRANSPORT_MODE_EMOJI.get(transport_mode, "") if transport_mode else ""
            if mode_emoji:
                location_parts.append(f"{mode_emoji} {commute_minutes} min")
            else:
                location_parts.append(f"{commute_minutes} min")
        if location_parts:
            lines.append(" · ".join(location_parts))
    else:
        # Full mode: full address, commute on separate line
        escaped_address = html.escape(address)
        location = f"{escaped_address}, {escaped_postcode}" if postcode else escaped_address
        lines.append(f"📍 {location}")
        if commute_minutes is not None:
            mode_emoji = TRANSPORT_MODE_EMOJI.get(transport_mode, "") if transport_mode else ""
            prefix = f"{mode_emoji} " if mode_emoji else ""
            lines.append(f"{prefix}{commute_minutes} min")

    return lines


def _format_bathroom_info(analysis: PropertyQualityAnalysis) -> str | None:
    """Format bathroom analysis for display."""
    if not analysis.bathroom:
        return None
    bathroom = analysis.bathroom
    parts = [bathroom.overall_condition.capitalize()]
    if bathroom.has_bathtub == "yes":
        parts.append("bathtub")
    if bathroom.shower_type and bathroom.shower_type not in ("unknown", "none"):
        parts.append(f"{bathroom.shower_type.replace('_', ' ')} shower")
    if bathroom.is_ensuite == "yes":
        parts.append("ensuite")
    return ", ".join(parts)


def _format_outdoor_info(analysis: PropertyQualityAnalysis) -> str | None:
    """Format outdoor space analysis for display."""
    if not analysis.outdoor_space:
        return None
    os = analysis.outdoor_space
    items = []
    if os.has_balcony:
        items.append("Balcony")
    if os.has_garden:
        items.append("Garden")
    if os.has_terrace:
        items.append("Terrace")
    if os.has_shared_garden:
        items.append("Shared garden")
    return ", ".join(items) if items else None


def _format_listing_extraction_info(analysis: PropertyQualityAnalysis) -> str | None:
    """Format key listing data for display."""
    if not analysis.listing_extraction:
        return None
    le = analysis.listing_extraction
    parts = []
    if le.epc_rating and le.epc_rating != "unknown":
        parts.append(f"EPC {le.epc_rating}")
    if le.service_charge_pcm:
        parts.append(f"+£{le.service_charge_pcm}/mo service charge")
    if le.pets_allowed == "yes":
        parts.append("Pets OK")
    elif le.pets_allowed == "no":
        parts.append("No pets")
    if le.bills_included == "yes":
        parts.append("Bills incl.")
    broadband_labels = {"fttp": "FTTP", "fttc": "FTTC", "cable": "Cable", "standard": "Basic BB"}
    if le.broadband_type and le.broadband_type in broadband_labels:
        parts.append(f"BB: {broadband_labels[le.broadband_type]}")
    return " · ".join(parts) if parts else None


def _format_viewing_notes(analysis: PropertyQualityAnalysis) -> list[str]:
    """Format viewing notes for display."""
    if not analysis.viewing_notes:
        return []
    vn = analysis.viewing_notes
    lines: list[str] = []
    if vn.check_items:
        items = ", ".join(html.escape(c) for c in vn.check_items[:3])
        lines.append(f"👁 <b>Check:</b> {items}")
    if vn.questions_for_agent:
        questions = ", ".join(html.escape(q) for q in vn.questions_for_agent[:3])
        lines.append(f"❓ <b>Ask:</b> {questions}")
    if vn.deal_breaker_tests:
        tests = ", ".join(html.escape(t) for t in vn.deal_breaker_tests[:3])
        lines.append(f"🔍 <b>Test:</b> {tests}")
    return lines


def _format_quality_block(analysis: PropertyQualityAnalysis) -> list[str]:
    """Build full quality analysis lines for text messages.

    Used by format_property_message and format_merged_property_message
    (text fallback). Uses text labels instead of emoji for detail sections
    to reduce emoji density.
    """
    lines: list[str] = []

    if analysis.condition_concerns:
        concerns_text = ", ".join(html.escape(c) for c in analysis.condition.maintenance_concerns)
        lines.append(f"⚠️ Concerns: {concerns_text}")

    lines.append("")
    lines.append(f"<blockquote expandable>{html.escape(analysis.summary)}</blockquote>")

    if analysis.highlights:
        chips = " · ".join(html.escape(h) for h in analysis.highlights[:5])
        lines.append(f"✅ {chips}")
    if analysis.lowlights:
        chips = " · ".join(html.escape(lo) for lo in analysis.lowlights[:5])
        lines.append(f"⛔ {chips}")

    lines.append(f"Kitchen: {_format_kitchen_info(analysis)}")

    bathroom_info = _format_bathroom_info(analysis)
    if bathroom_info:
        lines.append(f"Bathroom: {bathroom_info}")

    lines.append(f"Light: {_format_light_space_info(analysis)}")
    lines.append(f"Space: {_format_space_info(analysis)}")
    lines.append(f"Condition: {analysis.condition.overall_condition}")

    outdoor_info = _format_outdoor_info(analysis)
    if outdoor_info:
        lines.append(f"Outdoor: {outdoor_info}")

    listing_info = _format_listing_extraction_info(analysis)
    if listing_info:
        lines.append(f"Listing: {listing_info}")

    if analysis.listing_red_flags and analysis.listing_red_flags.red_flag_count > 0:
        rf = analysis.listing_red_flags
        rf_parts = []
        if rf.missing_room_photos:
            rf_parts.append(f"No photos of: {', '.join(rf.missing_room_photos)}")
        if rf.too_few_photos:
            rf_parts.append("Too few photos")
        if rf_parts:
            lines.append(f"⚠️ {' · '.join(rf_parts)}")

    viewing_lines = _format_viewing_notes(analysis)
    if viewing_lines:
        lines.append("")
        lines.extend(viewing_lines)

    value_info = _format_value_info(analysis)
    if value_info:
        lines.append(value_info)

    return lines


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
    lines = _format_header_lines(
        title=prop.title,
        price_pcm=prop.price_pcm,
        bedrooms=prop.bedrooms,
        address=prop.address,
        postcode=prop.postcode or "",
        commute_minutes=commute_minutes,
        transport_mode=transport_mode,
        overall_rating=quality_analysis.overall_rating if quality_analysis else None,
    )

    if quality_analysis:
        lines.extend(_format_quality_block(quality_analysis))

    source_name = SOURCE_NAMES.get(prop.source.value, prop.source.value)
    lines.append(f"\n🔗 {source_name}")

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
    lines = _format_header_lines(
        title=prop.title,
        price_pcm=prop.price_pcm,
        bedrooms=prop.bedrooms,
        address=prop.address,
        postcode=prop.postcode or "",
        commute_minutes=commute_minutes,
        transport_mode=transport_mode,
        overall_rating=quality_analysis.overall_rating if quality_analysis else None,
        price_varies=merged.price_varies,
        min_price=merged.min_price,
        max_price=merged.max_price,
    )

    if quality_analysis:
        lines.extend(_format_quality_block(quality_analysis))

    # Image count and floorplan
    if merged.images or merged.floorplan:
        image_parts = []
        if merged.images:
            image_parts.append(f"{len(merged.images)} images")
        if merged.floorplan:
            image_parts.append("floorplan")
        lines.append(f"\n📸 {' + '.join(image_parts)}")

    # Source information
    if len(merged.sources) > 1:
        source_links = []
        for source in merged.sources:
            name = SOURCE_NAMES.get(source.value, source.value)
            url = merged.source_urls.get(source)
            if url:
                source_links.append(_html_link(str(url), name))
            else:
                source_links.append(name)
        lines.append(f"🔗 Listed on: {', '.join(source_links)}")
    else:
        source_name = SOURCE_NAMES.get(prop.source.value, prop.source.value)
        lines.append(f"🔗 {source_name}")

    return "\n".join(lines)


def format_merged_property_caption(
    merged: MergedProperty,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    quality_analysis: PropertyQualityAnalysis | None = None,
) -> str:
    """Format a condensed caption for send_photo (1024 char limit).

    Builds incrementally in priority order, dropping lower-priority sections
    if they would exceed the limit. No lowlights (those are on the web dashboard).
    Source links go in inline keyboard buttons.

    Sections (priority order):
    1. Title + rating/price/beds + location/commute (always)
    2. AI one-liner (if available)
    3. Highlights + critical alerts (if available)
    4. Value note (lowest priority)
    """
    prop = merged.canonical
    header_lines = _format_header_lines(
        title=prop.title,
        price_pcm=prop.price_pcm,
        bedrooms=prop.bedrooms,
        address=prop.address,
        postcode=prop.postcode or "",
        commute_minutes=commute_minutes,
        transport_mode=transport_mode,
        overall_rating=quality_analysis.overall_rating if quality_analysis else None,
        price_varies=merged.price_varies,
        min_price=merged.min_price,
        max_price=merged.max_price,
        brief=True,
    )

    sections: list[str] = ["\n".join(header_lines)]

    if quality_analysis:
        # Section 2: AI one-liner
        display_text = quality_analysis.one_line or quality_analysis.summary
        if display_text:
            sections.append(html.escape(display_text))

        # Section 3: Highlights + critical alerts
        alert_lines: list[str] = []
        if quality_analysis.highlights:
            chips = " · ".join(html.escape(h) for h in quality_analysis.highlights[:4])
            alert_lines.append(f"✅ {chips}")
        if quality_analysis.condition_concerns:
            concerns = ", ".join(
                html.escape(c) for c in quality_analysis.condition.maintenance_concerns
            )
            alert_lines.append(f"⚠️ {concerns}")
        if (
            quality_analysis.listing_extraction
            and quality_analysis.listing_extraction.epc_rating in ("E", "F", "G")
        ):
            alert_lines.append(f"⚠️ EPC {quality_analysis.listing_extraction.epc_rating}")
        if (
            quality_analysis.listing_red_flags
            and quality_analysis.listing_red_flags.red_flag_count >= 2
        ):
            alert_lines.append(f"⚠️ {quality_analysis.listing_red_flags.red_flag_count} red flags")
        if alert_lines:
            sections.append("\n".join(alert_lines))

        # Section 4: Value note (lowest priority)
        value_info = _format_value_info(quality_analysis, brief=True)
        if value_info:
            sections.append(value_info)

    # Assemble incrementally: each section separated by blank line
    result = sections[0]
    for section in sections[1:]:
        candidate = result + "\n\n" + section
        if len(candidate) > 1024:
            break
        result = candidate

    return result


def _format_followup_detail(
    quality_analysis: PropertyQualityAnalysis | None = None,
) -> str:
    """Format minimal follow-up text for album keyboard message.

    Only includes viewing notes (actionable items for viewings).
    All other detail is available on the web dashboard via the Details button.
    """
    if not quality_analysis:
        return ""

    lines = _format_viewing_notes(quality_analysis)
    return "\n".join(lines) if lines else ""


def _build_inline_keyboard(
    merged: MergedProperty,
    web_base_url: str = "",
) -> "InlineKeyboardMarkup":
    """Build an inline keyboard markup with source URL buttons and map button.

    If web_base_url uses HTTPS, the "Details" button opens inside Telegram
    as a Mini App (WebApp). Otherwise it opens in the external browser.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    buttons: list[InlineKeyboardButton] = []

    # Add web dashboard link if configured
    if web_base_url:
        base = web_base_url.rstrip("/")
        detail_url = f"{base}/property/{merged.unique_id}"
        if base.startswith("https://"):
            # Open inside Telegram as a Mini App
            buttons.append(
                InlineKeyboardButton(
                    text="Details",
                    web_app=WebAppInfo(url=detail_url),
                )
            )
        else:
            buttons.append(InlineKeyboardButton(text="Details", url=detail_url))

    for source in merged.sources:
        name = SOURCE_NAMES.get(source.value, source.value)
        url = merged.source_urls.get(source)
        if url:
            buttons.append(InlineKeyboardButton(text=name, url=str(url)))

    # Map button: prefer coordinates, fall back to address search
    prop = merged.canonical
    if prop.latitude is not None and prop.longitude is not None:
        map_url = f"https://www.google.com/maps?q={prop.latitude},{prop.longitude}"
        buttons.append(InlineKeyboardButton(text="Map 📍", url=map_url))
    elif prop.postcode or prop.address:
        query = f"{prop.postcode}, London" if prop.postcode else prop.address
        map_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"
        buttons.append(InlineKeyboardButton(text="Map 📍", url=map_url))

    # Arrange in rows of 2
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_gallery_urls(merged: MergedProperty, max_images: int = 10) -> list[str]:
    """Return gallery image URLs for a merged property.

    Returns up to max_images gallery URLs (Telegram media group limit is 10).
    Prefers enriched gallery images, falls back to canonical image_url.
    """
    urls: list[str] = []
    for img in merged.images:
        if img.image_type == "gallery":
            urls.append(str(img.url))
            if len(urls) >= max_images:
                break
    # Fall back to canonical image_url from search results
    if not urls and merged.canonical.image_url:
        urls.append(str(merged.canonical.image_url))
    return urls


def _get_best_image_url(merged: MergedProperty) -> str | None:
    """Return the best image URL for photo notifications.

    Prefers enriched gallery images (direct CDN URLs) over canonical image_url
    (search result thumbnails), since some sites serve thumbnails that Telegram
    cannot fetch.
    """
    urls = _get_gallery_urls(merged, max_images=1)
    return urls[0] if urls else None


class TelegramNotifier:
    """Send property notifications via Telegram."""

    def __init__(self, *, bot_token: str, chat_id: int, web_base_url: str = "") -> None:
        """Initialize the notifier.

        Args:
            bot_token: Telegram bot token from @BotFather.
            chat_id: Chat ID to send notifications to.
            web_base_url: Base URL for web dashboard (optional).
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.web_base_url = web_base_url.rstrip("/") if web_base_url else ""
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
            # Build inline keyboard via MergedProperty wrapper
            temp_merged = MergedProperty(
                canonical=prop,
                sources=(prop.source,),
                source_urls={prop.source: prop.url},
                min_price=prop.price_pcm,
                max_price=prop.price_pcm,
            )
            keyboard = _build_inline_keyboard(temp_merged, web_base_url=self.web_base_url)

            from aiogram.types import LinkPreviewOptions

            await bot.send_message(
                chat_id=self.chat_id,
                text=message,
                reply_markup=keyboard,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
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
                exc_info=True,
            )
            return False

    async def send_merged_property_notification(
        self,
        merged: MergedProperty,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
        quality_analysis: PropertyQualityAnalysis | None = None,
        _retry_count: int = 0,
    ) -> bool:
        """Send a merged property notification with photo, inline keyboard, and venue.

        Notification format adapts based on quality rating:
        - Rating >= 4: album (if 3+ images) + venue pin (message sprawl justified)
        - Rating < 4 or unknown: single photo + no venue pin (compact triage)

        Automatically retries on Telegram flood control (429) up to 2 times,
        sleeping for the duration Telegram specifies.

        Args:
            merged: Merged property to notify about.
            commute_minutes: Commute time in minutes (optional).
            transport_mode: Transport mode used (optional).
            quality_analysis: Quality analysis result (optional).

        Returns:
            True if notification was sent successfully.
        """
        from aiogram.exceptions import TelegramRetryAfter

        is_high_rated = (
            quality_analysis is not None
            and quality_analysis.overall_rating is not None
            and quality_analysis.overall_rating >= 4
        )

        try:
            bot = self._get_bot()
            keyboard = _build_inline_keyboard(merged, web_base_url=self.web_base_url)
            gallery_urls = _get_gallery_urls(merged)

            sent_photo = False
            if gallery_urls:
                caption = format_merged_property_caption(
                    merged,
                    commute_minutes=commute_minutes,
                    transport_mode=transport_mode,
                    quality_analysis=quality_analysis,
                )
                try:
                    if is_high_rated and len(gallery_urls) >= 3:
                        # High-rated: album with viewing notes follow-up
                        followup_text = _format_followup_detail(
                            quality_analysis=quality_analysis,
                        )
                        sent_photo = await self._send_media_group(
                            gallery_urls,
                            caption=caption,
                            keyboard=keyboard,
                            followup_text=followup_text,
                        )
                    else:
                        # Single hero image with caption + keyboard
                        await bot.send_photo(
                            chat_id=self.chat_id,
                            photo=gallery_urls[0],
                            caption=caption,
                            reply_markup=keyboard,
                        )
                        sent_photo = True
                except TelegramRetryAfter:
                    raise  # Let the outer handler retry the whole notification
                except Exception as photo_err:
                    logger.warning(
                        "send_photo_failed_falling_back_to_text",
                        property_id=merged.unique_id,
                        image_url=gallery_urls[0] if gallery_urls else None,
                        error=str(photo_err),
                        exc_info=True,
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

            # Venue pin only for high-rated properties (reduces message sprawl)
            prop = merged.canonical
            if is_high_rated and prop.latitude is not None and prop.longitude is not None:
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
                has_image=bool(gallery_urls),
            )
            return True

        except TelegramRetryAfter as e:
            if _retry_count >= 2:
                logger.error(
                    "notification_failed_after_retries",
                    property_id=merged.unique_id,
                    retries=_retry_count,
                    error=str(e),
                )
                return False
            logger.info(
                "flood_control_retry",
                property_id=merged.unique_id,
                retry_after=e.retry_after,
                attempt=_retry_count + 1,
            )
            await asyncio.sleep(e.retry_after * random.uniform(1.0, 1.5))
            return await self.send_merged_property_notification(
                merged,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
                quality_analysis=quality_analysis,
                _retry_count=_retry_count + 1,
            )

        except Exception as e:
            logger.error(
                "notification_failed",
                property_id=merged.unique_id,
                error=str(e),
                exc_info=True,
            )
            return False

    async def _send_media_group(
        self,
        image_urls: list[str],
        *,
        caption: str,
        keyboard: "InlineKeyboardMarkup",
        followup_text: str = "",
    ) -> bool:
        """Send a media group (album) of images with caption on the first photo.

        After the album, sends a follow-up message with the full analysis text
        and inline keyboard (Telegram media groups don't support inline keyboards
        directly). Falls back to a minimal pointer if no followup_text provided.

        Args:
            image_urls: List of image URLs (up to 10).
            caption: Caption for the first photo.
            keyboard: Inline keyboard to send in follow-up message.
            followup_text: Full analysis text for follow-up (up to 4096 chars).

        Returns:
            True if the media group was sent successfully.
        """
        from aiogram.utils.media_group import MediaGroupBuilder

        builder = MediaGroupBuilder()
        for i, url in enumerate(image_urls[:10]):
            if i == 0:
                builder.add_photo(media=url, caption=caption, parse_mode="HTML")
            else:
                builder.add_photo(media=url)

        bot = self._get_bot()
        await bot.send_media_group(chat_id=self.chat_id, media=builder.build())

        # Media groups don't support inline keyboards, so send a follow-up
        # message with full analysis + buttons
        text = followup_text if followup_text else "Tap Details for full analysis 👆"
        await bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=keyboard,
        )
        return True

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
                disable_notification=True,
            )
            return True
        except Exception as e:
            logger.error("status_message_failed", error=str(e), exc_info=True)
            return False

    async def close(self) -> None:
        """Close the bot session."""
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None
