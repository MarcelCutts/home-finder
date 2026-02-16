"""Tests for run_reanalysis() orchestration in main.py.

Covers the async coordination logic, circuit breaker, error handling, and
early return paths. Uses file-backed SQLite for realistic DB interaction
and mocks PropertyQualityFilter as the single I/O boundary.
"""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.quality import APIUnavailableError
from home_finder.main import run_reanalysis
from home_finder.models import (
    MergedProperty,
    PropertyQualityAnalysis,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "reanalysis_test.db"


@pytest_asyncio.fixture
async def populated_storage(db_path: Path) -> PropertyStorage:
    """Create a file-backed storage, yield for pre-population, then close."""
    s = PropertyStorage(str(db_path))
    await s.initialize()
    yield s  # type: ignore[misc]
    await s.close()


@pytest.fixture
def reanalysis_settings(db_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="fake:token",
        telegram_chat_id=0,
        database_path=str(db_path),
        search_areas="e8",
        anthropic_api_key="fake-key",
        enable_quality_filter=True,
    )


async def _save_and_flag(
    storage: PropertyStorage,
    make_merged_property: Callable[..., MergedProperty],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    source_id: str = "z-1",
    postcode: str | None = "E8 3RH",
    rating: int = 4,
) -> MergedProperty:
    """Save an analyzed+notified property and flag it for reanalysis."""
    merged = make_merged_property(source_id=source_id, postcode=postcode)
    await storage.save_pre_analysis_properties([merged], {})
    await storage.complete_analysis(merged.unique_id, make_quality_analysis(rating=rating))
    await storage.mark_notified(merged.unique_id)
    await storage.request_reanalysis([merged.unique_id])
    return merged


# ---------------------------------------------------------------------------
# TestRunReanalysisRequestOnly
# ---------------------------------------------------------------------------


class TestRunReanalysisRequestOnly:
    async def test_request_only_flags_and_returns(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """request_only=True flags properties but never instantiates quality filter."""
        # Pre-populate 2 E8 properties
        for i in range(2):
            merged = make_merged_property(source_id=f"z-e8-{i}", postcode="E8 3RH")
            await populated_storage.save_pre_analysis_properties([merged], {})
            await populated_storage.complete_analysis(merged.unique_id, make_quality_analysis())
            await populated_storage.mark_notified(merged.unique_id)

        with patch("home_finder.main.PropertyQualityFilter") as mock_qf_cls:
            await run_reanalysis(
                reanalysis_settings,
                outcodes=["E8"],
                request_only=True,
            )
            mock_qf_cls.assert_not_called()

        # Verify flags were set
        queue = await populated_storage.get_reanalysis_queue()
        assert len(queue) == 2

    async def test_request_only_all(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """reanalyze_all=True with request_only flags all properties."""
        for sid, pc in [("z-e8", "E8 3RH"), ("z-e2", "E2 7QA"), ("z-n1", "N1 5AA")]:
            merged = make_merged_property(source_id=sid, postcode=pc)
            await populated_storage.save_pre_analysis_properties([merged], {})
            await populated_storage.complete_analysis(merged.unique_id, make_quality_analysis())
            await populated_storage.mark_notified(merged.unique_id)

        with patch("home_finder.main.PropertyQualityFilter") as mock_qf_cls:
            await run_reanalysis(
                reanalysis_settings,
                reanalyze_all=True,
                request_only=True,
            )
            mock_qf_cls.assert_not_called()

        queue = await populated_storage.get_reanalysis_queue()
        assert len(queue) == 3


# ---------------------------------------------------------------------------
# TestRunReanalysisEarlyReturns
# ---------------------------------------------------------------------------


class TestRunReanalysisEarlyReturns:
    async def test_empty_queue_no_analysis(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No flagged properties → early return without creating quality filter."""
        with patch("home_finder.main.PropertyQualityFilter") as mock_qf_cls:
            await run_reanalysis(reanalysis_settings)
            mock_qf_cls.assert_not_called()

        output = capsys.readouterr().out
        assert "No properties queued" in output

    async def test_no_api_key_returns_early(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Missing API key → early return with error message."""
        # Flag a property
        await _save_and_flag(populated_storage, make_merged_property, make_quality_analysis)

        from pydantic import SecretStr

        settings_no_key = reanalysis_settings.model_copy(
            update={"anthropic_api_key": SecretStr("")}
        )

        with patch("home_finder.main.PropertyQualityFilter") as mock_qf_cls:
            await run_reanalysis(settings_no_key)
            mock_qf_cls.assert_not_called()

        output = capsys.readouterr().out
        assert "not configured" in output


# ---------------------------------------------------------------------------
# TestRunReanalysisSuccess
# ---------------------------------------------------------------------------


class TestRunReanalysisSuccess:
    async def test_single_property_completed(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """Single flagged property gets reanalyzed successfully."""
        merged = await _save_and_flag(
            populated_storage, make_merged_property, make_quality_analysis
        )

        new_analysis = make_quality_analysis(rating=5)

        mock_filter = AsyncMock()
        mock_filter.analyze_single_merged = AsyncMock(return_value=(merged, new_analysis))
        mock_filter.close = AsyncMock()

        with patch("home_finder.main.PropertyQualityFilter", return_value=mock_filter):
            await run_reanalysis(reanalysis_settings)

        # Verify analysis updated
        stored = await populated_storage.get_quality_analysis(merged.unique_id)
        assert stored is not None
        assert stored.overall_rating == 5

        # Verify flag cleared
        queue = await populated_storage.get_reanalysis_queue()
        assert len(queue) == 0

        # Verify close() called
        mock_filter.close.assert_awaited_once()

    async def test_multiple_properties_all_succeed(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Multiple properties all reanalyzed successfully."""
        merged_list = []
        for i in range(3):
            m = await _save_and_flag(
                populated_storage, make_merged_property, make_quality_analysis,
                source_id=f"z-{i}", postcode="E8 3RH",
            )
            merged_list.append(m)

        async def _mock_analyze(merged: MergedProperty, *, data_dir: str | None = None):
            return (merged, make_quality_analysis(rating=5))

        mock_filter = AsyncMock()
        mock_filter.analyze_single_merged = AsyncMock(side_effect=_mock_analyze)
        mock_filter.close = AsyncMock()

        with patch("home_finder.main.PropertyQualityFilter", return_value=mock_filter):
            await run_reanalysis(reanalysis_settings)

        # All flags cleared
        queue = await populated_storage.get_reanalysis_queue()
        assert len(queue) == 0

        output = capsys.readouterr().out
        assert "3 updated" in output
        assert "0 failed" in output

    async def test_notification_status_unchanged(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """Reanalysis does not alter notification_status."""
        merged = await _save_and_flag(
            populated_storage, make_merged_property, make_quality_analysis
        )

        new_analysis = make_quality_analysis(rating=5)
        mock_filter = AsyncMock()
        mock_filter.analyze_single_merged = AsyncMock(return_value=(merged, new_analysis))
        mock_filter.close = AsyncMock()

        with patch("home_finder.main.PropertyQualityFilter", return_value=mock_filter):
            await run_reanalysis(reanalysis_settings)

        tracked = await populated_storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status.value == "sent"


# ---------------------------------------------------------------------------
# TestRunReanalysisErrorHandling
# ---------------------------------------------------------------------------


class TestRunReanalysisErrorHandling:
    async def test_api_unavailable_cancels_remaining(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """APIUnavailableError triggers circuit breaker — cancels remaining tasks."""
        merged_list = []
        for i in range(3):
            m = await _save_and_flag(
                populated_storage, make_merged_property, make_quality_analysis,
                source_id=f"z-{i}", postcode="E8 3RH",
            )
            merged_list.append(m)

        call_count = 0

        async def _mock_analyze(merged: MergedProperty, *, data_dir: str | None = None):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise APIUnavailableError("overloaded")
            return (merged, make_quality_analysis(rating=5))

        mock_filter = AsyncMock()
        mock_filter.analyze_single_merged = AsyncMock(side_effect=_mock_analyze)
        mock_filter.close = AsyncMock()

        with patch("home_finder.main.PropertyQualityFilter", return_value=mock_filter):
            await run_reanalysis(reanalysis_settings)

        # At least 1 should have completed
        queue = await populated_storage.get_reanalysis_queue()
        completed_count = 3 - len(queue)
        assert completed_count >= 1
        # close() must still be called
        mock_filter.close.assert_awaited_once()

    async def test_generic_exception_continues_others(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """RuntimeError on one property doesn't stop the rest."""
        await _save_and_flag(
            populated_storage, make_merged_property, make_quality_analysis,
            source_id="z-fail", postcode="E8 3RH",
        )
        await _save_and_flag(
            populated_storage, make_merged_property, make_quality_analysis,
            source_id="z-ok", postcode="E8 4AA",
        )

        call_count = 0

        async def _mock_analyze(merged: MergedProperty, *, data_dir: str | None = None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            return (merged, make_quality_analysis(rating=5))

        mock_filter = AsyncMock()
        mock_filter.analyze_single_merged = AsyncMock(side_effect=_mock_analyze)
        mock_filter.close = AsyncMock()

        with patch("home_finder.main.PropertyQualityFilter", return_value=mock_filter):
            await run_reanalysis(reanalysis_settings)

        output = capsys.readouterr().out
        assert "1 updated" in output
        assert "1 failed" in output

    async def test_none_analysis_counts_as_failed(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """analyze_single_merged returning None analysis counts as failure."""
        merged = await _save_and_flag(
            populated_storage, make_merged_property, make_quality_analysis
        )

        mock_filter = AsyncMock()
        mock_filter.analyze_single_merged = AsyncMock(return_value=(merged, None))
        mock_filter.close = AsyncMock()

        with patch("home_finder.main.PropertyQualityFilter", return_value=mock_filter):
            await run_reanalysis(reanalysis_settings)

        output = capsys.readouterr().out
        assert "0 updated" in output
        assert "1 failed" in output

        # Flag should still be set
        queue = await populated_storage.get_reanalysis_queue()
        assert len(queue) == 1

    async def test_close_called_on_api_error(
        self,
        populated_storage: PropertyStorage,
        reanalysis_settings: Settings,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """close() is called even when every analysis raises APIUnavailableError."""
        await _save_and_flag(populated_storage, make_merged_property, make_quality_analysis)

        mock_filter = AsyncMock()
        mock_filter.analyze_single_merged = AsyncMock(side_effect=APIUnavailableError("down"))
        mock_filter.close = AsyncMock()

        with patch("home_finder.main.PropertyQualityFilter", return_value=mock_filter):
            await run_reanalysis(reanalysis_settings)

        mock_filter.close.assert_awaited_once()
