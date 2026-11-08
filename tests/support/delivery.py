"""Keep real delivery planning and persistence while faking Bot API receipts and failures."""

from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

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


def mock_durable_notifier(succeeds=True):
    """Stand in for the TelegramNotifier class that a pipeline run constructs.

    Returns ``(proxy, construct)``: patch the pipeline's notifier class with
    ``side_effect=construct``. Each run gets a real notifier, with its delivery
    planning and persistence, whose Bot API calls succeed with fresh message IDs
    or, with ``succeeds=False``, are refused as a blocked chat.
    """
    proxy = MagicMock()
    proxy.send_property_notification = AsyncMock()
    proxy.send_price_drop_notification = AsyncMock()
    proxy.close = AsyncMock()
    proxy.send_merged_property_notification = AsyncMock()
    proxy.deliver_pending = AsyncMock()

    def construct(**kwargs):
        real = TelegramNotifier(**kwargs)
        bot = AsyncMock()
        ids = count(1)

        async def respond(**payload):
            if not succeeds:
                raise TelegramForbiddenError(
                    method=SendMessage(chat_id=real.chat_id, text="fixture"),
                    message="fixture blocked",
                )
            return SimpleNamespace(message_id=next(ids), chat=SimpleNamespace(id=real.chat_id))

        async def album(**payload):
            return [await respond(**payload) for _ in payload["media"]]

        bot.send_photo.side_effect = respond
        bot.send_message.side_effect = respond
        bot.send_venue.side_effect = respond
        bot.send_media_group.side_effect = album
        real._bot = bot
        proxy.send_merged_property_notification.side_effect = real.send_merged_property_notification
        proxy.deliver_pending.side_effect = real.deliver_pending
        proxy.__aenter__ = AsyncMock(return_value=proxy)

        async def close(*args):
            await proxy.close()

        proxy.__aexit__ = close
        return proxy

    return proxy, construct
