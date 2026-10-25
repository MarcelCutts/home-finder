"""Interleaved storage operations must own their complete database work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import NotificationStatus, Property, PropertyImage, UserStatus


@pytest.fixture
async def storage(tmp_path: Path, sample_property: Property) -> AsyncIterator[PropertyStorage]:
    async with PropertyStorage(str(tmp_path / "ownership.db")) as db:
        await db.save_property(sample_property)
        yield db


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["storage", "dashboard"])
async def test_failed_price_change_cannot_commit_through_unrelated_notification(
    storage: PropertyStorage, sample_property: Property, reader: str
) -> None:
    """Pause after the price write, let a competing action run, then fail."""
    conn = await storage._get_connection()
    original_execute = conn.execute
    price_written = asyncio.Event()
    fail_price = asyncio.Event()

    async def pause_after_price():  # type: ignore[no-untyped-def]
        price_written.set()
        await fail_price.wait()
        raise RuntimeError("price transaction failed")

    def intercept(sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if sql.startswith("SELECT q.analysis_json, p.postcode"):
            return pause_after_price()
        # Preserve execute's awaitable AND async-context-manager interface.
        return original_execute(sql, *args, **kwargs)

    with patch.object(conn, "execute", new=intercept):
        price_task = asyncio.create_task(
            storage.detect_and_record_price_change(sample_property.unique_id, 1700)
        )
        await asyncio.wait_for(price_written.wait(), 2)
        notify_task = asyncio.create_task(storage.mark_notified(sample_property.unique_id))

        async def read_price() -> int:
            if reader == "dashboard":
                detail = await storage.web.get_property_detail(sample_property.unique_id)
                assert detail is not None
                return detail["price_pcm"]
            tracked = await storage.get_property(sample_property.unique_id)
            assert tracked is not None
            return tracked.property.price_pcm

        read_task = asyncio.create_task(read_price())
        # A safe single-connection reader/writer waits; a separate reader may finish.
        # Waiting here makes the old independent commit reproducible, rather than
        # relying on which SQLite request happens to reach its worker first.
        await asyncio.wait({notify_task, read_task}, timeout=0.1)
        fail_price.set()
        with pytest.raises(RuntimeError, match="price transaction failed"):
            await price_task
        await notify_task
        observed = await read_task

    assert observed == sample_property.price_pcm
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None
    assert current.property.price_pcm == sample_property.price_pcm
    assert current.notification_status == NotificationStatus.SENT
    assert await storage.get_price_history(sample_property.unique_id) == []


@pytest.mark.asyncio
async def test_explicit_handle_allows_composition_but_not_implicit_or_child_scopes(
    storage: PropertyStorage, sample_property: Property
) -> None:
    image = PropertyImage(
        source=sample_property.source,
        url=HttpUrl("https://example.com/fixture.jpg"),
        image_type="gallery",
    )
    async with storage._transaction() as conn:
        await conn.execute("UPDATE properties SET price_pcm = 1700")
        await storage.save_property_images(sample_property.unique_id, [image], _conn=conn)
        observed = await storage.get_property(sample_property.unique_id, _conn=conn)
        assert observed is not None and observed.property.price_pcm == 1700
        assert await storage.get_property_images(sample_property.unique_id, _conn=conn) == [image]

        with pytest.raises(RuntimeError, match="Nested database scope"):
            await storage.mark_notified(sample_property.unique_id)
        with pytest.raises(RuntimeError, match="Nested database scope"):
            await storage.get_property(sample_property.unique_id)
        with pytest.raises(RuntimeError, match="Nested database scope"):
            await storage.close()

        with pytest.raises(RuntimeError, match="not owned by this task"):
            await asyncio.create_task(storage.get_property(sample_property.unique_id, _conn=conn))

    with pytest.raises(RuntimeError, match="not owned by this task"):
        await storage.get_property(sample_property.unique_id, _conn=conn)
    assert await storage.get_property_images(sample_property.unique_id) == [image]


@pytest.mark.asyncio
async def test_read_scope_rejects_writes_and_resets_query_only(
    storage: PropertyStorage, sample_property: Property
) -> None:
    async with storage._read_connection() as conn:
        with pytest.raises(RuntimeError, match="active write transaction"):
            async with storage._transaction(conn=conn):
                pytest.fail("Read scope must not be upgraded")
        with pytest.raises(aiosqlite.OperationalError, match="readonly"):
            await conn.execute("UPDATE properties SET price_pcm = 1700")
    await storage.mark_notified(sample_property.unique_id)
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None and current.property.price_pcm == sample_property.price_pcm


@pytest.mark.asyncio
async def test_cancelled_lock_waiter_does_not_touch_the_owner(
    storage: PropertyStorage, sample_property: Property
) -> None:
    async with storage._transaction() as conn:
        await conn.execute("UPDATE properties SET price_pcm = 1700")
        waiter = asyncio.create_task(storage.mark_notified(sample_property.unique_id))
        await asyncio.sleep(0)  # Run the waiter until it blocks on ownership.
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert conn.in_transaction
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None
    assert current.property.price_pcm == 1700
    assert current.notification_status == NotificationStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("read", [False, True])
async def test_cancellation_after_begin_is_cleaned_up(
    storage: PropertyStorage, sample_property: Property, read: bool
) -> None:
    conn = await storage._get_connection()
    original = conn.execute
    begun = asyncio.Event()

    async def delayed_begin(sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        cursor = await original(sql, *args, **kwargs)
        if sql == ("BEGIN" if read else "BEGIN IMMEDIATE"):
            begun.set()
            await asyncio.Event().wait()
        return cursor

    with patch.object(conn, "execute", side_effect=delayed_begin):
        operation = (
            storage.get_property(sample_property.unique_id)
            if read
            else storage.mark_notified(sample_property.unique_id)
        )
        task = asyncio.create_task(operation)
        await asyncio.wait_for(begun.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not conn.in_transaction
    await storage.mark_notified(sample_property.unique_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["commit", "rollback"])
async def test_repeated_cancellation_waits_for_transaction_completion(
    storage: PropertyStorage, sample_property: Property, phase: str
) -> None:
    conn = await storage._get_connection()
    original = getattr(conn, phase)
    finishing = asyncio.Event()
    finish = asyncio.Event()
    writing = asyncio.Event()

    async def delayed_finish() -> None:
        finishing.set()
        await finish.wait()
        await original()

    async def operation() -> None:
        async with storage._transaction() as owned:
            await owned.execute("UPDATE properties SET price_pcm = 1700")
            writing.set()
            if phase == "rollback":
                await asyncio.Event().wait()

    with patch.object(conn, phase, side_effect=delayed_finish):
        task = asyncio.create_task(operation())
        await asyncio.wait_for(writing.wait(), 2)
        if phase == "rollback":
            task.cancel()
        await asyncio.wait_for(finishing.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        notify = asyncio.create_task(storage.mark_notified(sample_property.unique_id))
        await asyncio.wait({notify}, timeout=0.05)
        assert not notify.done(), "Ownership was released before cleanup finished"
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await notify

    assert not conn.in_transaction
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None
    assert current.property.price_pcm == (1700 if phase == "commit" else sample_property.price_pcm)
    assert current.notification_status == NotificationStatus.SENT


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_before_reusing_connection(
    storage: PropertyStorage, sample_property: Property
) -> None:
    conn = await storage._get_connection()
    with (
        patch.object(conn, "commit", side_effect=aiosqlite.OperationalError("commit failed")),
        pytest.raises(aiosqlite.OperationalError, match="commit failed"),
    ):
        await storage.mark_notified(sample_property.unique_id)
    assert not conn.in_transaction
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None and current.notification_status == NotificationStatus.PENDING
    await storage.mark_notified(sample_property.unique_id)


@pytest.mark.asyncio
async def test_rollback_failure_closes_old_connection_before_reopening(
    storage: PropertyStorage, sample_property: Property
) -> None:
    conn = await storage._get_connection()
    with (
        patch.object(conn, "rollback", side_effect=aiosqlite.OperationalError("rollback failed")),
        pytest.raises(aiosqlite.OperationalError, match="rollback failed"),
    ):
        async with storage._transaction() as owned:
            await owned.execute("UPDATE properties SET price_pcm = 1700")
            raise RuntimeError("abandon this write")
    assert storage._conn is None
    with pytest.raises(ValueError, match="no active connection"):
        await conn.execute("SELECT 1")
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None and current.property.price_pcm == sample_property.price_pcm
    await storage.mark_notified(sample_property.unique_id)


@pytest.mark.asyncio
async def test_external_lock_fails_without_partial_write_and_manual_retry_works(
    storage: PropertyStorage, sample_property: Property
) -> None:
    conn = await storage._get_connection()
    # Shorten the same SQLite busy timeout solely for this failure-path test.
    await conn.execute("PRAGMA busy_timeout=20")
    async with aiosqlite.connect(storage.db_path) as other:
        await other.execute("BEGIN IMMEDIATE")
        with pytest.raises(aiosqlite.OperationalError, match="database is locked"):
            await storage.mark_notified(sample_property.unique_id)
        assert not conn.in_transaction
        await other.rollback()
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None and current.notification_status == NotificationStatus.PENDING
    await storage.mark_notified(sample_property.unique_id)


@pytest.mark.asyncio
async def test_close_drains_owner_and_cannot_be_interrupted_halfway(
    storage: PropertyStorage, sample_property: Property
) -> None:
    conn = await storage._get_connection()
    closing = asyncio.Event()
    finish = asyncio.Event()
    original = conn.close

    async def delayed_close() -> None:
        closing.set()
        await finish.wait()
        await original()

    with patch.object(conn, "close", side_effect=delayed_close):
        async with storage._transaction() as owned:
            await owned.execute("UPDATE properties SET price_pcm = 1700")
            closer = asyncio.create_task(storage.close())
            await asyncio.sleep(0)
            assert not closing.is_set()
        await asyncio.wait_for(closing.wait(), 2)
        closer.cancel()
        await asyncio.sleep(0)
        closer.cancel()
        assert not closer.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await closer
    assert storage._conn is None
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None and current.property.price_pcm == 1700


@pytest.mark.asyncio
async def test_two_status_transactions_both_commit(
    storage: PropertyStorage, sample_property: Property
) -> None:
    await asyncio.gather(
        storage.update_user_status(sample_property.unique_id, UserStatus.INTERESTED),
        storage.update_user_status(sample_property.unique_id, UserStatus.ENQUIRED),
    )
    history = await storage.get_status_history(sample_property.unique_id)
    assert {row["to_status"] for row in history} == {"interested", "enquired"}


@pytest.mark.asyncio
async def test_failed_status_change_cannot_commit_through_pipeline_write(
    storage: PropertyStorage, sample_property: Property
) -> None:
    conn = await storage._get_connection()
    original = conn.execute
    status_written = asyncio.Event()
    fail_status = asyncio.Event()

    async def intercept(sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "INSERT INTO status_events" in sql:
            status_written.set()
            await fail_status.wait()
            raise RuntimeError("status event failed")
        return await original(sql, *args, **kwargs)

    with patch.object(conn, "execute", side_effect=intercept):
        status = asyncio.create_task(
            storage.update_user_status(sample_property.unique_id, UserStatus.INTERESTED)
        )
        await asyncio.wait_for(status_written.wait(), 2)
        pipeline_run = asyncio.create_task(storage.runs.create_pipeline_run())
        await asyncio.wait({pipeline_run}, timeout=0.1)
        fail_status.set()
        with pytest.raises(RuntimeError, match="status event failed"):
            await status
        run_id = await pipeline_run
    await storage.runs.complete_pipeline_run(run_id, "completed")
    assert await storage.runs.get_last_pipeline_run() is not None
    detail = await storage.web.get_property_detail(sample_property.unique_id)
    assert detail is not None and detail["user_status"] == "new"
    assert await storage.get_status_history(sample_property.unique_id) == []


@pytest.mark.asyncio
async def test_failed_close_blocks_connection_replacement_until_cleanup_succeeds(
    storage: PropertyStorage, sample_property: Property
) -> None:
    conn = await storage._get_connection()
    with patch.object(conn, "close", side_effect=OSError("close failed")):
        with pytest.raises(OSError, match="close failed"):
            await storage.close()
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await storage.mark_notified(sample_property.unique_id)
        assert storage._conn is conn
    await storage.close()
    await storage.mark_notified(sample_property.unique_id)


@pytest.mark.asyncio
async def test_cancelled_initial_configuration_closes_the_partial_connection(
    tmp_path: Path,
) -> None:
    storage = PropertyStorage(str(tmp_path / "opening.db"))
    original_connect = aiosqlite.connect
    configuring = asyncio.Event()
    opened: list[aiosqlite.Connection] = []

    async def connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        conn = await original_connect(*args, **kwargs)
        opened.append(conn)
        original_execute = conn.execute

        async def execute(sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if sql == "PRAGMA foreign_keys=ON":
                configuring.set()
                await asyncio.Event().wait()
            return await original_execute(sql, *args, **kwargs)

        conn.execute = execute  # type: ignore[method-assign]
        return conn

    with patch("home_finder.db.storage.aiosqlite.connect", side_effect=connect):
        task = asyncio.create_task(storage.initialize())
        await asyncio.wait_for(configuring.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert storage._conn is None
    with pytest.raises(ValueError, match="no active connection"):
        await opened[0].execute("SELECT 1")
    async with storage:
        assert await storage.web.get_property_count() == 0


@pytest.mark.asyncio
async def test_cancelled_write_does_not_rollback_another_task(
    storage: PropertyStorage, sample_property: Property
) -> None:
    written = asyncio.Event()

    async def cancelled_operation() -> None:
        async with storage._transaction() as conn:
            await conn.execute("UPDATE properties SET price_pcm = 1700")
            written.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled_operation())
    await asyncio.wait_for(written.wait(), 2)
    notify = asyncio.create_task(storage.mark_notified(sample_property.unique_id))
    await asyncio.wait({notify}, timeout=0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await notify
    current = await storage.get_property(sample_property.unique_id)
    assert current is not None
    assert current.property.price_pcm == sample_property.price_pcm
    assert current.notification_status == NotificationStatus.SENT
