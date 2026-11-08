"""Dashboard reads through WebQueryService: the grid, detail page, map and counts."""

from datetime import UTC, datetime, timedelta

import pytest
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
from home_finder.web.filters import PropertyFilter


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
        image_url=HttpUrl("https://example.com/img.jpg"),
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
        image_url=HttpUrl("https://example.com/img.jpg"),
    )


@pytest.fixture
def sample_analysis() -> PropertyQualityAnalysis:
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher="yes"),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
        space=SpaceAnalysis(living_room_sqm=18.0, is_spacious_enough=True),
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


async def test_property_count_counts_saved_homes(storage, make_property) -> None:
    assert await storage.web.get_property_count() == 0
    await storage.save_property(make_property())
    assert await storage.web.get_property_count() == 1
    await storage.save_property(make_property(source=PropertySource.RIGHTMOVE))
    assert await storage.web.get_property_count() == 2


class TestGetPropertiesPaginated:
    @pytest.mark.asyncio
    async def test_empty_database(self, storage: PropertyStorage) -> None:
        props, total = await storage.web.get_properties_paginated(PropertyFilter())
        assert props == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_returns_properties(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.web.get_properties_paginated(PropertyFilter())
        assert total == 2
        assert len(props) == 2

    @pytest.mark.parametrize(
        ("sort", "expected_prices"),
        [
            ("newest", [1900, 1600, 2100]),
            ("price_asc", [1600, 1900, 2100]),
            ("price_desc", [2100, 1900, 1600]),
            # An unknown key falls back to newest first.
            ("bogus", [1900, 1600, 2100]),
        ],
    )
    async def test_sort_orders_the_grid(
        self, storage, make_merged_property, sort, expected_prices
    ) -> None:
        # Newest-first order is neither ascending nor descending by price, so
        # an ignored sort key cannot produce either price order by accident.
        now = datetime.now(UTC)
        for days_ago, price in ((3, 2100), (2, 1600), (1, 1900)):
            await storage.save_merged_property(
                make_merged_property(price_pcm=price, first_seen=now - timedelta(days=days_ago))
            )

        props, _ = await storage.web.get_properties_paginated(PropertyFilter(), sort=sort)
        assert [p["price_pcm"] for p in props] == expected_prices

    @pytest.mark.asyncio
    async def test_filter_by_bedrooms(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.web.get_properties_paginated(PropertyFilter(bedrooms=1))
        assert total == 1
        assert props[0]["bedrooms"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_min_price(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.web.get_properties_paginated(PropertyFilter(min_price=2000))
        assert total == 1
        assert props[0]["price_pcm"] >= 2000

    @pytest.mark.asyncio
    async def test_filter_by_max_price(
        self, storage: PropertyStorage, merged_a: MergedProperty, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)

        props, total = await storage.web.get_properties_paginated(PropertyFilter(max_price=1950))
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

        props, total = await storage.web.get_properties_paginated(PropertyFilter(area="E3"))
        assert total == 1
        assert props[0]["postcode"] is not None
        assert "E3" in props[0]["postcode"]

    @pytest.mark.asyncio
    async def test_filter_by_min_fit_score(
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

        props, total = await storage.web.get_properties_paginated(PropertyFilter(min_fit_score=50))
        assert total == 1
        assert props[0]["quality_rating"] is not None

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
                image_url=HttpUrl("https://example.com/img.jpg"),
            )
            merged = MergedProperty(
                canonical=prop,
                sources=(PropertySource.OPENRENT,),
                source_urls={PropertySource.OPENRENT: prop.url},
                min_price=prop.price_pcm,
                max_price=prop.price_pcm,
            )
            await storage.save_merged_property(merged)

        props, total = await storage.web.get_properties_paginated(
            PropertyFilter(), page=1, per_page=2
        )
        assert total == 5
        assert len(props) == 2

        props2, _ = await storage.web.get_properties_paginated(PropertyFilter(), page=2, per_page=2)
        assert len(props2) == 2

        props3, _ = await storage.web.get_properties_paginated(PropertyFilter(), page=3, per_page=2)
        assert len(props3) == 1

    @pytest.mark.asyncio
    async def test_sources_list_parsed(
        self, storage: PropertyStorage, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_b)

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
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

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        assert props[0]["quality_summary"] == sample_analysis.summary

    @pytest.mark.asyncio
    async def test_no_quality_summary_when_missing(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
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

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        assert props[0]["value_rating"] == "excellent"

    @pytest.mark.asyncio
    async def test_value_rating_none_when_no_analysis(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
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
            space=SpaceAnalysis(living_room_sqm=18.0, is_spacious_enough=True),
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

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert props[0]["value_rating"] == "good"


class TestGetPropertyDetail:
    @pytest.mark.asyncio
    async def test_found(self, storage: PropertyStorage, merged_a: MergedProperty) -> None:
        await storage.save_merged_property(merged_a)

        detail = await storage.web.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert detail["unique_id"] == merged_a.unique_id
        assert detail["title"] == "Nice 1 bed flat in E8"

    @pytest.mark.asyncio
    async def test_not_found(self, storage: PropertyStorage) -> None:
        detail = await storage.web.get_property_detail("nonexistent:999")
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

        detail = await storage.web.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert detail["quality_analysis"] is not None
        assert detail["quality_analysis"].overall_rating == 4

    @pytest.mark.asyncio
    async def test_without_quality_analysis(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        detail = await storage.web.get_property_detail(merged_a.unique_id)
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

        detail = await storage.web.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert len(detail["gallery_images"]) == 1
        assert len(detail["floorplan_images"]) == 1

    @pytest.mark.asyncio
    async def test_descriptions_json_parsed(
        self, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        detail = await storage.web.get_property_detail(merged_a.unique_id)
        assert detail is not None
        assert "descriptions_dict" in detail
        assert detail["descriptions_dict"].get("openrent") == "A lovely flat near the park."

    @pytest.mark.asyncio
    async def test_source_urls_parsed(
        self, storage: PropertyStorage, merged_b: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_b)

        detail = await storage.web.get_property_detail(merged_b.unique_id)
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

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        # Should pick the second image (living_room), not the EPC chart
        assert props[0]["image_url"] is not None
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

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        assert props[0]["image_url"] is not None
        assert "kitchen" in props[0]["image_url"]

    @pytest.mark.asyncio
    async def test_gallery_preferred_over_scraper_thumbnail(self, storage: PropertyStorage) -> None:
        """Gallery image (higher quality) is preferred over scraper thumbnail."""
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
        await storage.save_property_images(
            prop.unique_id,
            [
                PropertyImage(
                    url=HttpUrl("https://media.rightmove.co.uk/img/gallery_photo.jpg"),
                    source=PropertySource.RIGHTMOVE,
                    image_type="gallery",
                ),
            ],
        )

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        assert props[0]["image_url"] is not None
        assert "gallery_photo" in props[0]["image_url"]

    @pytest.mark.asyncio
    async def test_scraper_thumb_fallback_when_all_gallery_epc(
        self, storage: PropertyStorage
    ) -> None:
        """When all gallery images are EPC (filtered out), fall back to scraper thumbnail."""
        prop = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="epc-only",
            url=HttpUrl("https://rightmove.co.uk/epc-only"),
            title="Flat with only EPC gallery",
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

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        # EPC filtered out by subquery, falls back to scraper thumbnail
        assert props[0]["image_url"] is not None
        assert "scraper_thumb" in props[0]["image_url"]

    @pytest.mark.asyncio
    async def test_gallery_image_used_when_no_scraper_thumbnail(
        self, storage: PropertyStorage
    ) -> None:
        """When scraper image_url is absent, gallery image is used."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="no-thumb",
            url=HttpUrl("https://openrent.com/no-thumb"),
            title="No thumbnail flat",
            price_pcm=1900,
            bedrooms=1,
            address="10 Mare Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
        )
        assert prop.image_url is None
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            min_price=1900,
            max_price=1900,
        )
        await storage.save_merged_property(merged)
        await storage.save_property_images(
            prop.unique_id,
            [
                PropertyImage(
                    url=HttpUrl("https://example.com/gallery/bedroom.jpg"),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
            ],
        )

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        assert props[0]["image_url"] is not None
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

        props, _ = await storage.web.get_properties_paginated(PropertyFilter())
        assert len(props) == 1
        # Hash URL passes through — no way to tell it's an EPC from the URL alone
        assert props[0]["image_url"] is not None
        assert "5e2020de" in props[0]["image_url"]


class TestGetMapMarkers:
    """Tests for get_map_markers."""

    @pytest.mark.asyncio
    async def test_returns_all_matching_with_coords(self, storage: PropertyStorage) -> None:
        """Should return all properties with coordinates, not paginated."""
        for i in range(5):
            prop = Property(
                source=PropertySource.OPENRENT,
                source_id=f"map-{i}",
                url=HttpUrl(f"https://openrent.com/map-{i}"),
                title=f"Flat {i}",
                price_pcm=1800 + i * 100,
                bedrooms=1,
                address=f"{i} Test Street",
                postcode="E8 3RH",
                latitude=51.5465 + i * 0.001,
                longitude=-0.0553,
                image_url=HttpUrl("https://example.com/img.jpg"),
            )
            await storage.save_property(prop)

        markers = await storage.web.get_map_markers(PropertyFilter())
        assert len(markers) == 5
        # Check marker structure
        m = markers[0]
        assert "id" in m
        assert "lat" in m
        assert "lon" in m
        assert "price" in m
        assert "url" in m
        assert m["url"].startswith("/property/")

    @pytest.mark.asyncio
    async def test_excludes_properties_without_coords(self, storage: PropertyStorage) -> None:
        """Properties missing lat/lon should not appear in map markers."""
        with_coords = Property(
            source=PropertySource.OPENRENT,
            source_id="with-coords",
            url=HttpUrl("https://openrent.com/with-coords"),
            title="Has coords",
            price_pcm=1900,
            bedrooms=1,
            address="10 Test Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        without_coords = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="no-coords",
            url=HttpUrl("https://rightmove.co.uk/no-coords"),
            title="No coords",
            price_pcm=2000,
            bedrooms=2,
            address="20 Test Street",
            postcode="E8",
        )
        await storage.save_property(with_coords)
        await storage.save_property(without_coords)

        markers = await storage.web.get_map_markers(PropertyFilter())
        assert len(markers) == 1
        assert markers[0]["id"] == with_coords.unique_id

    @pytest.mark.asyncio
    async def test_respects_filters(self, storage: PropertyStorage) -> None:
        """Filters (e.g. bedrooms) should apply to map markers."""
        one_bed = Property(
            source=PropertySource.OPENRENT,
            source_id="1bed",
            url=HttpUrl("https://openrent.com/1bed"),
            title="1 bed",
            price_pcm=1900,
            bedrooms=1,
            address="10 Test Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        two_bed = Property(
            source=PropertySource.OPENRENT,
            source_id="2bed",
            url=HttpUrl("https://openrent.com/2bed"),
            title="2 bed",
            price_pcm=2200,
            bedrooms=2,
            address="20 Test Street",
            postcode="E8 3RH",
            latitude=51.5470,
            longitude=-0.0550,
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        await storage.save_property(one_bed)
        await storage.save_property(two_bed)

        markers = await storage.web.get_map_markers(PropertyFilter(bedrooms=1))
        assert len(markers) == 1
        assert markers[0]["id"] == one_bed.unique_id
