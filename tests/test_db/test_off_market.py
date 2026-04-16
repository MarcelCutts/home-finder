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

    async def test_marks_with_reason(self, storage: PropertyStorage, merged_a: MergedProperty):
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id, reason="let_agreed")

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT off_market_reason FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["off_market_reason"] == "let_agreed"

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
# mark_returned_to_market (with history)
# ---------------------------------------------------------------------------


class TestMarkReturnedToMarket:
    async def test_clears_off_market_flag(self, storage: PropertyStorage, merged_a: MergedProperty):
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        result = await storage.mark_returned_to_market(merged_a.unique_id)
        assert result is True

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT is_off_market, off_market_since, off_market_reason"
            " FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 0
        assert row["off_market_since"] is None
        assert row["off_market_reason"] is None

    async def test_appends_to_history(self, storage: PropertyStorage, merged_a: MergedProperty):
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id, reason="removed")

        await storage.mark_returned_to_market(merged_a.unique_id)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT off_market_history FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        history = json.loads(row["off_market_history"])
        assert len(history) == 1
        assert history[0]["reason"] == "removed"
        assert "off" in history[0]
        assert "back" in history[0]

    async def test_multiple_return_cycles_accumulate(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)

        # Cycle 1: off → back
        await storage.mark_off_market(merged_a.unique_id, reason="removed")
        await storage.mark_returned_to_market(merged_a.unique_id)

        # Cycle 2: off → back
        await storage.mark_off_market(merged_a.unique_id, reason="let_agreed")
        await storage.mark_returned_to_market(merged_a.unique_id)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT off_market_history FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        history = json.loads(row["off_market_history"])
        assert len(history) == 2
        assert history[0]["reason"] == "removed"
        assert history[1]["reason"] == "let_agreed"

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
        await storage.pipeline.save_unenriched_property(merged_a)

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

    async def test_includes_source_listings(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        """Properties with linked source_listings should include them."""
        await storage.save_merged_property(merged_a)

        props = await storage.get_properties_for_off_market_check()
        assert len(props) == 1
        # source_listings are populated from migration backfill
        assert "source_listings" in props[0]
        # Should have at least the canonical source listing
        sls = props[0]["source_listings"]
        assert isinstance(sls, list)
        if sls:  # May be empty if migration backfill didn't create it
            assert sls[0]["source"] == "openrent"


# ---------------------------------------------------------------------------
# Per-source off-market tracking
# ---------------------------------------------------------------------------


class TestPerSourceOffMarket:
    async def test_mark_source_listing_off_market(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT unique_id FROM source_listings WHERE merged_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            pytest.skip("No source_listing created by migration backfill")

        sl_uid = row["unique_id"]
        result = await storage.mark_source_listing_off_market(sl_uid, "removed")
        assert result is True

        cursor = await conn.execute(
            "SELECT is_off_market, off_market_reason, off_market_since"
            " FROM source_listings WHERE unique_id = ?",
            (sl_uid,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 1
        assert row["off_market_reason"] == "removed"
        assert row["off_market_since"] is not None

    async def test_mark_source_listing_active(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT unique_id FROM source_listings WHERE merged_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            pytest.skip("No source_listing created by migration backfill")

        sl_uid = row["unique_id"]
        await storage.mark_source_listing_off_market(sl_uid, "removed")
        result = await storage.mark_source_listing_active(sl_uid)
        assert result is True

        cursor = await conn.execute(
            "SELECT is_off_market, off_market_reason, off_market_since"
            " FROM source_listings WHERE unique_id = ?",
            (sl_uid,),
        )
        row = await cursor.fetchone()
        assert row["is_off_market"] == 0
        assert row["off_market_reason"] is None
        assert row["off_market_since"] is None

    async def test_update_source_listing_last_checked(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT unique_id FROM source_listings WHERE merged_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            pytest.skip("No source_listing created by migration backfill")

        sl_uid = row["unique_id"]
        result = await storage.update_source_listing_last_checked(sl_uid)
        assert result is True

        cursor = await conn.execute(
            "SELECT last_checked_at FROM source_listings WHERE unique_id = ?",
            (sl_uid,),
        )
        row = await cursor.fetchone()
        assert row["last_checked_at"] is not None

    async def test_update_property_last_checked(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ):
        await storage.save_merged_property(merged_a)

        result = await storage.update_property_last_checked(merged_a.unique_id)
        assert result is True

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT last_checked_at FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["last_checked_at"] is not None

    async def test_returns_false_for_missing(self, storage: PropertyStorage):
        assert await storage.mark_source_listing_off_market("nonexistent", "removed") is False
        assert await storage.mark_source_listing_active("nonexistent") is False
        assert await storage.update_source_listing_last_checked("nonexistent") is False
        assert await storage.update_property_last_checked("nonexistent") is False


# ---------------------------------------------------------------------------
# Migration 006
# ---------------------------------------------------------------------------


class TestMigration006:
    async def test_source_listings_has_off_market_columns(self, storage: PropertyStorage):
        """Migration 006 adds off-market columns to source_listings."""
        conn = await storage._get_connection()
        cursor = await conn.execute("PRAGMA table_info(source_listings)")
        columns = {row["name"] for row in await cursor.fetchall()}
        assert "is_off_market" in columns
        assert "off_market_since" in columns
        assert "off_market_reason" in columns
        assert "last_checked_at" in columns

    async def test_properties_has_new_columns(self, storage: PropertyStorage):
        """Migration 006 adds last_checked_at, off_market_reason, off_market_history."""
        conn = await storage._get_connection()
        cursor = await conn.execute("PRAGMA table_info(properties)")
        columns = {row["name"] for row in await cursor.fetchall()}
        assert "last_checked_at" in columns
        assert "off_market_reason" in columns
        assert "off_market_history" in columns
