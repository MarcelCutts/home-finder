"""Tests for the property event recorder (T4 audit trail)."""

from collections.abc import AsyncGenerator

import pytest_asyncio

from home_finder.db.storage import PropertyStorage
from home_finder.pipeline.event_recorder import EventRecorder, PropertyEvent


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def run_id(storage: PropertyStorage) -> int:
    return await storage.create_pipeline_run()


class TestPropertyEvent:
    """PropertyEvent dataclass basics."""

    def test_defaults(self) -> None:
        event = PropertyEvent("openrent:123", "openrent", "criteria_passed", "criteria")
        assert event.property_id == "openrent:123"
        assert event.source == "openrent"
        assert event.event_type == "criteria_passed"
        assert event.stage == "criteria"
        assert event.metadata is None

    def test_with_metadata(self) -> None:
        event = PropertyEvent(
            "zoopla:456",
            "zoopla",
            "criteria_dropped",
            "criteria",
            {"price": 3000, "bedrooms": 3},
        )
        assert event.metadata == {"price": 3000, "bedrooms": 3}


class TestEventRecorder:
    """EventRecorder buffering and flush behaviour."""

    async def test_record_accumulates(self, storage: PropertyStorage, run_id: int) -> None:
        recorder = EventRecorder(storage, run_id)
        recorder.record(PropertyEvent("a:1", "a", "criteria_passed", "criteria"))
        recorder.record(PropertyEvent("a:2", "a", "criteria_dropped", "criteria"))
        assert len(recorder._buffer) == 2

    async def test_flush_writes_and_clears(self, storage: PropertyStorage, run_id: int) -> None:
        recorder = EventRecorder(storage, run_id)
        recorder.record(PropertyEvent("a:1", "a", "criteria_passed", "criteria"))
        recorder.record(PropertyEvent("a:2", "a", "criteria_dropped", "criteria"))
        await recorder.flush()

        assert len(recorder._buffer) == 0

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM property_events WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 2

    async def test_empty_flush_is_noop(self, storage: PropertyStorage, run_id: int) -> None:
        recorder = EventRecorder(storage, run_id)
        await recorder.flush()  # should not error

        conn = await storage._get_connection()
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM property_events")
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    async def test_context_manager_auto_flushes(
        self, storage: PropertyStorage, run_id: int
    ) -> None:
        async with EventRecorder(storage, run_id) as recorder:
            recorder.record(PropertyEvent("a:1", "a", "enriched", "enrichment"))
            recorder.record(PropertyEvent("a:2", "a", "enrichment_failed", "enrichment"))

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM property_events WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 2

    async def test_record_batch(self, storage: PropertyStorage, run_id: int) -> None:
        recorder = EventRecorder(storage, run_id)
        events = [
            PropertyEvent("a:1", "a", "criteria_passed", "criteria"),
            PropertyEvent("a:2", "a", "criteria_passed", "criteria"),
            PropertyEvent("a:3", "a", "criteria_dropped", "criteria", {"price": 5000}),
        ]
        recorder.record_batch(events)
        assert len(recorder._buffer) == 3

        await recorder.flush()

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM property_events WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 3

    async def test_metadata_persisted_as_json(
        self, storage: PropertyStorage, run_id: int
    ) -> None:
        async with EventRecorder(storage, run_id) as recorder:
            recorder.record(
                PropertyEvent(
                    "z:99", "zoopla", "criteria_dropped", "criteria",
                    {"price": 3500, "bedrooms": 4},
                )
            )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT metadata_json FROM property_events WHERE property_id = 'z:99'"
        )
        row = await cursor.fetchone()
        assert row is not None
        import json

        meta = json.loads(row["metadata_json"])
        assert meta["price"] == 3500
        assert meta["bedrooms"] == 4

    async def test_null_metadata_persisted(
        self, storage: PropertyStorage, run_id: int
    ) -> None:
        async with EventRecorder(storage, run_id) as recorder:
            recorder.record(PropertyEvent("a:1", "a", "dedup_passed", "dedup"))

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT metadata_json FROM property_events WHERE property_id = 'a:1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["metadata_json"] is None

    async def test_run_id_property(self, storage: PropertyStorage, run_id: int) -> None:
        recorder = EventRecorder(storage, run_id)
        assert recorder.run_id == run_id
