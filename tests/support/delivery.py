"""Keep real delivery persistence while faking Bot API receipts and failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from home_finder.notifiers.telegram import TelegramNotifier


def receipt(i):
    return SimpleNamespace(message_id=i, chat=SimpleNamespace(id=123))


def notifier(storage, *, data_dir="", chat_id=123):
    bot = AsyncMock()
    bot.send_photo.return_value = receipt(11)
    bot.send_media_group.return_value = [receipt(i) for i in [11, 12, 13]]
    bot.send_message.return_value = receipt(14)
    bot.send_venue.return_value = receipt(15)
    sender = TelegramNotifier(
        bot_token="123456:fixture",
        chat_id=chat_id,
        data_dir=data_dir,
        delivery_repository=storage.deliveries,
    )
    sender._bot = bot
    return sender, bot


async def status(storage, uid):
    async with storage._read_connection() as conn:
        cur = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id=?", (uid,)
        )
        return (await cur.fetchone())[0]
