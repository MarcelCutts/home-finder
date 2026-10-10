"""Tests for price history and rent benchmarks (Ticket 10)."""

import json
import sqlite3
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
    ValueAnalysis,
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
        result = await storage.detect_and_record_price_change(merged_a.unique_id, 1900)
        assert result is None

    @pytest.mark.asyncio
    async def test_drop_returns_negative(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        result = await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        assert result == -150

    @pytest.mark.asyncio
    async def test_increase_returns_positive(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        result = await storage.detect_and_record_price_change(merged_a.unique_id, 2000)
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

    @pytest.mark.parametrize("new_price", [1750, 2100])
    async def test_price_range_and_source_quote_change_atomically(
        self, storage, merged_a, new_price
    ):
        await storage.save_merged_property(merged_a)
        await storage.detect_and_record_price_change(merged_a.unique_id, new_price)
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT price_pcm, min_price, max_price FROM properties")
            ).fetchone()
            quote = await (await conn.execute("SELECT price_pcm FROM source_listings")).fetchone()
        assert tuple(row) == (new_price, new_price, new_price)
        assert quote[0] == new_price

    async def test_retained_linked_source_prices_survive_canonical_change(self, storage, merged_a):
        await storage.save_merged_property(merged_a)
        other = merged_a.canonical.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "other",
                "price_pcm": 2150,
                "url": HttpUrl("https://www.zoopla.co.uk/to-rent/details/456/"),
            }
        )
        await storage.upsert_source_listings([other])
        await storage.link_source_listings([(other.unique_id, merged_a.unique_id)])
        await storage.detect_and_record_price_change(merged_a.unique_id, 1750, source="openrent")
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT price_pcm, min_price, max_price FROM properties")
            ).fetchone()
            quotes = await (
                await conn.execute("SELECT price_pcm FROM source_listings ORDER BY price_pcm")
            ).fetchall()
        assert tuple(row) == (1750, 1750, 2150)
        assert [r[0] for r in quotes] == [1750, 2150]
        assert len(await storage.get_price_history(merged_a.unique_id)) == 1

    async def test_different_portal_price_is_not_a_same_listing_drop(self, storage, merged_a):
        await storage.save_merged_property(merged_a)
        assert (
            await storage.detect_and_record_price_change(merged_a.unique_id, 1500, source="zoopla")
            is None
        )
        assert await storage.get_price_history(merged_a.unique_id) == []
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT price_pcm, min_price, max_price FROM properties")
            ).fetchone()
        assert tuple(row) == (1900, 1900, 1900)

    async def test_history_failure_rolls_back_source_quote_and_range(self, storage, merged_a):
        await storage.save_merged_property(merged_a)
        async with storage._transaction() as conn:
            await conn.execute(
                "CREATE TRIGGER fail_history BEFORE INSERT ON price_history "
                "BEGIN SELECT RAISE(ABORT, 'injected history failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected history failure"):
            await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT price_pcm, min_price, max_price FROM properties")
            ).fetchone()
            quote = await (await conn.execute("SELECT price_pcm FROM source_listings")).fetchone()
        assert tuple(row) == (1900, 1900, 1900)
        assert quote[0] == 1900
        assert await storage.get_price_history(merged_a.unique_id) == []

    async def test_cross_platform_selection_does_not_rewrite_canonical_quote(
        self, storage, merged_a
    ):
        await storage.save_merged_property(merged_a)
        other = merged_a.canonical.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "cheaper",
                "price_pcm": 1750,
                "url": HttpUrl("https://www.zoopla.co.uk/to-rent/details/456/"),
            }
        )
        await storage.upsert_source_listings([other])
        await storage.link_source_listings([(other.unique_id, merged_a.unique_id)])
        assert (
            await storage.detect_and_record_price_change(
                merged_a.unique_id, 1750, source="cross_platform"
            )
            == -150
        )
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT price_pcm, min_price, max_price FROM properties")
            ).fetchone()
            quotes = await (
                await conn.execute("SELECT source, price_pcm FROM source_listings ORDER BY source")
            ).fetchall()
        assert tuple(row) == (1750, 1750, 1900)
        assert [tuple(r) for r in quotes] == [("openrent", 1900), ("zoopla", 1750)]
        assert (await storage.get_price_history(merged_a.unique_id))[0][
            "source"
        ] == "cross_platform"

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
    async def test_empty(self, storage: PropertyStorage, merged_a: MergedProperty) -> None:
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


def _make_analysis(value: ValueAnalysis | None = None) -> PropertyQualityAnalysis:
    """Build a minimal PropertyQualityAnalysis for testing."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(),
        condition=ConditionAnalysis(),
        light_space=LightSpaceAnalysis(),
        space=SpaceAnalysis(),
        summary="Nice flat",
        value=value,
    )


class TestValueRecomputeOnPriceChange:
    @pytest.mark.asyncio
    async def test_price_change_recomputes_value_rating(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        analysis = _make_analysis(
            value=ValueAnalysis(
                area_average=1800,
                difference=100,
                rating="fair",
                note="Above average",
                quality_adjusted_rating="fair",
                quality_adjusted_note="Condition matches price",
            ),
        )
        await storage.save_quality_analysis(merged_a.unique_id, analysis)

        result = await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        assert result == -150

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT analysis_json FROM quality_analyses WHERE property_unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        data = json.loads(row["analysis_json"])
        # Rating should have been recomputed for the new price
        assert data["value"]["rating"] is not None
        # quality_adjusted_rating should be cleared (stale LLM assessment)
        assert data["value"]["quality_adjusted_rating"] is None
        assert data["value"]["quality_adjusted_note"] == ""

    @pytest.mark.asyncio
    async def test_price_change_recomputes_fit_score(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        analysis = _make_analysis(
            value=ValueAnalysis(
                area_average=1800,
                difference=100,
                rating="fair",
                note="Above average",
                quality_adjusted_rating="fair",
                quality_adjusted_note="Condition matches price",
            ),
        )
        await storage.save_quality_analysis(merged_a.unique_id, analysis)

        # Record original fit_score
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT fit_score FROM quality_analyses WHERE property_unique_id = ?",
            (merged_a.unique_id,),
        )
        original = await cursor.fetchone()
        original_score = original["fit_score"]

        # Price drop should trigger fit_score recompute
        await storage.detect_and_record_price_change(merged_a.unique_id, 1750)

        cursor = await conn.execute(
            "SELECT fit_score FROM quality_analyses WHERE property_unique_id = ?",
            (merged_a.unique_id,),
        )
        updated = await cursor.fetchone()
        # fit_score should have changed (lower price = better value = different score)
        assert updated["fit_score"] != original_score

    @pytest.mark.asyncio
    async def test_no_crash_without_quality_analysis(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        # No quality analysis saved — should not crash
        result = await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        assert result == -150

    @pytest.mark.asyncio
    async def test_cleared_quality_adjusted_rating(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        analysis = _make_analysis(
            value=ValueAnalysis(
                area_average=1800,
                difference=100,
                rating="fair",
                note="Above average",
                quality_adjusted_rating="good",
                quality_adjusted_note="Quality lifts value",
            ),
        )
        await storage.save_quality_analysis(merged_a.unique_id, analysis)

        await storage.detect_and_record_price_change(merged_a.unique_id, 1750)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT analysis_json FROM quality_analyses WHERE property_unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        data = json.loads(row["analysis_json"])
        assert data["value"]["quality_adjusted_rating"] is None
