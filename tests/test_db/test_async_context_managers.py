"""Tests for async context manager support on resource classes."""

from unittest.mock import AsyncMock

import pytest

from home_finder.db.storage import PropertyStorage
from home_finder.filters.off_market import OffMarketChecker
from home_finder.filters.quality import PropertyQualityFilter
from home_finder.notifiers.telegram import TelegramNotifier
from home_finder.scrapers.detail_fetcher import DetailFetcher


class TestPropertyStorageContextManager:
    @pytest.mark.asyncio
    async def test_calls_close_on_exit(self) -> None:
        storage = PropertyStorage(":memory:")
        async with storage:
            conn = await storage._get_connection()
            assert conn is not None  # initialize() was called
        assert storage._conn is None  # close() was called


class TestDetailFetcherContextManager:
    @pytest.mark.asyncio
    async def test_calls_close_on_exit(self) -> None:
        fetcher = DetailFetcher()
        fetcher.close = AsyncMock()  # type: ignore[method-assign]
        async with fetcher:
            pass
        fetcher.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_close_on_exception(self) -> None:
        fetcher = DetailFetcher()
        fetcher.close = AsyncMock()  # type: ignore[method-assign]
        with pytest.raises(ValueError):
            async with fetcher:
                raise ValueError("boom")
        fetcher.close.assert_awaited_once()


class TestPropertyQualityFilterContextManager:
    @pytest.mark.asyncio
    async def test_calls_close_on_exit(self) -> None:
        qf = PropertyQualityFilter(api_key="fake-key")
        qf.close = AsyncMock()  # type: ignore[method-assign]
        async with qf:
            pass
        qf.close.assert_awaited_once()


class TestTelegramNotifierContextManager:
    @pytest.mark.asyncio
    async def test_calls_close_on_exit(self) -> None:
        notifier = TelegramNotifier(bot_token="fake:token", chat_id=0, web_base_url="", data_dir="")
        notifier.close = AsyncMock()  # type: ignore[method-assign]
        async with notifier:
            pass
        notifier.close.assert_awaited_once()


class TestOffMarketCheckerContextManager:
    @pytest.mark.asyncio
    async def test_calls_close_on_exit(self) -> None:
        checker = OffMarketChecker()
        checker.close = AsyncMock()  # type: ignore[method-assign]
        async with checker:
            pass
        checker.close.assert_awaited_once()
