"""Advisory commute outcomes reach storage without false passes or lost homes."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import CommuteResult, CommuteState, TransportMode
from home_finder.pipeline.commands import run_backfill_commute
from home_finder.pipeline.event_recorder import EventRecorder
from home_finder.pipeline.stages import _geocode_and_compute_commute


@pytest.mark.parametrize(
    "scenario", ["unreachable", "outage", "no_config", "mixed", "missing_adapter_result"]
)
async def test_advisory_retention_and_durable_accounting(tmp_path, make_merged_property, scenario):
    homes = [make_merged_property(source_id=str(i)) for i in range(3)]
    settings = Settings(
        traveltime_app_id="" if scenario == "no_config" else "test",
        traveltime_api_key=SecretStr("test"),
    )
    criteria = settings.get_search_criteria()

    async def outcomes(properties, *, max_minutes, transport_mode):
        if scenario == "missing_adapter_result":
            return []
        results = []
        for index, prop in enumerate(properties):
            state = (
                CommuteState.UNREACHABLE
                if scenario == "unreachable"
                else CommuteState.PROVIDER_ERROR
            )
            seconds = None
            if scenario == "mixed":
                if index == 0:
                    seconds = 1800
                    state = CommuteState.WITHIN_LIMIT
                elif index == 1:
                    seconds = 1801
                    state = CommuteState.OVER_LIMIT
            results.append(
                CommuteResult(
                    property_id=prop.unique_id,
                    destination_postcode=criteria.destination_postcode,
                    transport_mode=transport_mode,
                    limit_seconds=max_minutes * 60,
                    state=state,
                    travel_time_seconds=seconds,
                )
            )
        return results

    adapter = MagicMock()
    adapter.geocode_properties = AsyncMock(side_effect=lambda homes: homes)
    adapter.filter_properties = AsyncMock(side_effect=outcomes)
    async with PropertyStorage(str(tmp_path / "commute.db")) as storage:
        run_id = await storage.pipeline.create_pipeline_run()
        recorder = EventRecorder(storage, run_id)
        with patch(
            "home_finder.pipeline.stages.CommuteFilter", return_value=adapter
        ) as constructor:
            retained, lookup, count = await _geocode_and_compute_commute(
                homes, criteria, settings, recorder=recorder
            )
        assert retained == homes
        assert count == (1 if scenario == "mixed" else 0)
        assert lookup == (
            {
                homes[0].unique_id: (30, TransportMode.CYCLING),
                homes[1].unique_id: (31, TransportMode.CYCLING),
            }
            if scenario == "mixed"
            else {}
        )
        await storage.pipeline.save_pre_analysis_properties(retained, lookup)
        async with storage._read_connection() as conn:
            events = await (
                await conn.execute(
                    "SELECT event_type, metadata_json FROM property_events "
                    "WHERE run_id = ? ORDER BY id",
                    (run_id,),
                )
            ).fetchall()
            projections = await (
                await conn.execute("SELECT commute_minutes FROM properties ORDER BY unique_id")
            ).fetchall()
        assert [row[0] for row in projections] == (
            [30, 31, None] if scenario == "mixed" else [None] * 3
        )
        assert [row[0] for row in events] == (
            ["commute_passed", "commute_over_limit", "commute_unknown"]
            if scenario == "mixed"
            else ["commute_unknown"] * 3
        )
        for _, raw_metadata in events:
            metadata = json.loads(raw_metadata)
            assert metadata["retained"] is True
            assert metadata["policy"] == "advisory"
        if scenario == "no_config":
            constructor.assert_not_called()
            assert all(json.loads(row[1])["reason"] == "not_configured" for row in events)
        else:
            assert adapter.filter_properties.await_count == len(criteria.transport_modes)


@pytest.mark.parametrize("seconds", [None, 1801])
async def test_backfill_writes_measurements_only(tmp_path, make_merged_property, seconds):
    path = str(tmp_path / "backfill.db")
    home = make_merged_property(latitude=51.54, longitude=-0.09)
    async with PropertyStorage(path) as storage:
        await storage.save_merged_property(home)
    settings = Settings(
        database_path=path, traveltime_app_id="test", traveltime_api_key=SecretStr("test")
    )

    async def outcomes(properties, *, max_minutes, transport_mode):
        return [
            CommuteResult(
                property_id=home.unique_id,
                destination_postcode=settings.destination_postcode,
                transport_mode=transport_mode,
                limit_seconds=max_minutes * 60,
                state=CommuteState.PROVIDER_ERROR if seconds is None else CommuteState.OVER_LIMIT,
                travel_time_seconds=seconds,
            )
        ]

    adapter = MagicMock(filter_properties=AsyncMock(side_effect=outcomes))
    with patch("home_finder.pipeline.commands.CommuteFilter", return_value=adapter):
        await run_backfill_commute(settings)
    async with PropertyStorage(path) as storage:
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT commute_minutes, transport_mode FROM properties")
            ).fetchone()
        assert tuple(row) == ((None, None) if seconds is None else (31, "cycling"))
