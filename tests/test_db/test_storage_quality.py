"""Tests for quality analysis and paginated query storage methods."""

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
    PropertyImage,
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
        title="Nice 1 bed flat in E8",
        price_pcm=1900,
        bedrooms=1,
        address="10 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
    )


@pytest.fixture
def prop_b() -> Property:
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="200",
        url=HttpUrl("https://rightmove.co.uk/200"),
        title="Lovely 2 bed in E3",
        price_pcm=2100,
        bedrooms=2,
        address="20 Roman Road",
        postcode="E3 5LU",
        latitude=51.5300,
        longitude=-0.0400,
    )


@pytest.fixture
def prop_c() -> Property:
    return Property(
        source=PropertySource.ZOOPLA,
        source_id="300",
        url=HttpUrl("https://zoopla.co.uk/300"),
        title="Studio in N16",
        price_pcm=1600,
        bedrooms=0,
        address="30 Stoke Newington Rd",
        postcode="N16 7XJ",
    )


@pytest.fixture
def sample_analysis() -> PropertyQualityAnalysis:
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher="yes"),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
        space=SpaceAnalysis(living_room_sqm=18.0, is_spacious_enough=True, confidence="high"),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2200, difference=-300, rating="excellent", note="Below avg"
        ),
        overall_rating=4,
        summary="Bright flat with modern kitchen and good natural light.",
    )


@pytest.fixture
def merged_a(prop_a: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_a,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
        descriptions={PropertySource.OPENRENT: "A lovely flat near the park."},
    )


@pytest.fixture
def merged_b(prop_b: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_b,
        sources=(PropertySource.RIGHTMOVE, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.RIGHTMOVE: prop_b.url,
            PropertySource.ZOOPLA: HttpUrl("https://zoopla.co.uk/999"),
        },
        min_price=2050,
        max_price=2100,
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s


class TestSaveAndGetQualityAnalysis:
    @pytest.mark.asyncio
    async def test_roundtrip(
        self,
        storage: PropertyStorage,
        prop_a: Property,
        sample_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_property(prop_a)
        await storage.save_quality_analysis(prop_a.unique_id, sample_analysis)

        result = await storage.get_quality_analysis(prop_a.unique_id)
        assert result is not None
        assert result.overall_rating == 4
        assert result.summary == "Bright flat with modern kitchen and good natural light."
        assert result.kitchen.hob_type == "gas"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, storage: PropertyStorage) -> None:
        result = await storage.get_quality_analysis("nonexistent:999")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(
        self,
        storage: PropertyStorage,
        prop_a: Property,
        sample_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_property(prop_a)
        await storage.save_quality_analysis(prop_a.unique_id, sample_analysis)

        # Create updated analysis
        updated = PropertyQualityAnalysis(
            kitchen=sample_analysis.kitchen,
            condition=ConditionAnalysis(
                overall_condition="fair",
                confidence="high",
                has_visible_damp="yes",
                maintenance_concerns=["damp patch in bathroom"],
            ),
            light_space=sample_analysis.light_space,
            space=sample_analysis.space,
            condition_concerns=True,
            concern_severity="moderate",
            overall_rating=3,
            summary="Some damp concerns.",
        )
        await storage.save_quality_analysis(prop_a.unique_id, updated)

        result = await storage.get_quality_analysis(prop_a.unique_id)
        assert result is not None
        assert result.overall_rating == 3
        assert result.condition_concerns is True

    @pytest.mark.asyncio
    async def test_migration_fixes_wrapped_one_line(
        self,
        prop_a: Property,
    ) -> None:
        """DB migration should unwrap one_line stored as JSON object."""
        # Use a fresh storage to insert bad data before migration
        storage = PropertyStorage(":memory:")
        conn = await storage._get_connection()
        # Create minimal schema without running full initialize()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS properties (
                unique_id TEXT PRIMARY KEY, source TEXT NOT NULL,
                source_id TEXT NOT NULL, url TEXT NOT NULL,
                title TEXT NOT NULL, price_pcm INTEGER NOT NULL,
                bedrooms INTEGER NOT NULL, address TEXT NOT NULL,
                postcode TEXT, latitude REAL, longitude REAL,
                description TEXT, image_url TEXT, available_from TEXT,
                first_seen TEXT NOT NULL, commute_minutes INTEGER,
                transport_mode TEXT,
                notification_status TEXT NOT NULL DEFAULT 'pending',
                notified_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sources TEXT, source_urls TEXT, min_price INTEGER, max_price INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS property_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_unique_id TEXT NOT NULL, source TEXT NOT NULL,
                url TEXT NOT NULL, image_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS quality_analyses (
                property_unique_id TEXT PRIMARY KEY,
                analysis_json TEXT NOT NULL, overall_rating INTEGER,
                condition_concerns BOOLEAN DEFAULT 0, concern_severity TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.commit()

        # Save property, then manually insert bad analysis_json
        await storage.save_property(prop_a)
        bad_json = (
            '{"one_line": {"one_line": "Bright flat"}, "summary": "Good",'
            ' "condition_concerns": false, "kitchen": {"overall_quality": "modern",'
            ' "notes": ""}, "condition": {"overall_condition": "good",'
            ' "has_visible_damp": false, "has_visible_mold": false,'
            ' "has_worn_fixtures": false, "maintenance_concerns": [],'
            ' "confidence": "high"}, "light_space": {"natural_light": "good",'
            ' "notes": ""}, "space": {"confidence": "low"}}'
        )
        await conn.execute(
            "INSERT INTO quality_analyses"
            " (property_unique_id, analysis_json, created_at)"
            " VALUES (?, ?, '2026-01-01')",
            (prop_a.unique_id, bad_json),
        )
        await conn.commit()

        # Verify it's bad
        cursor = await conn.execute(
            "SELECT json_type(json_extract(analysis_json, '$.one_line')) FROM quality_analyses"
        )
        row = await cursor.fetchone()
        assert row[0] == "object"

        # Run initialize() which includes the migration
        await storage.initialize()

        # Verify it's fixed
        cursor = await conn.execute(
            "SELECT json_extract(analysis_json, '$.one_line') FROM quality_analyses"
        )
        row = await cursor.fetchone()
        assert row[0] == "Bright flat"

        await storage.close()


class TestGetPropertiesPaginated:
    @pytest.mark.asyncio
    async def test_empty_database(self, storage: PropertyStorage) -> None:
        props, total = await storage.get_properties_paginated()
        assert props == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_returns_properties(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.get_properties_paginated()
        assert total == 2
        assert len(props) == 2

    @pytest.mark.asyncio
    async def test_sort_price_asc(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, _ = await storage.get_properties_paginated(sort="price_asc")
        assert props[0]["price_pcm"] <= props[1]["price_pcm"]

    @pytest.mark.asyncio
    async def test_sort_price_desc(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, _ = await storage.get_properties_paginated(sort="price_desc")
        assert props[0]["price_pcm"] >= props[1]["price_pcm"]

    @pytest.mark.asyncio
    async def test_filter_by_bedrooms(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.get_properties_paginated(bedrooms=1)
        assert total == 1
        assert props[0]["bedrooms"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_min_price(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.get_properties_paginated(min_price=2000)
        assert total == 1
        assert props[0]["price_pcm"] >= 2000

    @pytest.mark.asyncio
    async def test_filter_by_max_price(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.get_properties_paginated(max_price=1950)
        assert total == 1
        assert props[0]["price_pcm"] <= 1950

    @pytest.mark.asyncio
    async def test_filter_by_area(
        self,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.get_properties_paginated(area="E3")
        assert total == 1
        assert "E3" in props[0]["postcode"]

    @pytest.mark.asyncio
    async def test_filter_by_min_rating(
        self,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
        sample_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)
        await storage.save_quality_analysis(prop_a.unique_id, sample_analysis)

        props, total = await storage.get_properties_paginated(min_rating=4)
        assert total == 1
        assert props[0]["quality_rating"] >= 4

    @pytest.mark.asyncio
    async def test_pagination(self, storage: PropertyStorage) -> None:
        # Insert 5 properties
        for i in range(5):
            prop = Property(
                source=PropertySource.OPENRENT,
                source_id=str(i),
                url=HttpUrl(f"https://openrent.com/{i}"),
                title=f"Flat {i}",
                price_pcm=1800 + i * 100,
                bedrooms=1,
                address=f"{i} Test St",
                postcode="E8 1AA",
            )
            merged = MergedProperty(
                canonical=prop,
                sources=(PropertySource.OPENRENT,),
                source_urls={PropertySource.OPENRENT: prop.url},
                min_price=prop.price_pcm,
                max_price=prop.price_pcm,
            )
            await storage.save_merged_property(merged)

        props, total = await storage.get_properties_paginated(page=1, per_page=2)
        assert total == 5
        assert len(props) == 2

        props2, _ = await storage.get_properties_paginated(page=2, per_page=2)
        assert len(props2) == 2

        props3, _ = await storage.get_properties_paginated(page=3, per_page=2)
        assert len(props3) == 1

    @pytest.mark.asyncio
    async def test_sources_list_parsed(
        self, storage: PropertyStorage, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_b)

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        assert "rightmove" in props[0]["sources_list"]
        assert "zoopla" in props[0]["sources_list"]

    @pytest.mark.asyncio
    async def test_quality_summary_extracted(
        self,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        sample_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, sample_analysis)

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        assert props[0]["quality_summary"] == sample_analysis.summary

    @pytest.mark.asyncio
    async def test_no_quality_summary_when_missing(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        props, _ = await storage.get_properties_paginated()
        assert props[0]["quality_summary"] == ""

    @pytest.mark.asyncio
    async def test_value_rating_extracted(
        self,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        sample_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, sample_analysis)

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        assert props[0]["value_rating"] == "excellent"

    @pytest.mark.asyncio
    async def test_value_rating_none_when_no_analysis(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        props, _ = await storage.get_properties_paginated()
        assert props[0]["value_rating"] is None

    @pytest.mark.asyncio
    async def test_value_rating_quality_adjusted_preferred(
        self,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
    ) -> None:
        """quality_adjusted_rating should be preferred over rating."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher="yes"),
            condition=ConditionAnalysis(overall_condition="good", confidence="high"),
            light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
            space=SpaceAnalysis(living_room_sqm=18.0, is_spacious_enough=True, confidence="high"),
            value=ValueAnalysis(
                area_average=2200,
                difference=-300,
                rating="excellent",
                note="Below avg",
                quality_adjusted_rating="good",
                quality_adjusted_note="Adjusted",
            ),
            overall_rating=4,
            summary="Test.",
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        props, _ = await storage.get_properties_paginated()
        assert props[0]["value_rating"] == "good"

    @pytest.mark.asyncio
    async def test_invalid_sort_falls_back(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        # Invalid sort should use default (newest)
        props, total = await storage.get_properties_paginated(sort="bogus")
        assert total == 1


class TestGetPropertyDetail:
    @pytest.mark.asyncio
    async def test_found(self, storage: PropertyStorage, merged_a: MergedProperty) -> None:
        await storage.save_merged_property(merged_a)

        detail = await storage.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert detail["unique_id"] == merged_a.unique_id
        assert detail["title"] == "Nice 1 bed flat in E8"

    @pytest.mark.asyncio
    async def test_not_found(self, storage: PropertyStorage) -> None:
        detail = await storage.get_property_detail("nonexistent:999")
        assert detail is None

    @pytest.mark.asyncio
    async def test_with_quality_analysis(
        self,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        sample_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, sample_analysis)

        detail = await storage.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert detail["quality_analysis"] is not None
        assert detail["quality_analysis"].overall_rating == 4

    @pytest.mark.asyncio
    async def test_without_quality_analysis(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        detail = await storage.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert detail["quality_analysis"] is None

    @pytest.mark.asyncio
    async def test_with_images(
        self, storage: PropertyStorage, prop_a: Property, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        images = [
            PropertyImage(
                url=HttpUrl("https://example.com/gallery1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=PropertySource.OPENRENT,
                image_type="floorplan",
            ),
        ]
        await storage.save_property_images(prop_a.unique_id, images)

        detail = await storage.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert len(detail["gallery_images"]) == 1
        assert len(detail["floorplan_images"]) == 1

    @pytest.mark.asyncio
    async def test_descriptions_json_parsed(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        detail = await storage.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert "descriptions_dict" in detail
        assert detail["descriptions_dict"].get("openrent") == "A lovely flat near the park."

    @pytest.mark.asyncio
    async def test_source_urls_parsed(
        self, storage: PropertyStorage, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_b)

        detail = await storage.get_property_detail(merged_b.unique_id)
        assert detail is not None
        assert "rightmove" in detail["source_urls_dict"]
        assert "zoopla" in detail["source_urls_dict"]


class TestThumbnailEpcFiltering:
    """Dashboard thumbnail selection avoids EPC chart images."""

    @pytest.mark.asyncio
    async def test_gallery_subquery_skips_epc_url(
        self, storage: PropertyStorage, prop_a: Property, merged_a: MergedProperty
    ) -> None:
        """first_gallery_url skips images with 'epc' in the URL."""
        await storage.save_merged_property(merged_a)
        images = [
            PropertyImage(
                url=HttpUrl("https://media.example.com/epc/epc_chart.png"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://media.example.com/img/living_room.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        ]
        await storage.save_property_images(prop_a.unique_id, images)

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        # Should pick the second image (living_room), not the EPC chart
        assert "living_room" in props[0]["image_url"]
        assert "epc" not in props[0]["image_url"].lower()

    @pytest.mark.asyncio
    async def test_gallery_subquery_skips_energy_performance_url(
        self, storage: PropertyStorage, prop_a: Property, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        images = [
            PropertyImage(
                url=HttpUrl("https://cdn.example.com/energy-performance-cert.png"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://cdn.example.com/img/kitchen.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        ]
        await storage.save_property_images(prop_a.unique_id, images)

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        assert "kitchen" in props[0]["image_url"]

    @pytest.mark.asyncio
    async def test_scraper_image_url_preferred_over_gallery(
        self, storage: PropertyStorage
    ) -> None:
        """When the scraper provides image_url, it takes priority over gallery."""
        prop = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="thumb-test",
            url=HttpUrl("https://rightmove.co.uk/thumb-test"),
            title="Flat with scraper thumb",
            price_pcm=1800,
            bedrooms=1,
            address="1 Test St",
            image_url=HttpUrl("https://media.rightmove.co.uk/img/scraper_thumb.jpg"),
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.RIGHTMOVE,),
            source_urls={PropertySource.RIGHTMOVE: prop.url},
            min_price=1800,
            max_price=1800,
        )
        await storage.save_merged_property(merged)
        # Save a gallery image that happens to be an EPC
        await storage.save_property_images(
            prop.unique_id,
            [
                PropertyImage(
                    url=HttpUrl("https://media.rightmove.co.uk/epc/chart.png"),
                    source=PropertySource.RIGHTMOVE,
                    image_type="gallery",
                ),
            ],
        )

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        assert "scraper_thumb" in props[0]["image_url"]

    @pytest.mark.asyncio
    async def test_gallery_fallback_when_no_scraper_image(
        self, storage: PropertyStorage, prop_a: Property, merged_a: MergedProperty
    ) -> None:
        """When scraper image_url is absent, use first non-EPC gallery image."""
        # prop_a has no image_url
        assert prop_a.image_url is None
        await storage.save_merged_property(merged_a)
        await storage.save_property_images(
            prop_a.unique_id,
            [
                PropertyImage(
                    url=HttpUrl("https://example.com/gallery/bedroom.jpg"),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ],
        )

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        assert "bedroom" in props[0]["image_url"]

    @pytest.mark.asyncio
    async def test_hash_epc_url_not_filtered(
        self, storage: PropertyStorage, prop_a: Property, merged_a: MergedProperty
    ) -> None:
        """Zoopla hash-based EPC URLs (e.g. 5e2020de.png) are NOT filterable at DB level.
        Caption-based filtering in the detail_fetcher prevents these from being saved."""
        await storage.save_merged_property(merged_a)
        await storage.save_property_images(
            prop_a.unique_id,
            [
                PropertyImage(
                    url=HttpUrl("https://lid.zoocdn.com/u/1024/768/5e2020de.png"),
                    source=PropertySource.ZOOPLA,
                    image_type="gallery",
                ),
            ],
        )

        props, _ = await storage.get_properties_paginated()
        assert len(props) == 1
        # Hash URL passes through — no way to tell it's an EPC from the URL alone
        assert "5e2020de" in props[0]["image_url"]
