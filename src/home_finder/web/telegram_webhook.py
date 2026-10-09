"""Telegram webhook endpoint for inline keyboard callbacks."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.storage import DuplicateTelegramUpdateError
from home_finder.logging import get_logger
from home_finder.models import UserStatus
from home_finder.models.home_identity import is_callback_target
from home_finder.web.access import valid_webhook_secret

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
    settings: Settings = request.app.state.settings
    secret = settings.telegram_webhook_secret
    if not secret:
        return JSONResponse({"error": "webhook not configured"}, status_code=404)

    # Validate secret token
    if not valid_webhook_secret(request, settings):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError, RecursionError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid update"}, status_code=400)

    callback_query = body.get("callback_query")
    if not isinstance(callback_query, dict):
        # Not a callback_query update — acknowledge and ignore
        return JSONResponse({"ok": True})

    sender = callback_query.get("from")
    message = callback_query.get("message")
    chat = message.get("chat") if isinstance(message, dict) else None
    owner_id = settings.telegram_owner_user_id
    if owner_id is None and settings.telegram_chat_id > 0:
        owner_id = settings.telegram_chat_id
    if (
        owner_id is None
        or not settings.telegram_chat_id
        or not isinstance(sender, dict)
        or type(sender.get("id")) is not int
        or sender["id"] != owner_id
        or not isinstance(chat, dict)
        or type(chat.get("id")) is not int
        or chat["id"] != settings.telegram_chat_id
    ):
        return JSONResponse({"ok": True})

    callback_data = callback_query.get("data")
    callback_id = callback_query.get("id")
    update_id = body.get("update_id")
    if (
        not isinstance(callback_data, str)
        or not callback_data.isascii()
        or not 1 <= len(callback_data) <= 64
        or not isinstance(callback_id, str)
        or not 1 <= len(callback_id) <= 256
        or type(update_id) is not int
        or not 0 <= update_id < 2**63
    ):
        return JSONResponse({"ok": True})

    # Parse st:{uid}:{status}
    parts = callback_data.split(":")
    if len(parts) < 3 or parts[0] != "st":
        await _answer_callback(settings, callback_id, "Unknown action")
        return JSONResponse({"ok": True})

    # uid may contain colons (e.g. "openrent:12345"), so rejoin middle parts
    status_str = parts[-1]
    uid = ":".join(parts[1:-1])
    if not is_callback_target(uid):
        await _answer_callback(settings, callback_id, "Invalid property")
        return JSONResponse({"ok": True})

    target_status = _CALLBACK_STATUSES.get(status_str)
    if not target_status:
        await _answer_callback(settings, callback_id, "Unknown status")
        return JSONResponse({"ok": True})

    storage: PropertyStorage = request.app.state.storage
    try:
        prev = await storage.update_user_status(
            uid, target_status, source="telegram", telegram_update_id=update_id
        )
    except DuplicateTelegramUpdateError:
        # Telegram retries deliveries. Acknowledging a committed update must not
        # overwrite a later owner decision or append another status event.
        return JSONResponse({"ok": True})

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
