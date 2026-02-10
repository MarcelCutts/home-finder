"""Tests for quality analysis and paginated query storage methods."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.filters.quality import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    ValueAnalysis,
)
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertySource,
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
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher=True),
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
                has_visible_damp=True,
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
