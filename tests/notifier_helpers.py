"""Keep real delivery planning/persistence in pipeline tests; fake only the Bot API."""

from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from home_finder.notifiers.telegram import TelegramNotifier


def mock_durable_notifier(succeeds=True):
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
