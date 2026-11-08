"""RunRepository (``storage.runs``): pipeline runs, scraper runs and property events."""

import json

import pytest

from home_finder.db.storage import PropertyStorage
from home_finder.pipeline.contracts import CandidateProgress
from home_finder.pipeline.event_recorder import PropertyEvent

# Columns the pipeline writes besides CandidateProgress.run_columns(); see flow.py.
_ASSESSMENT_AND_DELIVERY_COLUMNS = {
    "analyzed_count": 8,
    "notified_count": 6,
    "analysis_seconds": 45.7,
    "notification_seconds": 2.1,
    "total_input_tokens": 50000,
    "total_output_tokens": 10000,
    "total_cache_read_tokens": 40000,
    "total_cache_creation_tokens": 5000,
    "estimated_cost_usd": 0.285,
}
_ORIGINAL_COUNT_COLUMNS = {
    "scraped_count",
    "new_count",
    "enriched_count",
    "analyzed_count",
    "notified_count",
    "anchors_updated",
}


def _progress_columns() -> dict[str, int | float]:
    progress = CandidateProgress(
        observed_listing_count=100,
        media_ready_count=9,
        anchors_updated=2,
        prepared_candidate_count=10,
        stage_timings={
            "collection_seconds": 12.5,
            "filtering_seconds": 0.3,
            "preparation_seconds": 25.3,
        },
        criteria_passed_count=50,
        location_passed_count=45,
        new_listing_count=30,
        commute_within_limit_count=20,
        unmatched_candidate_count=18,
        media_admitted_count=15,
    )
    return progress.run_columns()


class TestPipelineRunMetrics:
    async def test_every_metric_the_pipeline_writes_round_trips(self, storage) -> None:
        written = {**_progress_columns(), **_ASSESSMENT_AND_DELIVERY_COLUMNS}
        run_id = await storage.runs.create_pipeline_run()

        await storage.runs.update_pipeline_run(run_id, **written)
        await storage.runs.complete_pipeline_run(run_id, "completed")

        result = await storage.runs.get_last_pipeline_run()
        assert result is not None
        assert {column: result[column] for column in written} == pytest.approx(written)

    async def test_unwritten_metrics_keep_their_column_defaults(self, storage) -> None:
        """The original counts default to zero; later metric columns stay null."""
        run_id = await storage.runs.create_pipeline_run()
        await storage.runs.update_pipeline_run(run_id, scraping_seconds=5.0)
        await storage.runs.complete_pipeline_run(run_id, "completed")

        result = await storage.runs.get_last_pipeline_run()
        assert result is not None
        assert result["scraping_seconds"] == pytest.approx(5.0)
        unwritten = {*_progress_columns(), *_ASSESSMENT_AND_DELIVERY_COLUMNS} - {"scraping_seconds"}
        assert {column: result[column] for column in unwritten} == {
            column: 0 if column in _ORIGINAL_COUNT_COLUMNS else None for column in unwritten
        }

    async def test_later_updates_keep_earlier_metrics(self, storage) -> None:
        run_id = await storage.runs.create_pipeline_run()

        await storage.runs.update_pipeline_run(run_id, scraped_count=100)
        await storage.runs.update_pipeline_run(run_id, new_count=10, enriched_count=8)
        await storage.runs.update_pipeline_run(run_id, analyzed_count=8, notified_count=8)
        await storage.runs.complete_pipeline_run(run_id, "completed")

        result = await storage.runs.get_last_pipeline_run()
        assert result is not None
        assert (
            result["scraped_count"],
            result["new_count"],
            result["enriched_count"],
            result["analyzed_count"],
            result["notified_count"],
        ) == (100, 10, 8, 8, 8)

    async def test_update_without_metrics_leaves_the_run_unchanged(self, storage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        conn = await storage._get_connection()

        async def row():
            cursor = await conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,))
            return dict(await cursor.fetchone())

        before = await row()
        await storage.runs.update_pipeline_run(run_id)
        assert await row() == before


class TestCreatePipelineRun:
    @pytest.mark.asyncio
    async def test_creates_run_with_running_status(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        assert run_id is not None
        assert run_id > 0

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT status, started_at FROM pipeline_runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "running"
        assert row["started_at"] is not None

    @pytest.mark.asyncio
    async def test_creates_sequential_ids(self, storage: PropertyStorage) -> None:
        id1 = await storage.runs.create_pipeline_run()
        id2 = await storage.runs.create_pipeline_run()
        assert id2 == id1 + 1


class TestCompletePipelineRun:
    @pytest.mark.asyncio
    async def test_marks_completed(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        await storage.runs.complete_pipeline_run(run_id, "completed")

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT status, completed_at, duration_seconds FROM pipeline_runs WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        assert row["duration_seconds"] is not None
        assert row["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_marks_failed_with_error(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        await storage.runs.complete_pipeline_run(
            run_id, "failed", error_message="Connection timeout"
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT status, error_message FROM pipeline_runs WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "failed"
        assert row["error_message"] == "Connection timeout"


class TestGetLastPipelineRun:
    @pytest.mark.parametrize("status", ["partial", "cancelled"])
    async def test_latest_incomplete_run_is_not_hidden(
        self, storage: PropertyStorage, status: str
    ) -> None:
        previous = await storage.runs.create_pipeline_run()
        await storage.runs.complete_pipeline_run(previous, "completed")
        latest = await storage.runs.create_pipeline_run()
        await storage.runs.complete_pipeline_run(latest, status)
        result = await storage.runs.get_last_pipeline_run()
        assert result is not None
        assert (result["id"], result["status"]) == (latest, status)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_runs(self, storage: PropertyStorage) -> None:
        result = await storage.runs.get_last_pipeline_run()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_only_running(self, storage: PropertyStorage) -> None:
        await storage.runs.create_pipeline_run()  # Still running
        result = await storage.runs.get_last_pipeline_run()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_most_recent_completed(self, storage: PropertyStorage) -> None:
        id1 = await storage.runs.create_pipeline_run()
        await storage.runs.update_pipeline_run(id1, notified_count=3)
        await storage.runs.complete_pipeline_run(id1, "completed")

        id2 = await storage.runs.create_pipeline_run()
        await storage.runs.update_pipeline_run(id2, notified_count=7)
        await storage.runs.complete_pipeline_run(id2, "completed")

        result = await storage.runs.get_last_pipeline_run()
        assert result is not None
        assert result["id"] == id2
        assert result["notified_count"] == 7
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_returns_failed_runs(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        await storage.runs.complete_pipeline_run(run_id, "failed", error_message="boom")

        result = await storage.runs.get_last_pipeline_run()
        assert result is not None
        assert result["status"] == "failed"
        assert result["error_message"] == "boom"


class TestScraperRuns:
    """scraper_runs table persistence."""

    async def test_save_scraper_runs_persists(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        metrics = [
            {
                "scraper_name": "openrent",
                "started_at": "2026-02-23T10:00:00+00:00",
                "completed_at": "2026-02-23T10:00:12+00:00",
                "duration_seconds": 12.3,
                "areas_attempted": 3,
                "areas_completed": 3,
                "properties_found": 25,
                "pages_fetched": 6,
                "pages_failed": 0,
                "parse_errors": 0,
                "is_healthy": True,
                "error_message": None,
            },
            {
                "scraper_name": "zoopla",
                "started_at": "2026-02-23T10:00:12+00:00",
                "completed_at": "2026-02-23T10:00:30+00:00",
                "duration_seconds": 18.1,
                "areas_attempted": 3,
                "areas_completed": 2,
                "properties_found": 15,
                "pages_fetched": 4,
                "pages_failed": 1,
                "parse_errors": 0,
                "is_healthy": False,
                "error_message": None,
            },
        ]
        await storage.runs.save_scraper_runs(run_id, metrics)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM scraper_runs WHERE pipeline_run_id = ? ORDER BY id",
            (run_id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2

        openrent = dict(rows[0])
        assert openrent["scraper_name"] == "openrent"
        assert openrent["areas_attempted"] == 3
        assert openrent["areas_completed"] == 3
        assert openrent["properties_found"] == 25
        assert openrent["pages_fetched"] == 6
        assert openrent["pages_failed"] == 0
        assert openrent["is_healthy"] == 1  # SQLite stores bool as int
        assert openrent["duration_seconds"] == pytest.approx(12.3)

        zoopla = dict(rows[1])
        assert zoopla["scraper_name"] == "zoopla"
        assert zoopla["areas_completed"] == 2
        assert zoopla["is_healthy"] == 0

    async def test_save_scraper_runs_empty_list_is_noop(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        await storage.runs.save_scraper_runs(run_id, [])

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM scraper_runs WHERE pipeline_run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    async def test_save_scraper_runs_null_pipeline_run_id(self, storage: PropertyStorage) -> None:
        """Scraper runs can be saved without a pipeline run (e.g. dry-run)."""
        metrics = [
            {
                "scraper_name": "rightmove",
                "started_at": "2026-02-23T10:00:00+00:00",
                "properties_found": 10,
            },
        ]
        await storage.runs.save_scraper_runs(None, metrics)

        conn = await storage._get_connection()
        cursor = await conn.execute("SELECT * FROM scraper_runs WHERE pipeline_run_id IS NULL")
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["scraper_name"] == "rightmove"
        assert rows[0]["properties_found"] == 10

    async def test_separate_saves_attach_to_the_same_pipeline_run(
        self, storage: PropertyStorage
    ) -> None:
        """Each scraper saves its own metrics against the shared run."""
        run_id = await storage.runs.create_pipeline_run()
        for name in ["openrent", "rightmove", "zoopla", "onthemarket"]:
            await storage.runs.save_scraper_runs(
                run_id,
                [{"scraper_name": name, "started_at": "2026-02-23T10:00:00+00:00"}],
            )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM scraper_runs WHERE pipeline_run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 4


class TestPropertyEvents:
    """property_events table for pipeline audit trail."""

    async def test_insert_property_events_persists(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        events = [
            PropertyEvent("openrent:1", "openrent", "criteria_passed", "criteria"),
            PropertyEvent(
                "zoopla:2",
                "zoopla",
                "criteria_dropped",
                "criteria",
                {"price": 3000, "bedrooms": 3},
            ),
        ]
        await storage.runs.insert_property_events(run_id, events)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM property_events WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2

        first = dict(rows[0])
        assert first["property_id"] == "openrent:1"
        assert first["source"] == "openrent"
        assert first["event_type"] == "criteria_passed"
        assert first["stage"] == "criteria"
        assert first["metadata_json"] is None

        second = dict(rows[1])
        assert second["property_id"] == "zoopla:2"
        assert second["event_type"] == "criteria_dropped"

        meta = json.loads(second["metadata_json"])
        assert meta["price"] == 3000

    async def test_insert_empty_list_is_noop(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        await storage.runs.insert_property_events(run_id, [])

        conn = await storage._get_connection()
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM property_events")
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    async def test_cleanup_old_events_respects_keep_runs(self, storage: PropertyStorage) -> None:
        # Create 3 pipeline runs and add events to each
        run_ids = []
        for _ in range(3):
            rid = await storage.runs.create_pipeline_run()
            run_ids.append(rid)
            await storage.runs.insert_property_events(
                rid,
                [PropertyEvent("x:1", "x", "criteria_passed", "criteria")],
            )

        # Keep only the last 2 runs — run_ids[0] should be pruned
        deleted = await storage.runs.cleanup_old_events(keep_runs=2)
        assert deleted == 1

        conn = await storage._get_connection()
        cursor = await conn.execute("SELECT DISTINCT run_id FROM property_events ORDER BY run_id")
        remaining = [row["run_id"] for row in await cursor.fetchall()]
        assert run_ids[0] not in remaining
        assert run_ids[1] in remaining
        assert run_ids[2] in remaining

    async def test_cleanup_with_no_old_events(self, storage: PropertyStorage) -> None:
        run_id = await storage.runs.create_pipeline_run()
        await storage.runs.insert_property_events(
            run_id,
            [PropertyEvent("x:1", "x", "enriched", "enrichment")],
        )
        deleted = await storage.runs.cleanup_old_events(keep_runs=10)
        assert deleted == 0
