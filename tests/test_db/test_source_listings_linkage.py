"""Tests for source_listings linkage fixes (in-run dedup absorbed sources).

Verifies that non-canonical source_listings are linked to the golden record
when a merged property is saved, and that duplicate sources are deduplicated
when rebuilding golden records from source_listings.
"""

import json
from collections.abc import AsyncGenerator

import aiosqlite
import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.migrations import run_migrations
from home_finder.db.row_mappers import row_to_merged_property
from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
)


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    """Create an in-memory storage instance."""
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


def _openrent_prop(source_id: str = "or-1", price: int = 2000) -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id=source_id,
        url=HttpUrl(f"https://openrent.com/{source_id}"),
        title="Test flat",
        price_pcm=price,
        bedrooms=2,
        address="123 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
    )


def _zoopla_prop(source_id: str = "z-1", price: int = 1950) -> Property:
    return Property(
        source=PropertySource.ZOOPLA,
        source_id=source_id,
        url=HttpUrl(f"https://zoopla.co.uk/{source_id}"),
        title="Test flat",
        price_pcm=price,
        bedrooms=2,
        address="123 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        description="Zoopla description",
    )


class TestSaveMergedPropertyLinksNonCanonical:
    """save_merged_property links non-canonical source_listings by URL."""

    @pytest.mark.asyncio
    async def test_absorbed_source_listing_linked(self, storage: PropertyStorage) -> None:
        """When a merged property has multiple sources, non-canonical source_listings
        that were previously scraped (upsert_source_listings) get linked by URL."""
        canonical = _openrent_prop()
        zoopla = _zoopla_prop()

        # Simulate scrape-time write (no merged_id)
        await storage.upsert_source_listings([canonical, zoopla])

        # Verify both are unlinked
        assert await storage.is_seen(canonical.unique_id) is False
        assert await storage.is_seen(zoopla.unique_id) is False

        # In-run dedup merges them; save the merged result
        merged = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: canonical.url,
                PropertySource.ZOOPLA: zoopla.url,
            },
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=2000,
            descriptions={PropertySource.ZOOPLA: "Zoopla description"},
        )
        await storage.save_merged_property(merged)

        # Both should now be linked (seen)
        assert await storage.is_seen(canonical.unique_id) is True
        assert await storage.is_seen(zoopla.unique_id) is True

    @pytest.mark.asyncio
    async def test_no_error_when_no_non_canonical(self, storage: PropertyStorage) -> None:
        """Single-source merged property works fine (no non-canonical to link)."""
        prop = _openrent_prop()
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)
        assert await storage.is_seen(prop.unique_id) is True


class TestSavePreAnalysisLinksNonCanonical:
    """save_pre_analysis_properties links non-canonical source_listings."""

    @pytest.mark.asyncio
    async def test_absorbed_source_listing_linked(self, storage: PropertyStorage) -> None:
        canonical = _openrent_prop()
        zoopla = _zoopla_prop()

        # Scrape-time writes
        await storage.upsert_source_listings([canonical, zoopla])

        merged = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: canonical.url,
                PropertySource.ZOOPLA: zoopla.url,
            },
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=2000,
            descriptions={},
        )
        await storage.pipeline.save_pre_analysis_properties([merged], {})

        assert await storage.is_seen(canonical.unique_id) is True
        assert await storage.is_seen(zoopla.unique_id) is True


class TestSaveDroppedLinksNonCanonical:
    """save_dropped_properties links non-canonical source_listings."""

    @pytest.mark.asyncio
    async def test_absorbed_source_listing_linked(self, storage: PropertyStorage) -> None:
        canonical = _openrent_prop()
        zoopla = _zoopla_prop()

        await storage.upsert_source_listings([canonical, zoopla])

        merged = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: canonical.url,
                PropertySource.ZOOPLA: zoopla.url,
            },
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=2000,
            descriptions={},
        )
        await storage.pipeline.save_dropped_properties([merged], {})

        assert await storage.is_seen(canonical.unique_id) is True
        assert await storage.is_seen(zoopla.unique_id) is True


class TestUpdateMergedSourcesDeduplicates:
    """update_merged_sources deduplicates when multiple source_listings from same platform."""

    @pytest.mark.asyncio
    async def test_duplicate_sources_deduplicated(self, storage: PropertyStorage) -> None:
        """If two zoopla source_listings point to the same golden record,
        the rebuilt sources list should not have 'zoopla' twice."""
        canonical = _openrent_prop()
        zoopla1 = _zoopla_prop("z-1", 1950)
        zoopla2 = _zoopla_prop("z-2", 1900)

        # Save the initial golden record
        merged = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: canonical.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)

        # Simulate two zoopla scrape-time writes
        await storage.upsert_source_listings([zoopla1, zoopla2])

        # Link both zoopla listings to the golden record
        updated = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: canonical.url,
                PropertySource.ZOOPLA: zoopla2.url,
            },
            images=(),
            floorplan=None,
            min_price=1900,
            max_price=2000,
            descriptions={},
        )
        await storage.update_merged_sources(
            canonical.unique_id,
            updated,
            absorbed_ids=[zoopla1.unique_id, zoopla2.unique_id],
        )

        # Check that sources are deduplicated
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT sources FROM properties WHERE unique_id = ?",
            (canonical.unique_id,),
        )
        row = await cursor.fetchone()
        sources = json.loads(row["sources"])
        assert sources == ["openrent", "zoopla"]  # No duplicates


class TestRowToMergedPropertyDeduplicates:
    """row_to_merged_property deduplicates sources from both paths."""

    @pytest.mark.asyncio
    async def test_source_listings_path_deduplicates(
        self, storage: PropertyStorage
    ) -> None:
        """Duplicate sources in source_listings rows produce deduplicated MergedProperty."""
        canonical = _openrent_prop()
        zoopla1 = _zoopla_prop("z-1", 1950)
        zoopla2 = _zoopla_prop("z-2", 1900)

        # Save golden record and link all three source_listings
        merged = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: canonical.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)
        await storage.upsert_source_listings([zoopla1, zoopla2])

        # Manually link both zoopla listings
        conn = await storage._get_connection()
        await conn.execute(
            "UPDATE source_listings SET merged_id = ? WHERE unique_id IN (?, ?)",
            (canonical.unique_id, zoopla1.unique_id, zoopla2.unique_id),
        )
        await conn.commit()

        # Load as dedup anchor — should have deduplicated sources
        results = await storage.get_recent_properties_for_dedup(days=7)
        assert len(results) == 1
        result = results[0]
        source_values = [s.value for s in result.sources]
        assert source_values == ["openrent", "zoopla"]  # No duplicates
        assert result.min_price == 1900
        assert result.max_price == 2000

    @pytest.mark.asyncio
    async def test_json_fallback_path_deduplicates(
        self, storage: PropertyStorage
    ) -> None:
        """Duplicate sources in JSON columns produce deduplicated MergedProperty."""
        canonical = _openrent_prop()

        # Save with duplicated sources JSON
        merged = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: canonical.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)

        # Manually corrupt the sources JSON to have duplicates
        conn = await storage._get_connection()
        await conn.execute(
            "UPDATE properties SET sources = ? WHERE unique_id = ?",
            (json.dumps(["openrent", "zoopla", "zoopla"]), canonical.unique_id),
        )
        await conn.commit()

        # Load via JSON fallback path (no source_listings passed)
        cursor = await conn.execute(
            "SELECT * FROM properties WHERE unique_id = ?",
            (canonical.unique_id,),
        )
        row = await cursor.fetchone()
        result = await row_to_merged_property(row, load_images=False)
        source_values = [s.value for s in result.sources]
        assert source_values == ["openrent", "zoopla"]  # No duplicates


class TestInRunDedupIntegration:
    """Integration: in-run dedup → save → next run correctly skips absorbed property."""

    @pytest.mark.asyncio
    async def test_absorbed_property_skipped_on_next_run(
        self, storage: PropertyStorage
    ) -> None:
        """After in-run dedup merges two properties and saves, the absorbed
        property's source_listing has merged_id set, so filter_new_merged
        skips it on the next run."""
        canonical = _openrent_prop("or-int-1")
        absorbed = _zoopla_prop("z-int-1")

        # Run 1: Scrape both, simulate in-run dedup merge
        await storage.upsert_source_listings([canonical, absorbed])

        merged = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: canonical.url,
                PropertySource.ZOOPLA: absorbed.url,
            },
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)

        # Run 2: Both properties re-scraped
        await storage.upsert_source_listings([canonical, absorbed])

        # Wrap as single-source MergedProperty (pre-dedup step)
        canonical_wrapped = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: canonical.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        absorbed_wrapped = MergedProperty(
            canonical=absorbed,
            sources=(PropertySource.ZOOPLA,),
            source_urls={PropertySource.ZOOPLA: absorbed.url},
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=1950,
            descriptions={},
        )

        # filter_new_merged should skip both — they're already linked
        new = await storage.filter_new_merged([canonical_wrapped, absorbed_wrapped])
        assert len(new) == 0


class TestMigration005:
    """Tests for migrate_005_fix_source_listings_linkage."""

    @pytest.fixture
    async def conn(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        async with aiosqlite.connect(":memory:") as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    @pytest.mark.asyncio
    async def test_links_orphaned_source_listings(self, conn: aiosqlite.Connection) -> None:
        """Orphaned source_listings (merged_id=NULL) are linked by URL match."""
        # Run migrations up to 004
        from home_finder.db.migrations import (
            migrate_001_initial_schema,
            migrate_002_floor_area_sqm,
            migrate_003_source_aliases,
            migrate_004_source_listings,
        )

        for i, mig in enumerate(
            [migrate_001_initial_schema, migrate_002_floor_area_sqm,
             migrate_003_source_aliases, migrate_004_source_listings],
            1,
        ):
            await conn.execute("BEGIN IMMEDIATE")
            await mig(conn)
            await conn.execute(f"PRAGMA user_version = {i}")
            await conn.commit()

        # Insert a golden record with source_urls JSON referencing a zoopla URL
        source_urls = json.dumps({
            "openrent": "https://openrent.com/123",
            "zoopla": "https://zoopla.co.uk/456",
        })
        await conn.execute(
            """INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, first_seen, notification_status,
                sources, source_urls
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "openrent:123", "openrent", "123",
                "https://openrent.com/123", "Flat", 2000, 2,
                "123 Mare Street", "2025-01-15T10:00:00", "sent",
                json.dumps(["openrent", "zoopla"]),
                source_urls,
            ),
        )
        # Insert canonical source_listing (linked)
        await conn.execute(
            """INSERT INTO source_listings (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, first_seen, last_seen, merged_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "openrent:123", "openrent", "123",
                "https://openrent.com/123", "Flat", 2000, 2,
                "123 Mare Street", "2025-01-15T10:00:00", "2025-01-15T10:00:00",
                "openrent:123",
            ),
        )
        # Insert orphaned source_listing (merged_id=NULL, URL matches source_urls)
        await conn.execute(
            """INSERT INTO source_listings (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, first_seen, last_seen, merged_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "zoopla:456", "zoopla", "456",
                "https://zoopla.co.uk/456", "Flat", 1950, 2,
                "123 Mare Street", "2025-01-15T10:00:00", "2025-01-15T10:00:00",
                None,  # orphaned!
            ),
        )
        await conn.commit()

        # Run migration 005
        await run_migrations(conn)

        # Verify the orphaned source_listing is now linked
        cursor = await conn.execute(
            "SELECT merged_id FROM source_listings WHERE unique_id = ?",
            ("zoopla:456",),
        )
        row = await cursor.fetchone()
        assert row["merged_id"] == "openrent:123"

    @pytest.mark.asyncio
    async def test_deduplicates_sources_json(self, conn: aiosqlite.Connection) -> None:
        """Duplicate entries in sources JSON are cleaned up."""
        from home_finder.db.migrations import (
            migrate_001_initial_schema,
            migrate_002_floor_area_sqm,
            migrate_003_source_aliases,
            migrate_004_source_listings,
        )

        for i, mig in enumerate(
            [migrate_001_initial_schema, migrate_002_floor_area_sqm,
             migrate_003_source_aliases, migrate_004_source_listings],
            1,
        ):
            await conn.execute("BEGIN IMMEDIATE")
            await mig(conn)
            await conn.execute(f"PRAGMA user_version = {i}")
            await conn.commit()

        # Insert property with duplicated sources
        await conn.execute(
            """INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm,
                bedrooms, address, first_seen, notification_status, sources
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "rightmove:789", "rightmove", "789",
                "https://rightmove.co.uk/789", "Flat", 2100, 2,
                "45 Dalston Lane", "2025-01-16T14:00:00", "sent",
                json.dumps(["rightmove", "zoopla", "zoopla"]),
            ),
        )
        await conn.commit()

        await run_migrations(conn)

        cursor = await conn.execute(
            "SELECT sources FROM properties WHERE unique_id = ?",
            ("rightmove:789",),
        )
        row = await cursor.fetchone()
        sources = json.loads(row["sources"])
        assert sources == ["rightmove", "zoopla"]
