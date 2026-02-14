"""Tests for pipeline run tracking in storage."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from home_finder.db.storage import PropertyStorage


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s


class TestCreatePipelineRun:
    @pytest.mark.asyncio
    async def test_creates_run_with_running_status(self, storage: PropertyStorage) -> None:
        run_id = await storage.create_pipeline_run()
        assert run_id is not None
        assert run_id > 0

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT status, started_at FROM pipeline_runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        assert row["status"] == "running"
        assert row["started_at"] is not None

    @pytest.mark.asyncio
    async def test_creates_sequential_ids(self, storage: PropertyStorage) -> None:
        id1 = await storage.create_pipeline_run()
        id2 = await storage.create_pipeline_run()
        assert id2 == id1 + 1


class TestUpdatePipelineRun:
    @pytest.mark.asyncio
    async def test_updates_count_columns(self, storage: PropertyStorage) -> None:
        run_id = await storage.create_pipeline_run()
        await storage.update_pipeline_run(run_id, scraped_count=42, new_count=5)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT scraped_count, new_count FROM pipeline_runs WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["scraped_count"] == 42
        assert row["new_count"] == 5

    @pytest.mark.asyncio
    async def test_noop_with_no_counts(self, storage: PropertyStorage) -> None:
        run_id = await storage.create_pipeline_run()
        await storage.update_pipeline_run(run_id)  # No-op


class TestCompletePipelineRun:
    @pytest.mark.asyncio
    async def test_marks_completed(self, storage: PropertyStorage) -> None:
        run_id = await storage.create_pipeline_run()
        await storage.complete_pipeline_run(run_id, "completed")

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT status, completed_at, duration_seconds FROM pipeline_runs WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        assert row["duration_seconds"] is not None
        assert row["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_marks_failed_with_error(self, storage: PropertyStorage) -> None:
        run_id = await storage.create_pipeline_run()
        await storage.complete_pipeline_run(run_id, "failed", error_message="Connection timeout")

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT status, error_message FROM pipeline_runs WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "failed"
        assert row["error_message"] == "Connection timeout"


class TestGetLastPipelineRun:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_runs(self, storage: PropertyStorage) -> None:
        result = await storage.get_last_pipeline_run()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_only_running(self, storage: PropertyStorage) -> None:
        await storage.create_pipeline_run()  # Still running
        result = await storage.get_last_pipeline_run()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_most_recent_completed(self, storage: PropertyStorage) -> None:
        id1 = await storage.create_pipeline_run()
        await storage.update_pipeline_run(id1, notified_count=3)
        await storage.complete_pipeline_run(id1, "completed")

        id2 = await storage.create_pipeline_run()
        await storage.update_pipeline_run(id2, notified_count=7)
        await storage.complete_pipeline_run(id2, "completed")

        result = await storage.get_last_pipeline_run()
        assert result is not None
        assert result["id"] == id2
        assert result["notified_count"] == 7
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_returns_failed_runs(self, storage: PropertyStorage) -> None:
        run_id = await storage.create_pipeline_run()
        await storage.complete_pipeline_run(run_id, "failed", error_message="boom")

        result = await storage.get_last_pipeline_run()
        assert result is not None
        assert result["status"] == "failed"
        assert result["error_message"] == "boom"


class TestPipelineRunLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, storage: PropertyStorage) -> None:
        """Test create -> update counts -> complete flow."""
        run_id = await storage.create_pipeline_run()

        await storage.update_pipeline_run(run_id, scraped_count=100)
        await storage.update_pipeline_run(run_id, new_count=10, enriched_count=8)
        await storage.update_pipeline_run(run_id, analyzed_count=8, notified_count=8)

        await storage.complete_pipeline_run(run_id, "completed")

        result = await storage.get_last_pipeline_run()
        assert result is not None
        assert result["scraped_count"] == 100
        assert result["new_count"] == 10
        assert result["enriched_count"] == 8
        assert result["analyzed_count"] == 8
        assert result["notified_count"] == 8
        assert result["status"] == "completed"
        assert result["duration_seconds"] >= 0
