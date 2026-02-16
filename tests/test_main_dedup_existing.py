"""Integration tests for the --dedup-existing CLI command."""

import json
from pathlib import Path

import pytest
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.main import run_dedup_existing
from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
)
from home_finder.utils.image_cache import get_cache_dir, save_image_bytes


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="fake:token",
        telegram_chat_id=0,
        search_areas="e8",
        database_path=":memory:",
        data_dir=str(tmp_path),
    )


@pytest.fixture
def prop_otm() -> Property:
    """OnTheMarket listing for a property at 42 Mare Street."""
    return Property(
        source=PropertySource.ONTHEMARKET,
        source_id="18751817",
        url=HttpUrl("https://www.onthemarket.com/details/18751817/"),
        title="2 bed flat, Mare Street, E8",
        price_pcm=2200,
        bedrooms=2,
        address="42 Mare Street, Hackney, London",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        image_url=HttpUrl("https://cdn.example.com/otm_thumb.jpg"),
    )


@pytest.fixture
def prop_rm() -> Property:
    """Rightmove listing for the same property at 42 Mare Street."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="172249586",
        url=HttpUrl("https://www.rightmove.co.uk/properties/172249586"),
        title="2 bed flat to rent in Mare Street, Hackney, E8",
        price_pcm=2200,
        bedrooms=2,
        address="Mare Street, Hackney, London E8 3RH",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        image_url=HttpUrl("https://cdn.example.com/rm_thumb.jpg"),
    )


@pytest.fixture
def merged_otm(prop_otm: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_otm,
        sources=(PropertySource.ONTHEMARKET,),
        source_urls={PropertySource.ONTHEMARKET: prop_otm.url},
        min_price=2200,
        max_price=2200,
        descriptions={PropertySource.ONTHEMARKET: "Lovely 2 bed flat."},
    )


@pytest.fixture
def merged_rm(prop_rm: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_rm,
        sources=(PropertySource.RIGHTMOVE,),
        source_urls={PropertySource.RIGHTMOVE: prop_rm.url},
        min_price=2200,
        max_price=2200,
        descriptions={PropertySource.RIGHTMOVE: "Spacious 2 bed flat near Mare Street."},
    )


async def _run_with_storage(
    storage: PropertyStorage, settings: Settings
) -> None:
    """Run run_dedup_existing sharing the given storage's in-memory DB connection."""
    original_init = PropertyStorage.__init__

    def patched_init(self_storage: PropertyStorage, db_path: str) -> None:
        self_storage.db_path = db_path
        self_storage._conn = storage._conn
        self_storage._ensure_directory()
        from home_finder.db.pipeline_repo import PipelineRepository
        from home_finder.db.web_queries import WebQueryService

        self_storage._web = WebQueryService(
            self_storage._get_connection, self_storage.get_property_images
        )
        self_storage._pipeline = PipelineRepository(
            self_storage._get_connection,
            self_storage.get_property_images,
            self_storage.save_quality_analysis,
        )

    original_close = PropertyStorage.close

    async def patched_close(self_storage: PropertyStorage) -> None:
        pass

    PropertyStorage.__init__ = patched_init  # type: ignore[assignment]
    PropertyStorage.close = patched_close  # type: ignore[assignment]
    try:
        await run_dedup_existing(settings)
    finally:
        PropertyStorage.__init__ = original_init  # type: ignore[assignment]
        PropertyStorage.close = original_close  # type: ignore[assignment]


class TestRunDedupExisting:
    @pytest.mark.asyncio
    async def test_merges_duplicate_properties(
        self,
        settings: Settings,
        merged_otm: MergedProperty,
        merged_rm: MergedProperty,
        tmp_path: Path,
    ) -> None:
        """Two properties at the same address/postcode/price should be merged."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        await storage.save_merged_property(merged_otm)
        await storage.save_merged_property(merged_rm)

        # Create cached images for both
        otm_dir = get_cache_dir(str(tmp_path), merged_otm.unique_id)
        rm_dir = get_cache_dir(str(tmp_path), merged_rm.unique_id)
        save_image_bytes(otm_dir / "gallery_000_aaa11111.jpg", b"otm_image")
        save_image_bytes(rm_dir / "gallery_000_bbb22222.jpg", b"rm_image")

        # Verify both exist before dedup
        all_props = await storage.get_all_properties()
        assert len(all_props) == 2

        await _run_with_storage(storage, settings)

        # One property should remain, the other absorbed
        remaining = await storage.get_all_properties()
        assert len(remaining) == 1

        # The winner should have both sources in its sources JSON
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT sources FROM properties WHERE unique_id = ?",
            (remaining[0].property.unique_id,),
        )
        row = await cursor.fetchone()
        sources = json.loads(row["sources"]) if row["sources"] else []
        assert len(sources) >= 2
        assert "onthemarket" in sources
        assert "rightmove" in sources

        # Winner's image cache should have at least its own files
        winner_dir = get_cache_dir(str(tmp_path), remaining[0].property.unique_id)
        assert winner_dir.is_dir()
        cached_files = list(winner_dir.iterdir())
        assert len(cached_files) >= 1

        await storage.close()

    @pytest.mark.asyncio
    async def test_no_duplicates_found(
        self,
        settings: Settings,
        merged_otm: MergedProperty,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When there are no duplicates, nothing should be merged."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        await storage.save_merged_property(merged_otm)

        await _run_with_storage(storage, settings)

        captured = capsys.readouterr()
        assert "No duplicates found" in captured.out

        remaining = await storage.get_all_properties()
        assert len(remaining) == 1

        await storage.close()

    @pytest.mark.asyncio
    async def test_empty_database(
        self,
        settings: Settings,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty database should print message and return."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        await _run_with_storage(storage, settings)

        captured = capsys.readouterr()
        assert "No properties in database" in captured.out

        await storage.close()
