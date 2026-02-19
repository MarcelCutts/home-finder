"""Telegram webhook endpoint for inline keyboard callbacks."""

import hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import get_logger
from home_finder.models import UserStatus

logger = get_logger(__name__)

router = APIRouter()

# Status values the callback buttons can set
_CALLBACK_STATUSES: dict[str, UserStatus] = {
    "interested": UserStatus.INTERESTED,
    "archived": UserStatus.ARCHIVED,
}


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    """Handle incoming Telegram webhook updates.

    Validates the secret token header, parses callback_query updates
    with ``st:{uid}:{status}`` data, and updates the DB.
    """
    settings = request.app.state.settings
    secret = settings.telegram_webhook_secret
    if not secret:
        return JSONResponse({"error": "webhook not configured"}, status_code=404)

    # Validate secret token
    header_token = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(header_token, secret):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()

    callback_query = body.get("callback_query")
    if not callback_query:
        # Not a callback_query update — acknowledge and ignore
        return JSONResponse({"ok": True})

    callback_data: str = callback_query.get("data", "")
    callback_id: str = callback_query.get("id", "")

    # Parse st:{uid}:{status}
    parts = callback_data.split(":")
    if len(parts) < 3 or parts[0] != "st":
        await _answer_callback(settings, callback_id, "Unknown action")
        return JSONResponse({"ok": True})

    # uid may contain colons (e.g. "openrent:12345"), so rejoin middle parts
    status_str = parts[-1]
    uid = ":".join(parts[1:-1])

    target_status = _CALLBACK_STATUSES.get(status_str)
    if not target_status:
        await _answer_callback(settings, callback_id, f"Unknown status: {status_str}")
        return JSONResponse({"ok": True})

    storage: PropertyStorage = request.app.state.storage
    prev = await storage.update_user_status(uid, target_status, source="telegram")

    if prev is None:
        await _answer_callback(settings, callback_id, "Property not found")
    else:
        label = target_status.value.replace("_", " ").capitalize()
        await _answer_callback(settings, callback_id, f"Marked as {label}")
        logger.info(
            "telegram_status_update",
            unique_id=uid,
            from_status=prev,
            to_status=target_status.value,
        )

    return JSONResponse({"ok": True})


async def _answer_callback(settings: Settings, callback_id: str, text: str) -> None:
    """Answer a Telegram callback query to dismiss the loading spinner."""
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        token = settings.telegram_bot_token.get_secret_value()
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await bot.answer_callback_query(callback_id, text=text)
        finally:
            await bot.session.close()
    except Exception:
        logger.warning("answer_callback_failed", callback_id=callback_id, exc_info=True)
