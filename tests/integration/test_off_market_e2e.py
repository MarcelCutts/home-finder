"""Integration test for the run_check_off_market CLI wiring.

Tests the full flow: DB → OffMarketChecker → aggregation → DB updates,
with the HTTP layer mocked to avoid real network calls.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.filters.off_market import CheckResult, ListingStatus
from home_finder.models import MergedProperty, Property, PropertySource


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


def _make_merged(
    source: PropertySource,
    source_id: str,
    *,
    extra_sources: dict[PropertySource, str] | None = None,
) -> MergedProperty:
    url = f"https://{source.value}.co.uk/{source_id}"
    prop = Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(url),
        title=f"Test {source_id}",
        price_pcm=1800,
        bedrooms=1,
        address="10 Test Road",
        postcode="E8 3RH",
    )
    source_urls = {source: prop.url}
    sources = [source]
    if extra_sources:
        for extra_src, extra_url in extra_sources.items():
            source_urls[extra_src] = HttpUrl(extra_url)
            sources.append(extra_src)
    return MergedProperty(
        canonical=prop,
        sources=tuple(sources),
        source_urls=source_urls,
        min_price=1800,
        max_price=1800,
    )


def _mock_settings(proxy_url: str = "") -> MagicMock:
    settings = MagicMock()
    settings.proxy_url = proxy_url
    settings.database_path = ":memory:"  # not used — we patch PropertyStorage
    return settings


class TestRunCheckOffMarketIntegration:
    """End-to-end tests for run_check_off_market aggregation logic.

    Patches PropertyStorage to reuse the in-memory fixture and
    OffMarketChecker.check_batch to avoid real HTTP.
    """

    async def _run(
        self,
        storage: PropertyStorage,
        mock_results: list[CheckResult],
        *,
        only_scrapers: set[str] | None = None,
    ) -> None:
        """Invoke run_check_off_market with patched storage and checker."""
        from home_finder.pipeline.commands import run_check_off_market

        settings = _mock_settings()

        # Patch PropertyStorage so run_check_off_market reuses our fixture
        # instead of creating a new connection (which can't share :memory:)
        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage
        storage.initialize = AsyncMock()  # already initialized
        original_close = storage.close
        storage.close = AsyncMock()  # prevent closing our fixture

        with (
            patch("home_finder.pipeline.commands.PropertyStorage", mock_storage_cls),
            patch(
                "home_finder.filters.off_market.OffMarketChecker.check_batch",
                new_callable=AsyncMock,
                return_value=mock_results,
            ),
            patch(
                "home_finder.filters.off_market.OffMarketChecker.close",
                new_callable=AsyncMock,
            ),
        ):
            await run_check_off_market(settings, only_scrapers=only_scrapers)

        storage.close = original_close  # restore for fixture teardown

    async def test_single_source_removed_marks_off_market(
        self, storage: PropertyStorage
    ):
        merged = _make_merged(PropertySource.OPENRENT, "100")
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await self._run(
            storage,
            [
                CheckResult(
                    source="openrent",
                    url=str(merged.canonical.url),
                    status=ListingStatus.REMOVED,
                    property_id=merged.unique_id,
                )
            ],
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 1

    async def test_multi_source_partial_stays_on_market(
        self, storage: PropertyStorage
    ):
        merged = _make_merged(
            PropertySource.OPENRENT,
            "200",
            extra_sources={
                PropertySource.ZOOPLA: "https://zoopla.co.uk/to-rent/200"
            },
        )
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await self._run(
            storage,
            [
                CheckResult(
                    source="openrent",
                    url="https://openrent.co.uk/200",
                    status=ListingStatus.REMOVED,
                    property_id=merged.unique_id,
                ),
                CheckResult(
                    source="zoopla",
                    url="https://zoopla.co.uk/to-rent/200",
                    status=ListingStatus.ACTIVE,
                    property_id=merged.unique_id,
                ),
            ],
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 0

    async def test_previously_off_market_returns(self, storage: PropertyStorage):
        merged = _make_merged(PropertySource.RIGHTMOVE, "300")
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)
        await storage.mark_off_market(merged.unique_id)

        await self._run(
            storage,
            [
                CheckResult(
                    source="rightmove",
                    url=str(merged.canonical.url),
                    status=ListingStatus.ACTIVE,
                    property_id=merged.unique_id,
                )
            ],
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market, off_market_since FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 0
        assert row["off_market_since"] is None

    async def test_all_unknown_does_not_flag(self, storage: PropertyStorage):
        merged = _make_merged(PropertySource.ZOOPLA, "400")
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await self._run(
            storage,
            [
                CheckResult(
                    source="zoopla",
                    url=str(merged.canonical.url),
                    status=ListingStatus.UNKNOWN,
                    property_id=merged.unique_id,
                )
            ],
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 0
