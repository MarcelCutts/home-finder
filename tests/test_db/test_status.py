"""Tests for user status tracking (Ticket 7)."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
    UserStatus,
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


class TestUpdateUserStatus:
    @pytest.mark.asyncio
    async def test_returns_previous_status(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        prev = await storage.update_user_status(merged_a.unique_id, UserStatus.INTERESTED)
        assert prev == UserStatus.NEW

    @pytest.mark.asyncio
    async def test_updates_column(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.update_user_status(merged_a.unique_id, UserStatus.INTERESTED)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT user_status FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["user_status"] == "interested"

    @pytest.mark.asyncio
    async def test_nonexistent_returns_none(self, storage: PropertyStorage) -> None:
        result = await storage.update_user_status("fake:999", UserStatus.ARCHIVED)
        assert result is None

    @pytest.mark.asyncio
    async def test_default_new_status(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """New properties default to 'new' status."""
        await storage.save_merged_property(merged_a)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT user_status FROM properties WHERE unique_id = ?",
            (merged_a.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["user_status"] == "new"

    @pytest.mark.asyncio
    async def test_sequential_status_changes(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        prev1 = await storage.update_user_status(merged_a.unique_id, UserStatus.INTERESTED)
        assert prev1 == UserStatus.NEW
        prev2 = await storage.update_user_status(merged_a.unique_id, UserStatus.VIEWING_BOOKED)
        assert prev2 == UserStatus.INTERESTED


class TestGetStatusHistory:
    @pytest.mark.asyncio
    async def test_empty_history(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        history = await storage.get_status_history(merged_a.unique_id)
        assert history == []

    @pytest.mark.asyncio
    async def test_chronological_order(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.update_user_status(merged_a.unique_id, UserStatus.INTERESTED)
        await storage.update_user_status(merged_a.unique_id, UserStatus.VIEWING_BOOKED)
        history = await storage.get_status_history(merged_a.unique_id)
        assert len(history) == 2
        assert history[0]["from_status"] == "new"
        assert history[0]["to_status"] == "interested"
        assert history[1]["from_status"] == "interested"
        assert history[1]["to_status"] == "viewing_booked"

    @pytest.mark.asyncio
    async def test_source_tracking(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.update_user_status(
            merged_a.unique_id, UserStatus.INTERESTED, source="telegram"
        )
        history = await storage.get_status_history(merged_a.unique_id)
        assert len(history) == 1
        assert history[0]["source"] == "telegram"
