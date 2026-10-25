"""Offline Bot API fault tests with real delivery transactions and restarts."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramServerError
from aiogram.methods import SendMessage

from home_finder.db import PropertyStorage
from home_finder.models.delivery import DiscoverySnapshot
from home_finder.models.notifications import NotificationPolicy
from home_finder.notifiers.delivery import deliver, discovery_plan
from home_finder.notifiers.telegram import TelegramNotifier
from home_finder.pipeline.delivery_admin import administer, read_report
from home_finder.pipeline.run_lock import runner_lock

METHOD = SendMessage(chat_id=123, text="fixture")


@pytest.fixture
def delivery_analysis(sample_quality_analysis):
    from home_finder.models import BedroomAnalysis, FlooringNoiseAnalysis

    return sample_quality_analysis.model_copy(
        update={
            "space": sample_quality_analysis.space.model_copy(update={"hosting_layout": "good"}),
            "bedroom": BedroomAnalysis(
                primary_is_double="yes", office_separation="dedicated_room", can_fit_desk="yes"
            ),
            "flooring_noise": FlooringNoiseAnalysis(
                hosting_noise_risk="low", has_double_glazing="yes"
            ),
        }
    )


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


async def seed(storage, merged, analysis):
    await storage.processing.save_pre_analysis_properties([merged], {})
    await storage.save_property_images(merged.unique_id, list(merged.images))
    # Transport scenarios explicitly admit any rated fixture. Selection has its own suite.
    await storage.processing.complete_analysis(
        merged.unique_id, analysis, notification_policy=NotificationPolicy(min_fit_score=0)
    )
    return f"discovery:{merged.unique_id}"


async def status(storage, uid):
    async with storage._read_connection() as conn:
        cur = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id=?", (uid,)
        )
        return (await cur.fetchone())[0]


async def test_photo_receipt_survives_venue_rate_limit_and_restart(
    tmp_path, enriched_merged_property, delivery_analysis
):
    merged = enriched_merged_property.model_copy(
        update={"images": enriched_merged_property.images[:1]}
    )
    path = str(tmp_path / "restart.db")
    now = datetime(2026, 10, 10, tzinfo=UTC)
    async with PropertyStorage(path) as storage:
        key = await seed(storage, merged, delivery_analysis)
        storage.deliveries._clock = lambda: now
        sender, bot = notifier(storage)
        bot.send_venue.side_effect = TelegramRetryAfter(
            method=METHOD, message="flood", retry_after=60
        )
        assert not await deliver(sender, storage.deliveries, key)
        row = await storage.deliveries.get(key)
        assert row["state"] == "waiting" and row["next_part"] == 1
        assert json.loads(row["receipts_json"]) == [[11]]
        saved_plan = row["plan_json"]
        assert await status(storage, merged.unique_id) == "pending"
    async with PropertyStorage(path) as storage:
        storage.deliveries._clock = lambda: now + timedelta(seconds=59)
        await storage.deliveries.recover()
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, key)
        bot.send_venue.assert_not_called()
        storage.deliveries._clock = lambda: now + timedelta(seconds=61)
        assert await deliver(sender, storage.deliveries, key)
        bot.send_photo.assert_not_called()
        bot.send_venue.assert_called_once()
        assert await status(storage, merged.unique_id) == "sent"
        assert (await storage.deliveries.get(key))["plan_json"] == saved_plan
        assert not await deliver(sender, storage.deliveries, key)
        bot.send_venue.assert_called_once()


@pytest.mark.parametrize("later_prices", [(), (1500,), (1700,), (1900,), (1700, 1650)])
async def test_price_drop_during_partial_discovery_survives_restart(
    tmp_path, enriched_merged_property, delivery_analysis, later_prices
):
    merged = enriched_merged_property.model_copy(
        update={"images": enriched_merged_property.images[:1]}
    )
    uid = merged.unique_id
    path = str(tmp_path / "partial-discovery-price.db")
    now = datetime(2026, 10, 10, tzinfo=UTC)
    async with PropertyStorage(path) as storage:
        key = await seed(storage, merged, delivery_analysis)
        storage.deliveries._clock = lambda: now
        sender, bot = notifier(storage)
        bot.send_venue.side_effect = TelegramRetryAfter(
            method=METHOD, message="flood", retry_after=60
        )
        assert not await deliver(sender, storage.deliveries, key)
        frozen = await storage.deliveries.get(key)
        assert frozen["state"] == "waiting" and frozen["next_part"] == 1
        assert json.loads(frozen["receipts_json"]) == [[11]]
        assert "1,800" in bot.send_photo.call_args.kwargs["caption"]

        assert await storage.detect_and_record_price_change(uid, 1600) == -200
        assert await storage.detect_and_record_price_change(uid, 1600) is None
        price_keys = [k for k in await storage.deliveries.pending() if k.startswith("price:")]
        assert len(price_keys) == 1
        price_key = price_keys[0]
        assert (await storage.deliveries.get(price_key))["state"] == "queued"
        assert await storage.deliveries.get(key) == frozen

        # Expire the flood delay: discovery completion must independently gate price dispatch.
        storage.deliveries._clock = lambda: now + timedelta(seconds=61)
        assert not await deliver(sender, storage.deliveries, price_key)
        bot.send_message.assert_not_called()
        assert await status(storage, uid) == "pending"

    async with PropertyStorage(path) as storage:
        storage.deliveries._clock = lambda: now + timedelta(seconds=61)
        await storage.deliveries.recover()
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, price_key)
        assert not bot.mock_calls
        assert "review" not in (await storage.deliveries.report())["counts"]

        async def venue_with_new_prices(**kwargs):
            # New observations can arrive while the final discovery part is in flight.
            for price in later_prices:
                await storage.detect_and_record_price_change(uid, price)
            return receipt(15)

        bot.send_venue.side_effect = venue_with_new_prices
        assert await deliver(sender, storage.deliveries, key)
        bot.send_photo.assert_not_called()
        bot.send_venue.assert_called_once()
        bot.send_message.assert_not_called()
        completed = await storage.deliveries.get(key)
        assert completed["state"] == "complete"
        assert completed["snapshot_json"] == frozen["snapshot_json"]
        assert completed["plan_json"] == frozen["plan_json"]
        assert completed["plan_sha256"] == frozen["plan_sha256"]
        assert json.loads(completed["receipts_json"]) == [[11], [15]]
        assert await status(storage, uid) == "sent"

    prices = (1800, 1600, *later_prices)
    expect_drop = prices[-1] < prices[-2]
    async with PropertyStorage(path) as storage:
        storage.deliveries._clock = lambda: now + timedelta(seconds=61)
        await storage.deliveries.recover()
        assert "review" not in (await storage.deliveries.report())["counts"]
        pending = await storage.deliveries.pending()
        assert len(pending) == int(expect_drop)
        drops = await storage.get_unsent_price_drops()
        assert len(drops) == int(expect_drop)
        if expect_drop:
            assert drops[0]["new_price"] == prices[-1]
        if later_prices:
            old = await storage.deliveries.get(price_key)
            assert (old["state"], old["reason"]) == ("suppressed", "superseded_price")

        sender, bot = notifier(storage)
        assert await sender.deliver_pending() == int(expect_drop)
        assert bot.send_message.call_count == int(expect_drop)
        if expect_drop:
            assert (
                f"£{prices[-2]:,} → £{prices[-1]:,}/mo" in bot.send_message.call_args.kwargs["text"]
            )
            assert (await storage.deliveries.get(pending[0]))["state"] == "complete"
        assert await storage.get_unsent_price_drops() == []
        await storage.deliveries.recover()
        assert await sender.deliver_pending() == 0
        assert bot.send_message.call_count == int(expect_drop)
        bot.send_photo.assert_not_called()
        bot.send_venue.assert_not_called()


async def test_price_waits_for_reconciliation_of_failed_final_discovery_ack(
    tmp_path, enriched_merged_property, delivery_analysis
):
    merged = enriched_merged_property.model_copy(
        update={"images": enriched_merged_property.images[:1]}
    )
    uid = merged.unique_id
    path = str(tmp_path / "discovery-ack-price.db")
    now = datetime(2026, 10, 10, tzinfo=UTC)
    async with PropertyStorage(path) as storage:
        key = await seed(storage, merged, delivery_analysis)
        storage.deliveries._clock = lambda: now
        sender, bot = notifier(storage)
        bot.send_venue.side_effect = TelegramRetryAfter(
            method=METHOD, message="flood", retry_after=60
        )
        assert not await deliver(sender, storage.deliveries, key)
        await storage.detect_and_record_price_change(uid, 1600)
        async with storage._transaction() as conn:
            await conn.execute("""CREATE TRIGGER fail_sent BEFORE UPDATE OF notification_status
                ON properties WHEN NEW.notification_status='sent'
                BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        bot.send_venue.side_effect = None
        storage.deliveries._clock = lambda: now + timedelta(seconds=61)
        assert not await deliver(sender, storage.deliveries, key)
        row = await storage.deliveries.get(key)
        assert row["state"] == "sending" and row["next_part"] == 1
        assert json.loads(row["receipts_json"]) == [[11]]
        assert await status(storage, uid) == "pending"
        await storage.detect_and_record_price_change(uid, 1500)

    async with PropertyStorage(path) as storage:
        storage.deliveries._clock = lambda: now + timedelta(seconds=61)
        await storage.deliveries.recover()
        assert (await storage.deliveries.get(key))["state"] == "uncertain"
        sender, bot = notifier(storage)
        assert await sender.deliver_pending() == 0
        assert not bot.mock_calls
        assert "review" not in (await storage.deliveries.report())["counts"]
        async with storage._transaction() as conn:
            await conn.execute("DROP TRIGGER fail_sent")
        await storage.deliveries.resolve(
            key, "acknowledge", "fixture:venue_receipt", message_ids=[15]
        )
        assert await status(storage, uid) == "sent"
        assert await sender.deliver_pending() == 1
        assert "£1,600 → £1,500/mo" in bot.send_message.call_args.kwargs["text"]
        assert await storage.get_unsent_price_drops() == []
        await storage.deliveries.recover()
        assert await sender.deliver_pending() == 0
        bot.send_message.assert_called_once()
        bot.send_photo.assert_not_called()
        bot.send_venue.assert_not_called()


async def test_album_is_not_repeated_after_followup_rejection(
    tmp_path, enriched_merged_property, delivery_analysis
):
    async with PropertyStorage(str(tmp_path / "album.db")) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        bot.send_message.side_effect = TelegramRetryAfter(
            method=METHOD, message="flood", retry_after=1
        )
        assert not await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["next_part"] == 1
        bot.send_message.side_effect = None
        storage.deliveries._clock = lambda: datetime.now(UTC) + timedelta(seconds=2)
        assert await deliver(sender, storage.deliveries, key)
        bot.send_media_group.assert_called_once()
        assert bot.send_message.call_count == 2 and bot.send_venue.call_count == 1
        assert json.loads((await storage.deliveries.get(key))["receipts_json"]) == [
            [11, 12, 13],
            [14],
            [15],
        ]


@pytest.mark.parametrize(
    "failure", [TimeoutError(), TelegramServerError(method=METHOD, message="server")]
)
async def test_ambiguous_response_is_held_without_fallback(
    tmp_path, enriched_merged_property, delivery_analysis, failure
):
    async with PropertyStorage(str(tmp_path / "unknown.db")) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        bot.send_media_group.side_effect = failure
        assert not await deliver(sender, storage.deliveries, key)
        await storage.deliveries.recover()
        assert not await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["state"] == "uncertain"
        bot.send_media_group.assert_called_once()
        bot.send_message.assert_not_called()
        bot.send_venue.assert_not_called()


async def test_permanent_rejection_requires_explicit_resolution(
    tmp_path, enriched_merged_property, delivery_analysis
):
    path = tmp_path / "blocked.db"
    async with PropertyStorage(str(path)) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        bot.send_media_group.side_effect = TelegramForbiddenError(method=METHOD, message="blocked")
        assert not await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["state"] == "blocked"
        assert not await deliver(sender, storage.deliveries, key)
        bot.send_media_group.assert_called_once()
    with runner_lock(str(path)), pytest.raises(ValueError, match="active runner"):
        await administer(path, key, "retry", "fixture:unblocked")
    report = await administer(path, key, "retry", "fixture:unblocked")
    assert report["counts"]["ready"] == 1
    async with PropertyStorage(str(path)) as storage:
        sender, bot = notifier(storage)
        assert await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["resolution_ref"] == "fixture:unblocked"


async def test_lost_acknowledgement_does_not_redispatch(
    tmp_path, enriched_merged_property, delivery_analysis
):
    path = str(tmp_path / "ack.db")
    async with PropertyStorage(path) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        async with storage._transaction() as conn:
            await conn.execute("""CREATE TRIGGER fail_receipt BEFORE UPDATE OF receipts_json
                ON notification_deliveries BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["state"] == "sending"
        bot.send_media_group.assert_called_once()
        bot.send_message.assert_not_called()
    async with PropertyStorage(path) as storage:
        await storage.deliveries.recover()
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["state"] == "uncertain"
        bot.send_media_group.assert_not_called()
        async with storage._transaction() as conn:
            await conn.execute("DROP TRIGGER fail_receipt")
        await storage.deliveries.resolve(
            key, "acknowledge", "fixture:chat_receipt", message_ids=[11, 12, 13]
        )
        assert await deliver(sender, storage.deliveries, key)
        bot.send_media_group.assert_not_called()
        assert await status(storage, enriched_merged_property.unique_id) == "sent"


@pytest.mark.parametrize("boundary", ["dispatch", "receipt"])
async def test_cancellation_holds_unknown_or_finishes_receipt(
    tmp_path, enriched_merged_property, delivery_analysis, monkeypatch, boundary
):
    async with PropertyStorage(str(tmp_path / "cancel.db")) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        entered = asyncio.Event()
        release = asyncio.Event()
        if boundary == "dispatch":

            async def hang(**kwargs):
                entered.set()
                await release.wait()

            bot.send_media_group.side_effect = hang
        else:
            original = storage.deliveries.acknowledge

            async def ack(*args):
                entered.set()
                await release.wait()
                await original(*args)

            monkeypatch.setattr(storage.deliveries, "acknowledge", ack)
        task = asyncio.create_task(deliver(sender, storage.deliveries, key))
        await entered.wait()
        task.cancel()
        if boundary == "receipt":
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = await storage.deliveries.get(key)
        assert row["state"] == ("uncertain" if boundary == "dispatch" else "ready")
        assert row["next_part"] == (0 if boundary == "dispatch" else 1)
        bot.send_message.assert_not_called()


async def test_concurrent_callbacks_cannot_send_the_same_part(
    tmp_path, enriched_merged_property, delivery_analysis
):
    async with PropertyStorage(str(tmp_path / "race.db")) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def album(**kwargs):
            entered.set()
            await release.wait()
            return [receipt(i) for i in [11, 12, 13]]

        bot.send_media_group.side_effect = album
        first = asyncio.create_task(deliver(sender, storage.deliveries, key))
        await entered.wait()
        assert not await deliver(sender, storage.deliveries, key)
        release.set()
        assert await first
        bot.send_media_group.assert_called_once()


@pytest.mark.parametrize("bad", [[], [receipt(11)], [receipt(11), receipt(11), receipt(13)]])
async def test_incomplete_or_duplicate_album_receipts_are_uncertain(
    tmp_path, enriched_merged_property, delivery_analysis, bad
):
    async with PropertyStorage(str(tmp_path / "receipts.db")) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        bot.send_media_group.return_value = bad
        assert not await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["state"] == "uncertain"
        bot.send_message.assert_not_called()


async def test_recipient_change_never_inherits_earlier_receipts(
    tmp_path, enriched_merged_property, delivery_analysis
):
    async with PropertyStorage(str(tmp_path / "recipient.db")) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        bot.send_message.side_effect = TelegramRetryAfter(
            method=METHOD, message="flood", retry_after=1
        )
        assert not await deliver(sender, storage.deliveries, key)
        changed, bot2 = notifier(storage, chat_id=999)
        assert not await deliver(changed, storage.deliveries, key)
        assert not bot2.mock_calls
        row = await storage.deliveries.get(key)
        assert (row["state"], row["reason"]) == ("blocked", "recipient_changed")
        assert json.loads(row["receipts_json"]) == [[11, 12, 13]]
        await storage.deliveries.resolve(key, "suppress", "fixture:recipient_changed")
        await storage.deliveries.recover()
        assert (await storage.deliveries.get(key))["state"] == "suppressed"
        assert not await deliver(changed, storage.deliveries, key)
        assert not bot2.mock_calls


async def test_atomic_intent_creation_and_sent_reanalysis(
    tmp_path, enriched_merged_property, delivery_analysis
):
    merged = enriched_merged_property
    async with PropertyStorage(str(tmp_path / "atomic.db")) as storage:
        await storage.processing.save_pre_analysis_properties([merged], {})
        async with storage._transaction() as conn:
            await conn.execute("""CREATE TRIGGER fail_intent
                BEFORE INSERT ON notification_deliveries
                BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        with pytest.raises(aiosqlite.IntegrityError):
            await storage.processing.complete_analysis(merged.unique_id, delivery_analysis)
        assert await status(storage, merged.unique_id) == "pending_analysis"
        assert await storage.get_quality_analysis(merged.unique_id) is None
        async with storage._transaction() as conn:
            await conn.execute("DROP TRIGGER fail_intent")
        key = await seed(storage, merged, delivery_analysis)
        sender, _bot = notifier(storage)
        assert await deliver(sender, storage.deliveries, key)
        before = await storage.deliveries.get(key)
        await storage.processing.request_reanalysis([merged.unique_id])
        await storage.processing.complete_reanalysis(merged.unique_id, delivery_analysis)
        assert await storage.deliveries.get(key) == before
        assert await status(storage, merged.unique_id) == "sent"


async def test_latest_price_change_keeps_its_own_delivery_state(
    tmp_path, enriched_merged_property, delivery_analysis
):
    uid = enriched_merged_property.unique_id
    async with PropertyStorage(str(tmp_path / "prices.db")) as storage:
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        sender, bot = notifier(storage)
        assert await deliver(sender, storage.deliveries, key)
        await storage.detect_and_record_price_change(uid, 1700)
        old_key = (await storage.deliveries.pending())[0]
        entered = asyncio.Event()
        release = asyncio.Event()

        async def sending(**kwargs):
            entered.set()
            await release.wait()
            return receipt(20)

        bot.send_message.side_effect = sending
        task = asyncio.create_task(deliver(sender, storage.deliveries, old_key))
        await entered.wait()
        await storage.detect_and_record_price_change(uid, 1600)
        release.set()
        assert await task
        drops = await storage.get_unsent_price_drops()
        assert drops[0]["new_price"] == 1600
        new_key = (await storage.deliveries.pending())[0]
        assert new_key != old_key
        bot.send_message.side_effect = None
        assert await deliver(sender, storage.deliveries, new_key)
        assert await storage.get_unsent_price_drops() == []
        assert "1,700" in bot.send_message.call_args.kwargs["text"]
        assert "1,600" in bot.send_message.call_args.kwargs["text"]


@pytest.mark.parametrize("price_failure", [None, "rejected", "recipient_changed"])
async def test_unstarted_price_drop_is_superseded_even_by_a_rise(
    tmp_path, enriched_merged_property, delivery_analysis, price_failure
):
    async with PropertyStorage(str(tmp_path / "rise.db")) as storage:
        uid = enriched_merged_property.unique_id
        await seed(storage, enriched_merged_property, delivery_analysis)
        await storage.mark_notified(uid)
        await storage.detect_and_record_price_change(uid, 1700)
        price_key = next(k for k in await storage.deliveries.pending() if k.startswith("price:"))
        sender, bot = notifier(storage)
        if price_failure:
            bot.send_message.side_effect = TelegramForbiddenError(method=METHOD, message="blocked")
            assert not await deliver(sender, storage.deliveries, price_key)
            if price_failure == "recipient_changed":
                await storage.deliveries.resolve(price_key, "retry", "fixture:recipient_check")
                changed, changed_bot = notifier(storage, chat_id=999)
                assert not await deliver(changed, storage.deliveries, price_key)
                changed_bot.send_message.assert_not_called()
            assert (await storage.deliveries.get(price_key))["state"] == "blocked"
        await storage.detect_and_record_price_change(uid, 1900)
        async with storage._read_connection() as conn:
            cur = await conn.execute(
                "SELECT state,reason FROM notification_deliveries WHERE kind='price_drop'"
            )
            row = await cur.fetchone()
            assert tuple(row) == ("suppressed", "superseded_price")
        await storage.deliveries.recover()
        report = await storage.deliveries.report()
        assert "review" not in report["counts"]
        bot.reset_mock()
        assert not await deliver(sender, storage.deliveries, price_key)
        assert not bot.mock_calls


async def test_legacy_outcomes_require_review_and_report_redacts_content(
    tmp_path, enriched_merged_property, delivery_analysis
):
    path = tmp_path / "legacy.db"
    async with PropertyStorage(str(path)) as storage:
        await storage.save_merged_property(enriched_merged_property)
        await storage.mark_notification_failed(enriched_merged_property.unique_id)
        await storage.deliveries.recover()
        key = f"discovery:{enriched_merged_property.unique_id}"
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, key)
        assert not bot.mock_calls
        assert (await storage.deliveries.get(key))["state"] == "review"
    report = await read_report(path)
    text = json.dumps(report)
    assert "snapshot_json" not in text and "plan_json" not in text
    assert "recipient" not in text and "fixture" not in text
    assert "legacy_untracked" in text
    await administer(path, key, "suppress", "fixture:legacy_review")
    async with PropertyStorage(str(path)) as storage:
        await storage.deliveries.recover()
        assert (await storage.deliveries.get(key))["state"] == "suppressed"


@pytest.mark.parametrize("change", ["missing", "changed"])
async def test_saved_asset_cannot_silently_be_replaced(
    tmp_path, enriched_merged_property, delivery_analysis, change
):
    from home_finder.utils.image_cache import get_cache_dir, url_to_filename

    merged = enriched_merged_property
    root = tmp_path / "assets"
    directory = get_cache_dir(str(root), merged.unique_id)
    directory.mkdir(parents=True)
    url = str(merged.images[0].url)
    path = directory / url_to_filename(url, "gallery", 0)
    path.write_bytes(b"fixture-image")
    async with PropertyStorage(str(tmp_path / "assets.db")) as storage:
        key = await seed(storage, merged, delivery_analysis)
        sender, bot = notifier(storage, data_dir=str(root))
        row = await storage.deliveries.get(key)
        plan = discovery_plan(sender, DiscoverySnapshot.model_validate_json(row["snapshot_json"]))
        await storage.deliveries.prepare(key, "123456:123", plan)
        if change == "missing":
            path.unlink()
        else:
            path.write_bytes(b"replacement-image")
        assert not await deliver(sender, storage.deliveries, key)
        assert not bot.mock_calls
        assert (await storage.deliveries.get(key))["next_part"] == 0


async def test_final_receipt_and_property_sent_status_commit_together(
    tmp_path, enriched_merged_property, make_quality_analysis
):
    merged = enriched_merged_property.model_copy(
        update={"images": enriched_merged_property.images[:1]}
    )
    async with PropertyStorage(str(tmp_path / "final-ack.db")) as storage:
        key = await seed(storage, merged, make_quality_analysis())
        async with storage._transaction() as conn:
            await conn.execute("""CREATE TRIGGER fail_sent BEFORE UPDATE OF notification_status
                ON properties WHEN NEW.notification_status='sent'
                BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, key)
        row = await storage.deliveries.get(key)
        assert row["state"] == "sending" and row["receipts_json"] == "[]"
        assert await status(storage, merged.unique_id) == "pending"
        await storage.deliveries.recover()
        assert (await storage.deliveries.get(key))["state"] == "uncertain"
        assert not await deliver(sender, storage.deliveries, key)
        bot.send_photo.assert_called_once()


async def test_migration_retains_legacy_sent_and_quarantines_untracked_pending(
    tmp_path, enriched_merged_property, delivery_analysis
):
    from home_finder.db.migrations import migrate_012_notification_deliveries

    async with PropertyStorage(str(tmp_path / "legacy-migration.db")) as storage:
        await storage.save_merged_property(enriched_merged_property)
        uid = enriched_merged_property.unique_id
        async with storage._transaction() as conn:
            await conn.execute("DROP TABLE notification_deliveries")
            await migrate_012_notification_deliveries(conn)
        row = await storage.deliveries.get(f"discovery:{uid}")
        assert row["state"] == "review" and row["receipts_json"] == "[]"
        await storage.mark_notified(uid)
        async with storage._transaction() as conn:
            await conn.execute("DROP TABLE notification_deliveries")
            await migrate_012_notification_deliveries(conn)
            await migrate_012_notification_deliveries(conn)
        assert await storage.deliveries.get(f"discovery:{uid}") is None
        assert await status(storage, uid) == "sent"


@pytest.mark.parametrize("discovery_state", ["sent", "waiting", "blocked"])
async def test_price_intent_failure_rolls_back_the_price_change(
    tmp_path, enriched_merged_property, delivery_analysis, discovery_state
):
    async with PropertyStorage(str(tmp_path / "price-atomic.db")) as storage:
        uid = enriched_merged_property.unique_id
        key = await seed(storage, enriched_merged_property, delivery_analysis)
        if discovery_state == "sent":
            await storage.mark_notified(uid)
        else:
            sender, bot = notifier(storage)
            bot.send_message.side_effect = (
                TelegramRetryAfter(method=METHOD, message="flood", retry_after=60)
                if discovery_state == "waiting"
                else TelegramForbiddenError(method=METHOD, message="blocked")
            )
            assert not await deliver(sender, storage.deliveries, key)
        frozen = await storage.deliveries.get(key)
        async with storage._transaction() as conn:
            await conn.execute("""CREATE TRIGGER fail_price BEFORE INSERT ON notification_deliveries
                WHEN NEW.kind='price_drop' BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        with pytest.raises(aiosqlite.IntegrityError):
            await storage.detect_and_record_price_change(uid, 1700)
        assert await storage.get_price_history(uid) == []
        detail = await storage.web.get_property_detail(uid)
        assert detail["price_pcm"] == 1800
        assert await storage.deliveries.get(key) == frozen
        assert not [k for k in await storage.deliveries.pending() if k.startswith("price:")]


async def test_uncharged_rate_delay_still_applies_to_a_superseding_price(
    tmp_path, enriched_merged_property, delivery_analysis
):
    async with PropertyStorage(str(tmp_path / "recipient-delay.db")) as storage:
        uid = enriched_merged_property.unique_id
        await seed(storage, enriched_merged_property, delivery_analysis)
        await storage.mark_notified(uid)
        await storage.detect_and_record_price_change(uid, 1700)
        keys = [k for k in await storage.deliveries.pending() if k.startswith("price:")]
        sender, bot = notifier(storage)
        bot.send_message.side_effect = TelegramRetryAfter(
            method=METHOD, message="flood", retry_after=60
        )
        assert not await deliver(sender, storage.deliveries, keys[0])
        await storage.detect_and_record_price_change(uid, 1600)
        newkeys = [k for k in await storage.deliveries.pending() if k.startswith("price:")]
        assert len(newkeys) == 1 and newkeys[0] != keys[0]
        assert not await deliver(sender, storage.deliveries, newkeys[0])
        bot.send_message.assert_called_once()


async def test_read_only_report_does_not_migrate_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA user_version=6")
        await conn.commit()
    before = path.read_bytes()
    assert await read_report(path) == {"available": False}
    assert path.read_bytes() == before


async def test_images_arriving_before_first_plan_unblock_delivery(
    tmp_path, enriched_merged_property, make_quality_analysis
):
    merged = enriched_merged_property.model_copy(update={"images": ()})
    async with PropertyStorage(str(tmp_path / "later-images.db")) as storage:
        key = await seed(storage, merged, make_quality_analysis())
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, key)
        assert (await storage.deliveries.get(key))["reason"] == "no_images"
        await storage.save_property_images(merged.unique_id, list(enriched_merged_property.images))
        assert await deliver(sender, storage.deliveries, key)
        bot.send_photo.assert_called_once()


async def test_schema_upgrade_and_backup_preserve_spending_holds_and_checkpoint(tmp_path):
    from unittest.mock import patch

    from home_finder.db.backup import create_backup, restore_backup, verify_backup
    from home_finder.db.migrations import MIGRATIONS
    from home_finder.models.spend import VisualCheckpointWrite
    from tests.test_analysis.test_quality_respx import PHASE1_VISUAL_RESPONSE
    from tests.test_db.test_spend import policy, settlement, spec

    path = tmp_path / "schema11.db"
    with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:11]):
        async with PropertyStorage(str(path)) as storage:
            hold = await storage.spend.admit(spec("held"), policy())
            await storage.spend.mark_dispatched(hold)
            await storage.spend.mark_unknown(hold, "timeout")
            visual = await storage.spend.admit(spec("visual-receipt"), policy())
            await storage.spend.mark_dispatched(visual)
            await storage.spend.settle_usage(
                visual,
                settlement().model_copy(update={"result": "success"}),
                checkpoint=VisualCheckpointWrite(
                    input_sha256="a" * 64, data=PHASE1_VISUAL_RESPONSE["content"][0]["input"]
                ),
            )
            before = await storage.spend.report()
    backup = tmp_path / "backup"
    manifest = create_backup(path, backup)
    assert verify_backup(backup) == manifest
    restored = tmp_path / "restored"
    restore_backup(backup, restored)
    async with PropertyStorage(str(restored / "properties.db")) as storage:
        after = await storage.spend.report()
        temporal = {"as_of", "oldest_unresolved_age_seconds"}
        assert {k: v for k, v in before.items() if k not in temporal} == {
            k: v for k, v in after.items() if k not in temporal
        }
        assert after["oldest_unresolved_age_seconds"] >= before["oldest_unresolved_age_seconds"]
        assert after["held_nanousd"] > 0 and len(after["visual_checkpoints"]) == 1
        async with storage._read_connection() as conn:
            cur = await conn.execute("PRAGMA user_version")
            assert (await cur.fetchone())[0] == len(MIGRATIONS)


def test_without_asset_root_does_not_use_incidental_working_directory_cache(
    tmp_path, monkeypatch, enriched_merged_property
):
    from home_finder.utils.image_cache import get_cache_dir, url_to_filename

    merged = enriched_merged_property
    monkeypatch.chdir(tmp_path)
    url = str(merged.images[0].url)
    directory = get_cache_dir("", merged.unique_id)
    directory.mkdir(parents=True)
    (directory / url_to_filename(url, "gallery", 0)).write_bytes(b"incidental-local-image")
    sender = TelegramNotifier(bot_token="123456:fixture", chat_id=123)
    plan = discovery_plan(sender, DiscoverySnapshot(merged=merged))
    assert plan.parts[0].payload["photo"] == {"url": url}
