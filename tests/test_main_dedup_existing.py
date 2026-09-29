"""Integration tests for the --dedup-existing CLI command."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog.testing
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
)
from home_finder.pipeline.commands import run_dedup_existing
from home_finder.utils.image_cache import get_cache_dir, save_image_bytes


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        search_areas="e8",
        database_path=str(tmp_path / "properties.db"),
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


async def _run_with_storage(storage: PropertyStorage, settings: Settings) -> None:
    """Inject the fixture's storage, preserving its ownership boundary."""

    @asynccontextmanager
    async def existing_storage(db_path: str):  # type: ignore[no-untyped-def]
        yield storage

    with patch("home_finder.pipeline.commands.PropertyStorage", existing_storage):
        await run_dedup_existing(settings)


class TestRunDedupExisting:
    @pytest.mark.asyncio
    async def test_reports_established_duplicates_without_erasing_them(
        self,
        settings: Settings,
        merged_otm: MergedProperty,
        merged_rm: MergedProperty,
        tmp_path: Path,
    ) -> None:
        """Matching established homes need reviewed, reversible membership changes."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        await storage.save_merged_property(merged_otm)
        await storage.save_merged_property(merged_rm)

        # Create cached images for both (must be valid images to survive hash_from_disk)
        import io

        from PIL import Image as _Image

        _buf = io.BytesIO()
        _Image.new("RGB", (1, 1), color="red").save(_buf, format="JPEG")
        _valid_jpeg = _buf.getvalue()

        otm_dir = get_cache_dir(str(tmp_path), merged_otm.unique_id)
        rm_dir = get_cache_dir(str(tmp_path), merged_rm.unique_id)
        save_image_bytes(otm_dir / "gallery_000_aaa11111.jpg", _valid_jpeg)
        save_image_bytes(rm_dir / "gallery_000_bbb22222.jpg", _valid_jpeg)

        # Verify both exist before dedup
        all_props = await storage.get_all_properties()
        assert len(all_props) == 2

        with structlog.testing.capture_logs() as captured:
            await _run_with_storage(storage, settings)

        # Both identities, source memberships and cached inputs remain intact.
        remaining = await storage.get_all_properties()
        assert len(remaining) == 2

        conn = await storage._get_connection()
        cursor = await conn.execute("SELECT unique_id,merged_id FROM source_listings")
        assert dict(await cursor.fetchall()) == {
            merged_otm.unique_id: merged_otm.unique_id,
            merged_rm.unique_id: merged_rm.unique_id,
        }
        assert list(otm_dir.iterdir()) and list(rm_dir.iterdir())
        summary = next(event for event in captured if event["event"] == "dedup_complete")
        assert summary["proposed_absorptions"] == summary["deferred"] == 1
        assert summary["absorbed"] == 0 and summary["remaining"] == 2
        refusal = next(event for event in captured if event["event"] == "canonical_merge_deferred")
        assert any("established_property" in reasons for reasons in refusal["blockers"].values())

        await storage.close()

    @pytest.mark.asyncio
    async def test_no_duplicates_found(
        self,
        settings: Settings,
        merged_otm: MergedProperty,
    ) -> None:
        """When there are no duplicates, nothing should be merged."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        await storage.save_merged_property(merged_otm)

        with structlog.testing.capture_logs() as captured:
            await _run_with_storage(storage, settings)

        events = [e["event"] for e in captured]
        assert "dedup_no_duplicates_found" in events

        remaining = await storage.get_all_properties()
        assert len(remaining) == 1

        await storage.close()

    @pytest.mark.asyncio
    async def test_empty_database(
        self,
        settings: Settings,
    ) -> None:
        """Empty database should log message and return."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        with structlog.testing.capture_logs() as captured:
            await _run_with_storage(storage, settings)

        events = [e["event"] for e in captured]
        assert "dedup_no_properties_in_database" in events

        await storage.close()
