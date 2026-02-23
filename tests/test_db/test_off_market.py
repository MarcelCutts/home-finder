"""Tests for off-market database methods."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import MergedProperty, Property, PropertySource

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prop_a() -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id="100",
        url=HttpUrl("https://openrent.com/100"),
        title="1 bed in E8",
        price_pcm=1900,
        bedrooms=1,
        address="10 Mare Street",
        postcode="E8 3RH",
    )


@pytest.fixture
def merged_a(prop_a: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_a,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
    )


@pytest.fixture
def multi_source_prop() -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id="200",
        url=HttpUrl("https://openrent.com/200"),
        title="2 bed in E3",
        price_pcm=2100,
        bedrooms=2,
        address="20 Victoria Park Rd",
        postcode="E3 5AA",
    )


@pytest.fixture
def multi_source_merged(multi_source_prop: Property) -> MergedProperty:
    return MergedProperty(
        canonical=multi_source_prop,
        sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.OPENRENT: multi_source_prop.url,
            PropertySource.ZOOPLA: HttpUrl("https://zoopla.co.uk/to-rent/details/200"),
        },
        min_price=2100,
        max_price=2150,
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# mark_off_market
# ---------------------------------------------------------------------------


class TestMarkOffMarket:
    async def test_marks_property_off_market(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)
        result = await storage.mark_off_market(merged_a.unique_id)
        assert result is True

        # Verify in DB
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market, off_market_since FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 1
        assert row["off_market_since"] is not None

    async def test_preserves_original_date_on_re_mark(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)

        # First mark
        await storage.mark_off_market(merged_a.unique_id)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT off_market_since FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        first_date = row["off_market_since"]

        # Second mark — should preserve original date via COALESCE
        await storage.mark_off_market(merged_a.unique_id)
        cursor = await conn.execute(
            "SELECT off_market_since FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["off_market_since"] == first_date

    async def test_returns_false_for_missing_property(self, storage: PropertyStorage):
        result = await storage.mark_off_market("nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# mark_returned_to_market
# ---------------------------------------------------------------------------


class TestMarkReturnedToMarket:
    async def test_clears_off_market_flag(self, storage: PropertyStorage, merged_a: MergedProperty):
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        result = await storage.mark_returned_to_market(merged_a.unique_id)
        assert result is True

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market, off_market_since FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 0
        assert row["off_market_since"] is None

    async def test_returns_false_for_missing_property(self, storage: PropertyStorage):
        result = await storage.mark_returned_to_market("nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# get_properties_for_off_market_check
# ---------------------------------------------------------------------------


class TestGetPropertiesForOffMarketCheck:
    async def test_returns_active_properties(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)
        # Mark as notified so it's past the pending state
        await storage.mark_notified(merged_a.unique_id)

        props = await storage.get_properties_for_off_market_check()
        assert len(props) == 1
        assert props[0]["unique_id"] == merged_a.unique_id

    async def test_excludes_pending_enrichment(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_unenriched_property(merged_a)

        props = await storage.get_properties_for_off_market_check()
        assert len(props) == 0

    async def test_filters_by_source(
        self,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        multi_source_merged: MergedProperty,
    ):
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(multi_source_merged)

        # Filter to zoopla only — merged_a is openrent-only, multi_source has zoopla
        props = await storage.get_properties_for_off_market_check(sources={"zoopla"})
        assert len(props) == 1
        assert props[0]["unique_id"] == multi_source_merged.unique_id

    async def test_returns_source_urls_json(
        self,
        storage: PropertyStorage,
        multi_source_merged: MergedProperty,
    ):
        await storage.save_merged_property(multi_source_merged)

        props = await storage.get_properties_for_off_market_check()
        assert len(props) == 1
        urls = json.loads(props[0]["source_urls"])
        assert "openrent" in urls
        assert "zoopla" in urls

    async def test_includes_off_market_status(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        props = await storage.get_properties_for_off_market_check()
        assert len(props) == 1
        assert props[0]["is_off_market"] == 1
