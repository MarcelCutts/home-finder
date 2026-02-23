"""Tests for observability: funnel counts, scraper runs, API costs, and property events."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from home_finder.db.storage import PropertyStorage
from home_finder.pipeline.event_recorder import PropertyEvent


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


class TestFunnelCounts:
    """T3: Funnel count columns on pipeline_runs."""

    async def test_funnel_counts_stored_via_update(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.update_pipeline_run(
            run_id,
            criteria_filtered_count=50,
            location_filtered_count=45,
            new_property_count=30,
            commute_within_limit_count=20,
            post_dedup_count=18,
            post_floorplan_count=15,
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            """SELECT criteria_filtered_count, location_filtered_count,
                      new_property_count, commute_within_limit_count,
                      post_dedup_count, post_floorplan_count
               FROM pipeline_runs WHERE id = ?""",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["criteria_filtered_count"] == 50
        assert row["location_filtered_count"] == 45
        assert row["new_property_count"] == 30
        assert row["commute_within_limit_count"] == 20
        assert row["post_dedup_count"] == 18
        assert row["post_floorplan_count"] == 15

    async def test_funnel_counts_nullable(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.update_pipeline_run(run_id, criteria_filtered_count=10)
        await storage.pipeline.complete_pipeline_run(run_id, "completed")

        result = await storage.pipeline.get_last_pipeline_run()
        assert result is not None
        assert result["criteria_filtered_count"] == 10
        assert result["location_filtered_count"] is None
        assert result["new_property_count"] is None
        assert result["commute_within_limit_count"] is None
        assert result["post_dedup_count"] is None
        assert result["post_floorplan_count"] is None

    async def test_funnel_counts_returned_by_get_last(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.update_pipeline_run(
            run_id,
            criteria_filtered_count=100,
            location_filtered_count=90,
            new_property_count=50,
            commute_within_limit_count=40,
            post_dedup_count=35,
            post_floorplan_count=30,
        )
        await storage.pipeline.complete_pipeline_run(run_id, "completed")

        result = await storage.pipeline.get_last_pipeline_run()
        assert result is not None
        assert result["criteria_filtered_count"] == 100
        assert result["location_filtered_count"] == 90
        assert result["new_property_count"] == 50
        assert result["commute_within_limit_count"] == 40
        assert result["post_dedup_count"] == 35
        assert result["post_floorplan_count"] == 30


class TestApiCostColumns:
    """T1: API cost aggregate columns on pipeline_runs."""

    async def test_token_counts_stored(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.update_pipeline_run(
            run_id,
            total_input_tokens=50000,
            total_output_tokens=10000,
            total_cache_read_tokens=40000,
            total_cache_creation_tokens=5000,
            estimated_cost_usd=0.285,
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            """SELECT total_input_tokens, total_output_tokens,
                      total_cache_read_tokens, total_cache_creation_tokens,
                      estimated_cost_usd
               FROM pipeline_runs WHERE id = ?""",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["total_input_tokens"] == 50000
        assert row["total_output_tokens"] == 10000
        assert row["total_cache_read_tokens"] == 40000
        assert row["total_cache_creation_tokens"] == 5000
        assert row["estimated_cost_usd"] == pytest.approx(0.285)

    async def test_token_counts_nullable(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.complete_pipeline_run(run_id, "completed")

        result = await storage.pipeline.get_last_pipeline_run()
        assert result is not None
        assert result["total_input_tokens"] is None
        assert result["total_output_tokens"] is None
        assert result["total_cache_read_tokens"] is None
        assert result["total_cache_creation_tokens"] is None
        assert result["estimated_cost_usd"] is None


class TestScraperRuns:
    """T2: scraper_runs table persistence."""

    async def test_save_scraper_runs_persists(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
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
        await storage.pipeline.save_scraper_runs(run_id, metrics)

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

    async def test_save_scraper_runs_empty_list_is_noop(
        self, storage: PropertyStorage
    ) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.save_scraper_runs(run_id, [])

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM scraper_runs WHERE pipeline_run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    async def test_save_scraper_runs_null_pipeline_run_id(
        self, storage: PropertyStorage
    ) -> None:
        """Scraper runs can be saved without a pipeline run (e.g. dry-run)."""
        metrics = [
            {
                "scraper_name": "rightmove",
                "started_at": "2026-02-23T10:00:00+00:00",
                "properties_found": 10,
            },
        ]
        await storage.pipeline.save_scraper_runs(None, metrics)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM scraper_runs WHERE pipeline_run_id IS NULL"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["scraper_name"] == "rightmove"
        assert rows[0]["properties_found"] == 10

    async def test_scraper_runs_foreign_key(self, storage: PropertyStorage) -> None:
        """Multiple scraper runs can reference the same pipeline run."""
        run_id = await storage.pipeline.create_pipeline_run()
        for name in ["openrent", "rightmove", "zoopla", "onthemarket"]:
            await storage.pipeline.save_scraper_runs(
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
    """T4: property_events table for pipeline audit trail."""

    async def test_insert_property_events_persists(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        events = [
            PropertyEvent("openrent:1", "openrent", "criteria_passed", "criteria"),
            PropertyEvent(
                "zoopla:2", "zoopla", "criteria_dropped", "criteria",
                {"price": 3000, "bedrooms": 3},
            ),
        ]
        await storage.pipeline.insert_property_events(run_id, events)

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
        import json

        meta = json.loads(second["metadata_json"])
        assert meta["price"] == 3000

    async def test_insert_empty_list_is_noop(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.insert_property_events(run_id, [])

        conn = await storage._get_connection()
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM property_events")
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    async def test_cleanup_old_events_respects_keep_runs(
        self, storage: PropertyStorage
    ) -> None:
        # Create 3 pipeline runs and add events to each
        run_ids = []
        for _ in range(3):
            rid = await storage.pipeline.create_pipeline_run()
            run_ids.append(rid)
            await storage.pipeline.insert_property_events(
                rid,
                [PropertyEvent("x:1", "x", "criteria_passed", "criteria")],
            )

        # Keep only the last 2 runs — run_ids[0] should be pruned
        deleted = await storage.pipeline.cleanup_old_events(keep_runs=2)
        assert deleted == 1

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT DISTINCT run_id FROM property_events ORDER BY run_id"
        )
        remaining = [row["run_id"] for row in await cursor.fetchall()]
        assert run_ids[0] not in remaining
        assert run_ids[1] in remaining
        assert run_ids[2] in remaining

    async def test_cleanup_with_no_old_events(self, storage: PropertyStorage) -> None:
        run_id = await storage.pipeline.create_pipeline_run()
        await storage.pipeline.insert_property_events(
            run_id,
            [PropertyEvent("x:1", "x", "enriched", "enrichment")],
        )
        deleted = await storage.pipeline.cleanup_old_events(keep_runs=10)
        assert deleted == 0
