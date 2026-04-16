"""Integration test for the run_check_off_market CLI wiring.

Tests the full flow: DB -> OffMarketChecker -> aggregation -> DB updates,
with the HTTP layer mocked to avoid real network calls.

Covers: per-source persistence on source_listings, mixed-linkage fallback,
return-to-market history, and LET_AGREED handling.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.filters.off_market import BatchResult, CheckResult, ListingStatus
from home_finder.models import MergedProperty, Property, PropertySource


def _make_batch_result(results: list[CheckResult]) -> BatchResult:
    """Build a BatchResult from a flat list of CheckResults."""
    by_source: dict[str, dict[str, int]] = {}
    for r in results:
        counts = by_source.setdefault(r.source, {s.value: 0 for s in ListingStatus})
        counts[r.status.value] += 1
    return BatchResult(results=results, by_source=by_source, circuit_breakers_tripped=[])


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
                return_value=_make_batch_result(mock_results),
            ),
            patch(
                "home_finder.filters.off_market.OffMarketChecker.close",
                new_callable=AsyncMock,
            ),
        ):
            await run_check_off_market(settings, only_scrapers=only_scrapers)

        storage.close = original_close  # restore for fixture teardown

    async def test_single_source_removed_marks_off_market(self, storage: PropertyStorage):
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
            "SELECT is_off_market, off_market_reason FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 1
        assert row["off_market_reason"] == "removed"

    async def test_multi_source_partial_stays_on_market(self, storage: PropertyStorage):
        merged = _make_merged(
            PropertySource.OPENRENT,
            "200",
            extra_sources={PropertySource.ZOOPLA: "https://zoopla.co.uk/to-rent/200"},
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
        await storage.mark_off_market(merged.unique_id, reason="removed")

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
            "SELECT is_off_market, off_market_since, off_market_history"
            " FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 0
        assert row["off_market_since"] is None
        # History should have one entry
        history = json.loads(row["off_market_history"])
        assert len(history) == 1
        assert history[0]["reason"] == "removed"

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

    async def test_let_agreed_marks_off_market_with_reason(self, storage: PropertyStorage):
        """LET_AGREED status should be treated as inactive with reason."""
        merged = _make_merged(PropertySource.RIGHTMOVE, "500")
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await self._run(
            storage,
            [
                CheckResult(
                    source="rightmove",
                    url=str(merged.canonical.url),
                    status=ListingStatus.LET_AGREED,
                    property_id=merged.unique_id,
                )
            ],
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market, off_market_reason FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 1
        assert row["off_market_reason"] == "let_agreed"

    async def test_stamps_last_checked_at_on_golden_record(self, storage: PropertyStorage):
        """last_checked_at should be stamped on the golden record."""
        merged = _make_merged(PropertySource.OPENRENT, "600")
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await self._run(
            storage,
            [
                CheckResult(
                    source="openrent",
                    url=str(merged.canonical.url),
                    status=ListingStatus.ACTIVE,
                    property_id=merged.unique_id,
                )
            ],
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT last_checked_at FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["last_checked_at"] is not None

    async def test_partial_linkage_checks_unlinked_source_urls(self, storage: PropertyStorage):
        """When some source_listings exist but source_urls has extra URLs, both are checked."""
        merged = _make_merged(
            PropertySource.OPENRENT,
            "750",
            extra_sources={PropertySource.ZOOPLA: "https://zoopla.co.uk/to-rent/750"},
        )
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        # Simulate partial linkage: upsert a source_listing for OpenRent only
        openrent_prop = Property(
            source=PropertySource.OPENRENT,
            source_id="750",
            url=HttpUrl("https://openrent.co.uk/750"),
            title="Test 750",
            price_pcm=1800,
            bedrooms=1,
            address="10 Test Road",
            postcode="E8 3RH",
        )
        await storage.upsert_source_listings([openrent_prop])
        await storage.link_source_listings(
            [(openrent_prop.unique_id, merged.unique_id)],
        )

        # Mock check_batch to capture what checks were requested
        captured_checks: list[tuple[str, str, str]] = []

        async def _capture_batch(checks: list[tuple[str, str, str]]) -> BatchResult:
            captured_checks.extend(checks)
            results = [
                CheckResult(
                    source=source,
                    url=url,
                    status=ListingStatus.ACTIVE,
                    property_id=prop_id,
                )
                for prop_id, source, url in checks
            ]
            return _make_batch_result(results)

        from home_finder.pipeline.commands import run_check_off_market

        settings = _mock_settings()
        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage
        storage.initialize = AsyncMock()
        original_close = storage.close
        storage.close = AsyncMock()

        with (
            patch("home_finder.pipeline.commands.PropertyStorage", mock_storage_cls),
            patch(
                "home_finder.filters.off_market.OffMarketChecker.check_batch",
                new_callable=AsyncMock,
                side_effect=_capture_batch,
            ),
            patch(
                "home_finder.filters.off_market.OffMarketChecker.close",
                new_callable=AsyncMock,
            ),
        ):
            await run_check_off_market(settings)

        storage.close = original_close

        # Should have checks for BOTH sources — the linked OpenRent AND the unlinked Zoopla
        checked_sources = {(src, url) for _, src, url in captured_checks}
        assert ("openrent", "https://openrent.co.uk/750") in checked_sources
        assert ("zoopla", "https://zoopla.co.uk/to-rent/750") in checked_sources

    async def test_mixed_removed_and_let_agreed_marks_off_market(self, storage: PropertyStorage):
        """If one source is REMOVED and another LET_AGREED, both inactive -> off-market."""
        merged = _make_merged(
            PropertySource.RIGHTMOVE,
            "700",
            extra_sources={PropertySource.ZOOPLA: "https://zoopla.co.uk/to-rent/700"},
        )
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await self._run(
            storage,
            [
                CheckResult(
                    source="rightmove",
                    url=str(merged.canonical.url),
                    status=ListingStatus.LET_AGREED,
                    property_id=merged.unique_id,
                ),
                CheckResult(
                    source="zoopla",
                    url="https://zoopla.co.uk/to-rent/700",
                    status=ListingStatus.REMOVED,
                    property_id=merged.unique_id,
                ),
            ],
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market, off_market_reason FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 1
        # Reason from first inactive source (let_agreed takes precedence)
        assert row["off_market_reason"] == "let_agreed"
