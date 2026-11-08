"""Initial assessment: completing a home, the assessment batch and estimated floor area."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from home_finder.analysis.analyzer import TokenUsage
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    TransportMode,
)
from home_finder.models.quality import RoomArea
from home_finder.pipeline.assessment import (
    complete_initial_assessment,
    persist_estimated_floor_area,
    process_initial_assessments,
)
from home_finder.pipeline.contracts import CandidateBatch


class TestCompleteInitialAssessment:
    async def test_completes_analysis(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        # Pre-save (as pipeline does before analysis)
        await storage.processing.save_pre_analysis_properties([merged], {})
        await complete_initial_assessment(merged, None, storage)

        count = await storage.web.get_property_count()
        assert count == 1
        # A skipped assessment finishes processing without establishing an alert.
        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status.value == "dashboard_only"

    async def test_preserves_commute_from_pre_save(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        commute_info = (18, TransportMode.CYCLING)
        # Pre-save with commute data
        await storage.processing.save_pre_analysis_properties(
            [merged], {merged.unique_id: commute_info}
        )
        await complete_initial_assessment(merged, None, storage)

        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.commute_minutes == 18
        assert tracked.transport_mode == TransportMode.CYCLING

    async def test_saves_quality_analysis(
        self,
        storage: PropertyStorage,
        sample_quality_analysis: PropertyQualityAnalysis,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        await storage.processing.save_pre_analysis_properties([merged], {})
        await complete_initial_assessment(merged, sample_quality_analysis, storage)

        # Verify quality analysis was stored
        detail = await storage.web.get_property_detail(merged.unique_id)
        assert detail is not None
        assert detail["quality_rating"] == 4


@patch("home_finder.pipeline.assessment._lookup_wards", new_callable=AsyncMock)
class TestInitialAssessments:
    async def test_disabled_analysis_completes_each_candidate_without_provider_calls(
        self,
        _mock_wards: AsyncMock,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        props = [make_merged_property(price_pcm=1800 + i * 10) for i in range(3)]
        batch = CandidateBatch(candidates=props, commute_lookup={})
        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key=SecretStr(""),
        )

        callback = AsyncMock()
        with patch("home_finder.pipeline.assessment.PropertyQualityAnalyzer") as analyzer_factory:
            summary = await process_initial_assessments(batch, settings, storage, callback)
        analyzer_factory.assert_not_called()
        assert summary.assessed == 0
        assert summary.skipped == len(props)

        assert summary.processed_count == 3
        assert summary.token_usage is None
        assert callback.await_count == 3

    async def test_callback_receives_correct_args(
        self,
        _mock_wards: AsyncMock,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        commute = (15, TransportMode.PUBLIC_TRANSPORT)
        batch = CandidateBatch(
            candidates=[merged],
            commute_lookup={merged.canonical.unique_id: commute},
        )
        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key=SecretStr(""),
        )

        received: list[tuple[Any, Any, Any]] = []

        async def capture(m: Any, c: Any, q: Any) -> None:
            received.append((m, c, q))

        summary = await process_initial_assessments(batch, settings, storage, capture)
        assert summary.assessed == 0
        assert summary.skipped == 1

        assert len(received) == 1
        assert summary.token_usage is None
        m, c, q = received[0]
        assert m.canonical.unique_id == merged.canonical.unique_id
        assert c == commute
        assert q is None  # quality disabled

    async def test_one_home_analysis_failure_leaves_it_pending_and_completes_other_homes(
        self, _mock_wards, storage, make_merged_property, make_quality_analysis
    ):
        homes = [make_merged_property(price_pcm=1800 + i * 10) for i in range(3)]
        batch = CandidateBatch(candidates=homes)
        settings = Settings(
            _env_file=None,
            search_areas="e8",
            enable_quality_filter=True,
            anthropic_api_key=SecretStr("synthetic-test-key"),
        )
        analyzer = AsyncMock()
        analyzer.__aenter__.return_value = analyzer
        analyzer.token_usage = TokenUsage()

        async def analyze(home, **kwargs):
            if home.unique_id == homes[0].unique_id:
                raise ValueError("One home's evidence could not be processed")
            return home, make_quality_analysis()

        analyzer.analyze_single_merged.side_effect = analyze
        callback = AsyncMock()
        with patch(
            "home_finder.pipeline.assessment.PropertyQualityAnalyzer", return_value=analyzer
        ):
            summary = await process_initial_assessments(batch, settings, storage, callback)

        assert summary.assessed == 2
        assert summary.failed == 1
        assert summary.skipped == summary.deferred == 0
        assert {call.args[0].unique_id for call in callback.await_args_list} == {
            home.unique_id for home in homes[1:]
        }
        pending = await storage.processing.get_pending_analysis_properties()
        assert [home.unique_id for home in pending] == [homes[0].unique_id]


class TestPersistEstimatedFloorArea:
    async def test_skips_when_floor_area_already_set(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """No DB update when merged.floor_area_sqm is already set."""
        merged = make_merged_property(floor_area_sqm=46.5)
        storage.update_floor_area = AsyncMock()  # type: ignore[method-assign]

        await persist_estimated_floor_area(merged, sample_quality_analysis, storage)
        storage.update_floor_area.assert_not_called()

    async def test_skips_when_analysis_is_none(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """No DB update when quality_analysis is None."""
        merged = make_merged_property()
        storage.update_floor_area = AsyncMock()  # type: ignore[method-assign]

        await persist_estimated_floor_area(merged, None, storage)
        storage.update_floor_area.assert_not_called()

    async def test_persists_valid_estimate(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """Persists estimate when space.total_area_sqm is within range."""
        merged = make_merged_property()
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=50.0))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm, floor_area_source FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqm"] == 50.0  # stored directly in sqm
        assert row["floor_area_source"] == "estimated"

    async def test_rejects_out_of_range(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """Does not persist when estimate is below 9 sqm."""
        merged = make_merged_property()
        # 5 sqm — below the 9.0 sqm floor
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=5.0))
        storage.update_floor_area = AsyncMock()  # type: ignore[method-assign]

        await persist_estimated_floor_area(merged, analysis, storage)
        storage.update_floor_area.assert_not_called()

    async def test_logs_bedroom_mismatch_warning(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Warns when floor area is outside expected range for bedroom count."""
        # 2-bed property with 200 sqm — way above expected (35-110)
        merged = make_merged_property(bedrooms=2)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=200.0))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_bedroom_mismatch" in (captured.out + captured.err)
        # Still persists (warning only)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqm"] == 200.0

    async def test_no_bedroom_mismatch_warning_when_in_range(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No warning when floor area is within expected range."""
        # 2-bed property with 60 sqm — within expected (35-110)
        merged = make_merged_property(bedrooms=2)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=60.0))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_bedroom_mismatch" not in (captured.out + captured.err)

    async def test_logs_room_sum_mismatch_warning(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Warns when room area sum significantly exceeds the total."""
        rooms = [
            RoomArea(name="Living Room", area_sqm=50.0),
            RoomArea(name="Bedroom", area_sqm=22.0),
        ]
        # room_sum=72, total=60 — excess of 12, exceeds max(5, 60*0.15)=9
        merged = make_merged_property(bedrooms=1)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=60.0, room_areas=rooms))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_sum_mismatch" in (captured.out + captured.err)
        # Still persists
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqm"] == 60.0

    @pytest.mark.parametrize(
        ("total", "known_area"), [(49.69, 13.8), (35.1, 3.56), (70.05, 20.15), (52.6, None)]
    )
    async def test_partial_or_withheld_room_areas_do_not_warn(
        self, storage, make_merged_property, make_quality_analysis, capsys, total, known_area
    ):
        merged = make_merged_property(bedrooms=1)
        analysis = make_quality_analysis(
            space=SpaceAnalysis(
                total_area_sqm=total,
                floorplan_total_area_sqm=total,
                room_areas=[
                    RoomArea(name="Measured room", area_sqm=known_area),
                    RoomArea(name="Withheld room", area_sqm=None),
                ],
            )
        )
        await storage.processing.save_pre_analysis_properties([merged], {})
        await persist_estimated_floor_area(merged, analysis, storage)
        captured = capsys.readouterr()
        assert "floor_area_sum_mismatch" not in captured.out + captured.err
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm FROM properties WHERE unique_id = ?", (merged.unique_id,)
        )
        assert (await cursor.fetchone())["floor_area_sqm"] == total

    async def test_no_room_sum_mismatch_when_close(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No warning when room sum is close to total."""
        rooms = [
            RoomArea(name="Living Room", area_sqm=20.0),
            RoomArea(name="Bedroom", area_sqm=12.0),
            RoomArea(name="Kitchen", area_sqm=8.0),
            RoomArea(name="Bathroom", area_sqm=5.0),
            RoomArea(name="Hallway", area_sqm=4.0),
        ]
        # room_sum=49, total=50 — diff of 1, within max(5, 50*0.15)=7.5
        merged = make_merged_property(bedrooms=1)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=50.0, room_areas=rooms))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_sum_mismatch" not in (captured.out + captured.err)
