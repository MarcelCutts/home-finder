"""Tests for price history and rent benchmarks (Ticket 10)."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
)


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


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


class TestDetectAndRecordPriceChange:
    @pytest.mark.asyncio
    async def test_no_change_returns_none(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        result = await storage.detect_and_record_price_change(
            merged_a.unique_id, 1900
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drop_returns_negative(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        result = await storage.detect_and_record_price_change(
            merged_a.unique_id, 1750
        )
        assert result == -150

    @pytest.mark.asyncio
    async def test_increase_returns_positive(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        result = await storage.detect_and_record_price_change(
            merged_a.unique_id, 2000
        )
        assert result == 100

    @pytest.mark.asyncio
    async def test_new_property_returns_none(self, storage: PropertyStorage) -> None:
        result = await storage.detect_and_record_price_change("fake:999", 1500)
        assert result is None

    @pytest.mark.asyncio
    async def test_updates_price_pcm(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT price_pcm FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["price_pcm"] == 1750

    @pytest.mark.asyncio
    async def test_resets_notified_flag(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        # First drop + mark notified
        await storage.detect_and_record_price_change(merged_a.unique_id, 1800)
        await storage.mark_price_drop_notified(merged_a.unique_id)
        # Second drop resets flag
        await storage.detect_and_record_price_change(merged_a.unique_id, 1700)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT price_drop_notified FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["price_drop_notified"] == 0


class TestGetPriceHistory:
    @pytest.mark.asyncio
    async def test_empty(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        history = await storage.get_price_history(merged_a.unique_id)
        assert history == []

    @pytest.mark.asyncio
    async def test_multiple_events_returned(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.detect_and_record_price_change(merged_a.unique_id, 1800)
        await storage.detect_and_record_price_change(merged_a.unique_id, 1700)
        history = await storage.get_price_history(merged_a.unique_id)
        assert len(history) == 2
        new_prices = {h["new_price"] for h in history}
        assert new_prices == {1700, 1800}


class TestGetUnsentPriceDrops:
    @pytest.mark.asyncio
    async def test_returns_drops_for_notified_properties(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_notified(merged_a.unique_id)
        await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        drops = await storage.get_unsent_price_drops()
        assert len(drops) == 1
        assert drops[0]["unique_id"] == merged_a.unique_id
        assert drops[0]["change_amount"] == -150

    @pytest.mark.asyncio
    async def test_mark_price_drop_notified(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_notified(merged_a.unique_id)
        await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        await storage.mark_price_drop_notified(merged_a.unique_id)
        drops = await storage.get_unsent_price_drops()
        assert drops == []
