"""Plan once, acknowledge each Bot API request, and stop on ambiguous outcomes."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardMarkup

from home_finder.db.delivery import DeliveryRepository, DeliveryStateError
from home_finder.filters.fit_score import compute_fit_score
from home_finder.logging import get_logger
from home_finder.models.delivery import DeliveryPart, DeliveryPlan, DiscoverySnapshot
from home_finder.notifiers.telegram import (
    _build_inline_keyboard,
    _format_followup_detail,
    _get_gallery_urls,
    _resolve_photo,
    format_merged_property_caption,
    format_merged_property_message,
)
from home_finder.utils.async_tasks import finish_before_cancelling

if TYPE_CHECKING:
    from home_finder.notifiers.telegram import TelegramNotifier

logger = get_logger(__name__)


def _photo(notifier: "TelegramNotifier", url: str, uid: str, album: bool) -> dict[str, str]:
    if not notifier.data_dir:
        return {"url": url}
    item = _resolve_photo(url, uid, notifier.data_dir, prefer_thumbnail=not album)
    if isinstance(item, FSInputFile):
        path = Path(item.path).resolve(strict=True)
        relative = path.relative_to(Path(notifier.data_dir).resolve())
        return {"file": str(relative), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {"url": item}


def discovery_plan(notifier: "TelegramNotifier", snapshot: DiscoverySnapshot) -> DeliveryPlan:
    merged = snapshot.merged
    quality = snapshot.quality_analysis
    fit = compute_fit_score(quality, merged.canonical.bedrooms, postcode=merged.canonical.postcode)
    high = fit is not None and fit >= 70
    keyboard = _build_inline_keyboard(merged, web_base_url=notifier.web_base_url).model_dump(
        mode="json", exclude_none=True
    )
    urls = _get_gallery_urls(merged)
    args = {
        "commute_minutes": snapshot.commute_minutes,
        "transport_mode": snapshot.transport_mode,
        "quality_analysis": quality,
    }
    parts = []
    if urls:
        caption = format_merged_property_caption(merged, **args)  # type: ignore[arg-type]
        album = high and len(urls) >= 3
        photos = [
            _photo(notifier, url, merged.unique_id, album) for url in (urls if album else urls[:1])
        ]
        if album:
            parts.append(
                DeliveryPart(method="album", payload={"photos": photos, "caption": caption})
            )
            parts.append(
                DeliveryPart(
                    method="message",
                    payload={
                        "text": _format_followup_detail(quality_analysis=quality)
                        or "Tap Details for full analysis 👆",
                        "reply_markup": keyboard,
                    },
                )
            )
        else:
            parts.append(
                DeliveryPart(
                    method="photo",
                    payload={"photo": photos[0], "caption": caption, "reply_markup": keyboard},
                )
            )
    else:
        parts.append(
            DeliveryPart(
                method="message",
                payload={
                    "text": format_merged_property_message(merged, **args),  # type: ignore[arg-type]
                    "reply_markup": keyboard,
                },
            )
        )
    prop = merged.canonical
    if high and prop.latitude is not None and prop.longitude is not None:
        parts.append(
            DeliveryPart(
                method="venue",
                payload={
                    "latitude": prop.latitude,
                    "longitude": prop.longitude,
                    "title": prop.address,
                    "address": prop.postcode or prop.address,
                },
            )
        )
    return DeliveryPlan(parts=tuple(parts))


def _resolve_asset(notifier: "TelegramNotifier", media: dict[str, str]) -> BufferedInputFile | str:
    if "url" in media:
        return media["url"]
    root = Path(notifier.data_dir).resolve()
    path = (root / media["file"]).resolve(strict=True)
    if not path.is_relative_to(root):
        raise DeliveryStateError("Invalid delivery asset path")
    data = path.read_bytes()
    if len(data) > 10 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != media["sha256"]:
        raise DeliveryStateError("Changed or oversized delivery asset")
    # Send these verified bytes, not a path that can change between check and upload.
    return BufferedInputFile(data, filename=path.name)


def request_payload(notifier: "TelegramNotifier", part: DeliveryPart) -> dict[str, Any]:
    from aiogram.utils.media_group import MediaGroupBuilder

    payload = dict(part.payload)
    if "reply_markup" in payload:
        payload["reply_markup"] = InlineKeyboardMarkup.model_validate(payload["reply_markup"])
    if part.method == "photo":
        payload["photo"] = _resolve_asset(notifier, payload["photo"])
    elif part.method == "album":
        builder = MediaGroupBuilder()
        for i, photo in enumerate(payload.pop("photos")):
            builder.add_photo(
                media=_resolve_asset(notifier, photo),
                caption=payload["caption"] if i == 0 else None,
                parse_mode="HTML",
            )
        payload = {"media": list(builder.build())}
    return payload


async def send_part(
    notifier: "TelegramNotifier", part: DeliveryPart, payload: dict[str, Any]
) -> Any:
    bot = notifier._get_bot()
    if part.method == "album":
        return await bot.send_media_group(chat_id=notifier.chat_id, **payload)
    if part.method == "photo":
        return await bot.send_photo(chat_id=notifier.chat_id, **payload)
    if part.method == "venue":
        return await bot.send_venue(chat_id=notifier.chat_id, **payload)
    return await bot.send_message(chat_id=notifier.chat_id, **payload)


def message_ids(result: Any, part: DeliveryPart, chat_id: int) -> list[int]:
    messages = result if isinstance(result, list) else [result]
    ids = [m.message_id for m in messages]
    if (
        len(messages) != part.receipt_count
        or len(set(ids)) != len(ids)
        or any(type(i) is not int or i <= 0 for i in ids)
        or any(m.chat.id != chat_id for m in messages)
    ):
        raise DeliveryStateError("Invalid Telegram receipt")
    return ids


async def deliver(notifier: "TelegramNotifier", repo: DeliveryRepository, key: str) -> bool:
    # Token rotation for the same bot is harmless; another bot/chat must not inherit receipts.
    recipient = f"{notifier.bot_token.split(':', 1)[0]}:{notifier.chat_id}"
    if await repo.suppress_if_stopped(key):
        return False
    await repo.refresh_missing_images(key)
    row = await repo.get(key)
    if row is None or row["state"] in {"review", "blocked", "uncertain", "sending", "suppressed"}:
        return False
    if row["state"] == "complete":
        return False  # already counted/delivered, never replay or count twice
    log = logger.bind(delivery_key=key, property_id=row["property_id"])
    try:
        if row["state"] == "queued":
            if row["kind"] == "discovery":
                snapshot = DiscoverySnapshot.model_validate_json(row["snapshot_json"])
                if not _get_gallery_urls(snapshot.merged):
                    await repo.defer(key, "no_images")
                    log.info("notification_delivery_deferred", reason="no_images")
                    return False
                plan = discovery_plan(notifier, snapshot)
            else:
                drop = json.loads(row["snapshot_json"])
                first = datetime.fromisoformat(drop.pop("first_seen"))
                if first.tzinfo is None:
                    first = first.replace(tzinfo=UTC)
                drop["days_listed"] = max(0, (datetime.now(UTC) - first).days)
                plan = notifier.price_drop_plan(**drop)
            await repo.prepare(key, recipient, plan)
            row = await repo.get(key)
            assert row is not None
            log.info("notification_delivery_planned", parts=len(plan.parts))
        if row["recipient"] != recipient:
            await repo.defer(key, "recipient_changed", blocked=True)
            log.warning("notification_delivery_deferred", reason="recipient_changed")
            return False
        plan = repo.plan(row)
        for index in range(row["next_part"], len(plan.parts)):
            part = plan.parts[index]
            # Resolve assets before dispatch; an absent/changed file never changes the plan.
            payload = request_payload(notifier, part)
            if not await repo.claim(key, recipient, index):
                return False
            log.info("notification_part_dispatched", part=index, method=part.method)
            started = time.monotonic()
            try:
                result = await send_part(notifier, part, payload)
                ids = message_ids(result, part, notifier.chat_id)
            except TelegramRetryAfter as exc:
                await finish_before_cancelling(
                    repo.fail(key, index, "waiting", "rate_limited", retry_after=exc.retry_after)
                )
                log.info("notification_part_waiting", part=index, retry_after=exc.retry_after)
                return False
            except (
                TelegramBadRequest,
                TelegramForbiddenError,
                TelegramNotFound,
                TelegramUnauthorizedError,
            ) as exc:
                await finish_before_cancelling(repo.fail(key, index, "blocked", type(exc).__name__))
                log.warning("notification_part_blocked", part=index, reason=type(exc).__name__)
                return False
            except BaseException as exc:
                await finish_before_cancelling(
                    repo.fail(key, index, "uncertain", type(exc).__name__)
                )
                log.warning("notification_part_uncertain", part=index, reason=type(exc).__name__)
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                return False
            # Crash/DB failure here leaves 'sending', never automatic redispatch.
            await finish_before_cancelling(repo.acknowledge(key, index, ids))
            log.info(
                "notification_part_acknowledged",
                part=index,
                message_ids=ids,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
        log.info("notification_delivery_complete")
        return True
    except Exception as exc:
        # Fail closed without logging bodies, credentials or third-party exception text.
        try:
            await repo.defer(key, type(exc).__name__, blocked=True)
        except Exception:
            log.error("notification_delivery_state_unavailable")
        log.warning("notification_delivery_deferred", reason=type(exc).__name__)
        return False
