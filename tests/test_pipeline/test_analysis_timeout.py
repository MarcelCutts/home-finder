"""Tests for per-property wall-clock timeout in _run_concurrent_analysis."""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from home_finder.config import Settings
from home_finder.models import MergedProperty, PropertyQualityAnalysis
from home_finder.pipeline.analysis import _PropertyTimeoutError, _run_concurrent_analysis


@pytest.fixture
def make_items(
    make_merged_property: Callable[..., MergedProperty],
) -> Callable[[int], list[MergedProperty]]:
    """Factory that creates N distinct MergedProperty items."""

    def _make(n: int) -> list[MergedProperty]:
        return [make_merged_property(source_id=f"timeout-test-{i}") for i in range(n)]

    return _make


async def _instant_analyze(
    merged: MergedProperty,
) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
    """Analyze function that returns immediately with no quality analysis."""
    return merged, None


async def _slow_analyze(
    merged: MergedProperty,
) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
    """Analyze function that sleeps forever (simulates timeout)."""
    await asyncio.sleep(999)
    return merged, None  # pragma: no cover


async def test_wall_clock_timeout_skips_property(
    make_items: Callable[[int], list[MergedProperty]],
) -> None:
    """A slow property times out and returns count=0."""
    items = make_items(1)
    results: list[tuple[MergedProperty, PropertyQualityAnalysis | None]] = []

    async def _on_result(
        merged: MergedProperty, qa: PropertyQualityAnalysis | None
    ) -> None:
        results.append((merged, qa))

    count = await _run_concurrent_analysis(
        items,
        _slow_analyze,
        _on_result,
        per_property_timeout=0.05,
    )
    assert count == 0
    assert len(results) == 0


async def test_timeout_does_not_cancel_batch(
    make_items: Callable[[int], list[MergedProperty]],
) -> None:
    """One slow + two fast properties: 2 succeed, slow is skipped."""
    items = make_items(3)
    slow_id = items[0].unique_id

    async def _mixed_analyze(
        merged: MergedProperty,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
        if merged.unique_id == slow_id:
            await asyncio.sleep(999)
            return merged, None  # pragma: no cover
        return merged, None

    results: list[str] = []

    async def _on_result(
        merged: MergedProperty, qa: PropertyQualityAnalysis | None
    ) -> None:
        results.append(merged.unique_id)

    count = await _run_concurrent_analysis(
        items,
        _mixed_analyze,
        _on_result,
        per_property_timeout=0.1,
        concurrency=10,
    )
    assert count == 2
    assert slow_id not in results


async def test_duration_logging_on_success(
    make_items: Callable[[int], list[MergedProperty]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """property_analysis_duration log emitted on successful analysis."""
    items = make_items(1)

    async def _on_result(
        merged: MergedProperty, qa: PropertyQualityAnalysis | None
    ) -> None:
        pass

    count = await _run_concurrent_analysis(
        items,
        _instant_analyze,
        _on_result,
        per_property_timeout=5.0,
    )

    assert count == 1
    captured = capsys.readouterr()
    assert "property_analysis_duration" in captured.out


async def test_on_error_called_for_timeout(
    make_items: Callable[[int], list[MergedProperty]],
) -> None:
    """on_error callback is invoked when a property times out."""
    items = make_items(1)
    error_cb = MagicMock()

    async def _on_result(
        merged: MergedProperty, qa: PropertyQualityAnalysis | None
    ) -> None:
        pass  # pragma: no cover

    count = await _run_concurrent_analysis(
        items,
        _slow_analyze,
        _on_result,
        per_property_timeout=0.05,
        on_error=error_cb,
    )
    assert count == 0
    error_cb.assert_called_once()


async def test_no_timeout_when_none(
    make_items: Callable[[int], list[MergedProperty]],
) -> None:
    """per_property_timeout=None means no cap (backward compat)."""
    items = make_items(2)
    results: list[str] = []

    async def _on_result(
        merged: MergedProperty, qa: PropertyQualityAnalysis | None
    ) -> None:
        results.append(merged.unique_id)

    count = await _run_concurrent_analysis(
        items,
        _instant_analyze,
        _on_result,
        per_property_timeout=None,
    )
    assert count == 2
    assert len(results) == 2


class TestPropertyAnalysisTimeoutSettings:
    """Config validation: 30-900s range enforced."""

    def test_default_value(self) -> None:
        s = Settings(search_areas="e8")
        assert s.property_analysis_timeout == 600.0

    def test_custom_value(self) -> None:
        s = Settings(search_areas="e8", property_analysis_timeout=60.0)
        assert s.property_analysis_timeout == 60.0

    def test_minimum_bound(self) -> None:
        with pytest.raises(ValidationError):
            Settings(search_areas="e8", property_analysis_timeout=10.0)

    def test_maximum_bound(self) -> None:
        with pytest.raises(ValidationError):
            Settings(search_areas="e8", property_analysis_timeout=1000.0)

    def test_boundary_30(self) -> None:
        s = Settings(search_areas="e8", property_analysis_timeout=30.0)
        assert s.property_analysis_timeout == 30.0

    def test_boundary_900(self) -> None:
        s = Settings(search_areas="e8", property_analysis_timeout=900.0)
        assert s.property_analysis_timeout == 900.0


class TestPropertyTimeoutError:
    """Unit tests for the _PropertyTimeoutError exception."""

    def test_stores_property_id(self) -> None:
        err = _PropertyTimeoutError("prop-123")
        assert err.property_id == "prop-123"

    def test_is_not_base_exception(self) -> None:
        """Timeout error extends Exception, not BaseException."""
        err = _PropertyTimeoutError("prop-123")
        assert isinstance(err, Exception)
        assert not isinstance(err, KeyboardInterrupt)
