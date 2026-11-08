"""The legacy notification_status projection and the lists derived from it.

These storage methods set or read only ``properties.notification_status``.
Delivery intents and receipts live in ``notification_deliveries``; see
tests/test_notifiers for the delivery lifecycle.
"""

from home_finder.models import NotificationStatus, PropertySource


async def test_mark_notified_records_sent_and_its_time(storage, make_property) -> None:
    prop = make_property()
    await storage.save_property(prop)
    await storage.mark_notified(prop.unique_id)

    tracked = await storage.get_property(prop.unique_id)
    assert tracked is not None
    assert tracked.notification_status == NotificationStatus.SENT
    assert tracked.notified_at is not None


async def test_mark_notification_failed_records_failed(storage, make_property) -> None:
    prop = make_property()
    await storage.save_property(prop)
    await storage.mark_notification_failed(prop.unique_id)

    tracked = await storage.get_property(prop.unique_id)
    assert tracked is not None
    assert tracked.notification_status == NotificationStatus.FAILED


async def test_pending_list_drops_a_listing_once_it_is_marked_sent(storage, make_property) -> None:
    sent = make_property()
    pending = make_property(source=PropertySource.RIGHTMOVE, source_id="67890")
    await storage.save_property(sent)
    await storage.save_property(pending)
    assert len(await storage.get_pending_notifications()) == 2

    await storage.mark_notified(sent.unique_id)

    [remaining] = await storage.get_pending_notifications()
    assert remaining.property.source_id == "67890"


async def test_unsent_list_holds_pending_and_failed_but_not_sent_or_awaiting_analysis(
    storage, make_property, make_merged_property
) -> None:
    pending, failed, sent = (make_property(source_id=name) for name in ("p", "f", "s"))
    for prop in (pending, failed, sent):
        await storage.save_property(prop)
    await storage.mark_notification_failed(failed.unique_id)
    await storage.mark_notified(sent.unique_id)
    awaiting_analysis = make_merged_property(source_id="a")
    await storage.processing.save_pre_analysis_properties([awaiting_analysis], {})

    unsent = await storage.get_unsent_notifications()

    assert {t.property.unique_id: t.notification_status for t in unsent} == {
        pending.unique_id: NotificationStatus.PENDING,
        failed.unique_id: NotificationStatus.FAILED,
    }
