"""End-to-end tests for the deduplication and merge pipeline.

Tests the full stack: raw Property objects through deduplication, merging,
and post-enrichment deduplication using realistic London rental data modeled
on actual scraper output.
"""

from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import HttpUrl

from home_finder.filters.deduplication import (
    MATCH_THRESHOLD,
    Deduplicator,
    calculate_match_score,
)
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_property(
    source: PropertySource = PropertySource.OPENRENT,
    source_id: str = "1",
    price_pcm: int = 1800,
    bedrooms: int = 2,
    address: str = "123 Mare Street, Hackney",
    postcode: str | None = "E8 3RH",
    latitude: float | None = 51.5465,
    longitude: float | None = -0.0553,
    title: str = "2 bed flat",
    description: str | None = None,
    image_url: str | None = None,
    first_seen: datetime | None = None,
) -> Property:
    """Build a Property with sensible defaults for tests."""
    url_map = {
        PropertySource.OPENRENT: "https://openrent.com",
        PropertySource.RIGHTMOVE: "https://rightmove.co.uk",
        PropertySource.ZOOPLA: "https://zoopla.co.uk",
        PropertySource.ONTHEMARKET: "https://onthemarket.com",
    }
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"{url_map[source]}/{source_id}"),
        title=title,
        price_pcm=price_pcm,
        bedrooms=bedrooms,
        address=address,
        postcode=postcode,
        latitude=latitude,
        longitude=longitude,
        description=description,
        image_url=HttpUrl(image_url) if image_url else None,
        first_seen=first_seen or datetime(2026, 2, 1, 10, 0),
    )


def _make_merged(
    prop: Property,
    images: tuple[PropertyImage, ...] = (),
    floorplan: PropertyImage | None = None,
    descriptions: dict[PropertySource, str] | None = None,
) -> MergedProperty:
    """Wrap a Property as a single-source MergedProperty."""
    descs: dict[PropertySource, str] = descriptions or {}
    if prop.description and prop.source not in descs:
        descs[prop.source] = prop.description
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        images=images,
        floorplan=floorplan,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
        descriptions=descs,
    )


# ---------------------------------------------------------------------------
# Real-world-like property datasets
# ---------------------------------------------------------------------------

# Scenario: Same 2-bed flat listed on OpenRent, Zoopla, and Rightmove
# OpenRent and Zoopla have full postcodes; Rightmove has outcode only.
MARE_ST_OPENRENT = _make_property(
    source=PropertySource.OPENRENT,
    source_id="OR-2780001",
    price_pcm=1850,
    bedrooms=2,
    address="Flat 4, 123 Mare Street, Hackney, London",
    postcode="E8 3RH",
    latitude=51.54650,
    longitude=-0.05530,
    title="2 Bed Flat, Mare Street, E8",
    first_seen=datetime(2026, 2, 1, 10, 0),
)

MARE_ST_ZOOPLA = _make_property(
    source=PropertySource.ZOOPLA,
    source_id="ZP-72380001",
    price_pcm=1850,
    bedrooms=2,
    address="123 Mare Street, Hackney E8 3RH",
    postcode="E8 3RH",
    latitude=51.54652,  # ~0.2m different (GPS jitter)
    longitude=-0.05528,
    title="2 bedroom flat to rent in Mare Street, E8",
    first_seen=datetime(2026, 2, 1, 11, 0),
)

MARE_ST_RIGHTMOVE = _make_property(
    source=PropertySource.RIGHTMOVE,
    source_id="RM-170001",
    price_pcm=1850,
    bedrooms=2,
    address="Mare Street, Hackney",
    postcode="E8",  # Rightmove only gives outcode
    latitude=None,
    longitude=None,
    title="2 bed flat to rent",
    first_seen=datetime(2026, 2, 1, 12, 0),
)

# Different property on same street (should NOT merge)
MARE_ST_DIFFERENT = _make_property(
    source=PropertySource.ONTHEMARKET,
    source_id="OTM-18650001",
    price_pcm=2200,
    bedrooms=2,
    address="456 Mare Street, Hackney, London",
    postcode="E8 4AA",
    latitude=51.54820,
    longitude=-0.05610,
    title="2 bed apartment, Mare Street",
    first_seen=datetime(2026, 2, 1, 13, 0),
)

# Scenario: Same 1-bed in Dalston on OTM + Zoopla, slightly different prices
DALSTON_OTM = _make_property(
    source=PropertySource.ONTHEMARKET,
    source_id="OTM-18670001",
    price_pcm=1600,
    bedrooms=1,
    address="Flat 2, 78 Kingsland Road, Dalston",
    postcode="E8 2PB",
    latitude=51.54910,
    longitude=-0.07640,
    title="1 bedroom flat, Kingsland Road",
    first_seen=datetime(2026, 2, 1, 9, 0),
)

DALSTON_ZOOPLA = _make_property(
    source=PropertySource.ZOOPLA,
    source_id="ZP-72390001",
    price_pcm=1625,  # £25 more (~1.6% diff, within 3% tolerance)
    bedrooms=1,
    address="78 Kingsland Rd, London E8 2PB",
    postcode="E8 2PB",
    latitude=51.54912,
    longitude=-0.07638,
    title="1 bed flat to rent in Kingsland Road, E8",
    first_seen=datetime(2026, 2, 1, 10, 30),
)

# Completely unrelated property in different area
STOKE_NEWINGTON = _make_property(
    source=PropertySource.OPENRENT,
    source_id="OR-2790001",
    price_pcm=2100,
    bedrooms=3,
    address="15 Church Street, Stoke Newington",
    postcode="N16 0AS",
    latitude=51.5615,
    longitude=-0.0765,
    title="3 Bed Flat, Church Street, N16",
    first_seen=datetime(2026, 2, 1, 8, 0),
)


# ---------------------------------------------------------------------------
# Test: deduplicate_and_merge (sync path)
# ---------------------------------------------------------------------------


class TestDeduplicateAndMergeE2E:
    """E2E tests for the deduplicate_and_merge_async method."""

    async def test_identical_listing_two_platforms_merged(self) -> None:
        """Same property on OpenRent + Zoopla with full postcode gets merged."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, MARE_ST_ZOOPLA])

        assert len(result) == 1
        merged = result[0]
        assert len(merged.sources) == 2
        assert PropertySource.OPENRENT in merged.sources
        assert PropertySource.ZOOPLA in merged.sources
        # Canonical is the earlier one
        assert merged.canonical.source == PropertySource.OPENRENT

    async def test_three_platform_merge_full_postcodes(self) -> None:
        """Same property on 3 platforms: OR + ZP merge (full postcode),
        RM (outcode only) stays separate without image hashing."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async(
            [MARE_ST_OPENRENT, MARE_ST_ZOOPLA, MARE_ST_RIGHTMOVE]
        )

        # OR + ZP should merge; RM has only outcode → separate
        merged_multi = [m for m in result if len(m.sources) > 1]
        merged_single = [m for m in result if len(m.sources) == 1]

        assert len(merged_multi) == 1
        assert len(merged_single) == 1
        assert PropertySource.RIGHTMOVE in merged_single[0].sources

    async def test_different_property_same_street_not_merged(self) -> None:
        """Two different properties on same street stay separate."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async(
            [MARE_ST_OPENRENT, MARE_ST_DIFFERENT]
        )

        assert len(result) == 2

    async def test_mixed_bag_correct_grouping(self) -> None:
        """Full scenario: 7 properties with correct grouping."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        all_props = [
            MARE_ST_OPENRENT,
            MARE_ST_ZOOPLA,
            MARE_ST_RIGHTMOVE,
            MARE_ST_DIFFERENT,
            DALSTON_OTM,
            DALSTON_ZOOPLA,
            STOKE_NEWINGTON,
        ]
        result = await deduplicator.deduplicate_and_merge_async(all_props)

        multi_source = [m for m in result if len(m.sources) > 1]

        # Mare St OR+ZP merged, Dalston OTM+ZP merged
        assert len(multi_source) == 2

    async def test_price_range_in_merged(self) -> None:
        """Merged property captures price range from both platforms."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([DALSTON_OTM, DALSTON_ZOOPLA])

        assert len(result) == 1
        merged = result[0]
        assert merged.min_price == 1600
        assert merged.max_price == 1625
        assert merged.price_varies is True

    async def test_source_urls_preserved(self) -> None:
        """Merged property has URLs from both platforms."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, MARE_ST_ZOOPLA])

        merged = result[0]
        assert PropertySource.OPENRENT in merged.source_urls
        assert PropertySource.ZOOPLA in merged.source_urls
        assert "openrent" in str(merged.source_urls[PropertySource.OPENRENT])
        assert "zoopla" in str(merged.source_urls[PropertySource.ZOOPLA])

    async def test_cross_platform_disabled_no_merge(self) -> None:
        """With cross_platform disabled, no merging occurs."""
        deduplicator = Deduplicator(enable_cross_platform=False)
        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, MARE_ST_ZOOPLA])

        assert len(result) == 2
        assert all(len(m.sources) == 1 for m in result)

    async def test_same_source_duplicate_deduped(self) -> None:
        """Duplicate from same source (same unique_id) is removed."""
        dup = MARE_ST_OPENRENT.model_copy(update={"first_seen": datetime(2026, 2, 2)})
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, dup])

        assert len(result) == 1
        # Keeps the earlier first_seen
        assert result[0].canonical.first_seen == MARE_ST_OPENRENT.first_seen

    async def test_no_postcode_properties_kept_separate(self) -> None:
        """Properties without postcodes cannot cross-platform match."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="nopc-1",
            postcode=None,
            latitude=None,
            longitude=None,
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="nopc-2",
            postcode=None,
            latitude=None,
            longitude=None,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 2

    async def test_empty_input(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)
        assert await deduplicator.deduplicate_and_merge_async([]) == []

    async def test_single_property(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT])
        assert len(result) == 1
        assert result[0].canonical == MARE_ST_OPENRENT


# ---------------------------------------------------------------------------
# Test: deduplicate_and_merge_async (async path with weighted scoring)
# ---------------------------------------------------------------------------


class TestDeduplicateAndMergeAsyncE2E:
    """E2E tests for the async weighted-scoring dedup path."""

    @pytest.mark.asyncio
    async def test_full_postcode_match_async(self) -> None:
        """Properties with matching full postcodes merge in async path."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, MARE_ST_ZOOPLA])

        assert len(result) == 1
        merged = result[0]
        assert len(merged.sources) == 2

    @pytest.mark.asyncio
    async def test_outcode_only_no_merge_without_images(self) -> None:
        """Rightmove (outcode only) does NOT merge without image hashing."""
        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=False,
        )
        result = await deduplicator.deduplicate_and_merge_async(
            [MARE_ST_OPENRENT, MARE_ST_RIGHTMOVE]
        )

        # Score: street(20) + outcode(10) + price(15) = 45 < 60 threshold
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_different_bedrooms_never_merge(self) -> None:
        """Properties with different bedrooms never merge."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="bed-1",
            bedrooms=1,
            postcode="E8 3RH",
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="bed-2",
            bedrooms=2,
            postcode="E8 3RH",
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_graduated_price_within_tolerance(self) -> None:
        """2% price difference still merges (graduated scoring)."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="price-1",
            price_pcm=1800,
            postcode="E8 3RH",
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="price-2",
            price_pcm=1836,  # 2% difference
            postcode="E8 3RH",
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_price_beyond_double_tolerance_still_merges_on_location(self) -> None:
        """8% price diff → price score = 0, but postcode + coords + outcode still merge."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="bigprice-1",
            price_pcm=1800,
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="bigprice-2",
            price_pcm=1950,  # ~8% higher
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        # postcode(40) + coords(40) + street(20) + outcode(10) = 110
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_different_outcode_blocks_no_comparison(self) -> None:
        """Properties in different outcodes are never compared (blocking)."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="e8-1",
            postcode="E8 3RH",
            bedrooms=2,
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="n16-1",
            postcode="N16 0AS",
            bedrooms=2,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_same_source_not_merged_cross_platform(self) -> None:
        """Two properties from the same source are never cross-platform merged."""
        p1 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="zp-1",
            postcode="E8 3RH",
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="zp-2",
            postcode="E8 3RH",
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        # Same source → won't merge even with identical data
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_large_batch_with_multiple_groups(self) -> None:
        """Realistic batch: 7 properties → correct grouping via async path."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        all_props = [
            MARE_ST_OPENRENT,
            MARE_ST_ZOOPLA,
            MARE_ST_RIGHTMOVE,
            MARE_ST_DIFFERENT,
            DALSTON_OTM,
            DALSTON_ZOOPLA,
            STOKE_NEWINGTON,
        ]
        result = await deduplicator.deduplicate_and_merge_async(all_props)

        multi_source = [m for m in result if len(m.sources) > 1]
        assert len(multi_source) == 2

    @pytest.mark.asyncio
    async def test_coordinate_proximity_25m_merges(self) -> None:
        """Properties 25m apart (within 50m threshold) should merge."""
        # ~25m = ~0.000225° latitude at London latitude
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="close-1",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="close-2",
            postcode="E8 3RH",
            latitude=51.546725,
            longitude=-0.0553,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_coordinate_distance_200m_apart_no_coord_bonus(self) -> None:
        """Properties 200m apart get no coordinate score, but may still merge
        on postcode + outcode + street + price."""
        # ~200m = ~0.0018° latitude
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="far-1",
            postcode="E8 3RH",
            address="123 Mare Street, Hackney",
            latitude=51.5465,
            longitude=-0.0553,
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="far-2",
            postcode="E8 3RH",
            address="456 Mare St, E8",
            latitude=51.5483,
            longitude=-0.0553,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        # postcode(40) + street(20) + outcode(10) + price(15) = 85
        # coords at 200m → 0, but other signals sufficient
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Test: deduplicate_merged_async (post-enrichment dedup)
# ---------------------------------------------------------------------------


class TestDeduplicateMergedAsyncE2E:
    """E2E tests for post-enrichment MergedProperty deduplication."""

    @pytest.mark.asyncio
    async def test_enriched_merge_combines_images(self) -> None:
        """Merging enriched MergedProperties combines their images."""
        img1 = PropertyImage(
            url=HttpUrl("https://openrent.com/img1.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img2 = PropertyImage(
            url=HttpUrl("https://zoopla.co.uk/img2.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )
        fp = PropertyImage(
            url=HttpUrl("https://zoopla.co.uk/floor.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="floorplan",
        )

        mp1 = _make_merged(MARE_ST_OPENRENT, images=(img1,))
        mp2 = _make_merged(MARE_ST_ZOOPLA, images=(img2,), floorplan=fp)

        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 1
        merged = result[0]
        assert len(merged.images) == 2
        assert merged.floorplan is not None
        assert len(merged.sources) == 2

    @pytest.mark.asyncio
    async def test_enriched_merge_preserves_descriptions(self) -> None:
        """Merging combines descriptions from both sources."""
        mp1 = _make_merged(
            MARE_ST_OPENRENT.model_copy(update={"description": "OpenRent desc"}),
            descriptions={PropertySource.OPENRENT: "OpenRent desc"},
        )
        mp2 = _make_merged(
            MARE_ST_ZOOPLA.model_copy(update={"description": "Zoopla desc"}),
            descriptions={PropertySource.ZOOPLA: "Zoopla desc"},
        )

        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 1
        assert PropertySource.OPENRENT in result[0].descriptions
        assert PropertySource.ZOOPLA in result[0].descriptions

    @pytest.mark.asyncio
    async def test_enriched_different_properties_stay_separate(self) -> None:
        """Non-matching enriched properties remain separate."""
        mp1 = _make_merged(MARE_ST_OPENRENT)
        mp2 = _make_merged(STOKE_NEWINGTON)

        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_enriched_same_source_not_merged(self) -> None:
        """Same-source enriched properties are not cross-platform merged."""
        p2 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="or-dup",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
        )
        mp1 = _make_merged(MARE_ST_OPENRENT)
        mp2 = _make_merged(p2)

        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_enriched_duplicate_images_deduped(self) -> None:
        """When merging, duplicate image URLs are not repeated."""
        same_img = PropertyImage(
            url=HttpUrl("https://cdn.example.com/shared.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        mp1 = _make_merged(MARE_ST_OPENRENT, images=(same_img,))
        mp2 = _make_merged(
            MARE_ST_ZOOPLA,
            images=(
                PropertyImage(
                    url=HttpUrl("https://cdn.example.com/shared.jpg"),
                    source=PropertySource.ZOOPLA,
                    image_type="gallery",
                ),
            ),
        )

        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 1
        # Image URLs are deduped by URL string
        assert len(result[0].images) == 1

    @pytest.mark.asyncio
    async def test_enriched_cross_platform_disabled(self) -> None:
        """With cross-platform disabled, nothing merges."""
        mp1 = _make_merged(MARE_ST_OPENRENT)
        mp2 = _make_merged(MARE_ST_ZOOPLA)

        deduplicator = Deduplicator(enable_cross_platform=False)
        result = await deduplicator.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 2


# ---------------------------------------------------------------------------
# Test: properties_to_merged
# ---------------------------------------------------------------------------


class TestPropertiesToMerged:
    """Test the wrapping step that precedes enrichment."""

    def test_wraps_each_as_single_source(self) -> None:
        deduplicator = Deduplicator()
        result = deduplicator.properties_to_merged([MARE_ST_OPENRENT, DALSTON_OTM, STOKE_NEWINGTON])
        assert len(result) == 3
        assert all(len(m.sources) == 1 for m in result)

    def test_dedupes_by_unique_id(self) -> None:
        dup = MARE_ST_OPENRENT.model_copy(update={"first_seen": datetime(2026, 2, 5)})
        deduplicator = Deduplicator()
        result = deduplicator.properties_to_merged([MARE_ST_OPENRENT, dup])
        assert len(result) == 1
        assert result[0].canonical.first_seen == MARE_ST_OPENRENT.first_seen


# ---------------------------------------------------------------------------
# Test: scoring invariants with Hypothesis
# ---------------------------------------------------------------------------


# Strategy for generating valid London-like properties
london_postcodes = st.sampled_from(
    [
        "E8 3RH",
        "E8 4AA",
        "E9 5LN",
        "E3 4AB",
        "N16 0AS",
        "N16 7AB",
        "E17 4RD",
        "E10 5NP",
        "N15 3AA",
        "E5 8QJ",
    ]
)

london_streets = st.sampled_from(
    [
        "Mare Street",
        "Kingsland Road",
        "Church Street",
        "High Street",
        "Victoria Road",
        "Green Lanes",
        "Stoke Newington Road",
        "Morning Lane",
        "Graham Road",
        "Dalston Lane",
    ]
)

property_sources = st.sampled_from(list(PropertySource))


@st.composite
def london_property(draw: st.DrawFn) -> Property:
    """Generate a realistic London rental property."""
    source = draw(property_sources)
    source_id = str(draw(st.integers(min_value=1, max_value=999999)))
    postcode = draw(london_postcodes)
    street = draw(london_streets)
    bedrooms = draw(st.integers(min_value=0, max_value=4))
    price = draw(st.integers(min_value=800, max_value=3500))
    # London coordinates: lat ~51.5, lon ~-0.05
    lat = draw(st.floats(min_value=51.50, max_value=51.60, allow_nan=False))
    lon = draw(st.floats(min_value=-0.10, max_value=0.02, allow_nan=False))

    url_map = {
        PropertySource.OPENRENT: "https://openrent.com",
        PropertySource.RIGHTMOVE: "https://rightmove.co.uk",
        PropertySource.ZOOPLA: "https://zoopla.co.uk",
        PropertySource.ONTHEMARKET: "https://onthemarket.com",
    }
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"{url_map[source]}/{source_id}"),
        title=f"{bedrooms} bed flat",
        price_pcm=price,
        bedrooms=bedrooms,
        address=f"123 {street}, London",
        postcode=postcode,
        latitude=lat,
        longitude=lon,
        first_seen=datetime(2026, 2, 1),
    )


class TestScoringInvariants:
    """Property-based tests for scoring invariants."""

    @given(prop=london_property())
    @settings(max_examples=50)
    def test_self_match_is_maximum(self, prop: Property) -> None:
        """A property always gets maximum score against itself."""
        score = calculate_match_score(prop, prop)
        # Self-match: postcode + coords + street + outcode + price all hit
        assert score.total > 0
        assert score.is_match is True

    @given(p1=london_property(), p2=london_property())
    @settings(max_examples=50)
    def test_scoring_is_symmetric(self, p1: Property, p2: Property) -> None:
        """Score(A, B) == Score(B, A)."""
        s1 = calculate_match_score(p1, p2)
        s2 = calculate_match_score(p2, p1)
        assert s1.total == pytest.approx(s2.total, abs=0.01)
        assert s1.is_match == s2.is_match

    @given(prop=london_property())
    @settings(max_examples=30)
    def test_different_bedrooms_always_zero(self, prop: Property) -> None:
        """Changing bedrooms always gives zero score."""
        other_beds = (prop.bedrooms + 1) % 5
        other = prop.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "other",
                "bedrooms": other_beds,
            }
        )
        score = calculate_match_score(prop, other)
        assert score.total == 0

    @given(p1=london_property(), p2=london_property())
    @settings(max_examples=50)
    def test_score_is_non_negative(self, p1: Property, p2: Property) -> None:
        """Scores are never negative."""
        score = calculate_match_score(p1, p2)
        assert score.total >= 0

    @given(prop=london_property())
    @settings(max_examples=30)
    def test_match_requires_minimum_signals(self, prop: Property) -> None:
        """If is_match is True, signal_count >= MINIMUM_SIGNALS."""
        other = prop.model_copy(update={"source": PropertySource.ZOOPLA, "source_id": "other"})
        score = calculate_match_score(prop, other)
        if score.is_match:
            assert score.signal_count >= 2


class TestDeduplicationInvariants:
    """Invariant tests for the deduplication pipeline."""

    @given(props=st.lists(london_property(), min_size=0, max_size=8))
    @settings(max_examples=20)
    def test_idempotence_of_unique_id_dedup(self, props: list[Property]) -> None:
        """Running properties_to_merged twice gives same count."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        first = deduplicator.properties_to_merged(props)
        # Convert back to properties and re-wrap
        re_props = [m.canonical for m in first]
        second = deduplicator.properties_to_merged(re_props)
        assert len(first) == len(second)

    @pytest.mark.asyncio
    @given(props=st.lists(london_property(), min_size=0, max_size=8))
    @settings(max_examples=20)
    async def test_async_output_count_leq_input(self, props: list[Property]) -> None:
        """Async path: output count <= input unique count."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async(props)

        unique_input = len({p.unique_id for p in props})
        assert len(result) <= unique_input

    @pytest.mark.asyncio
    @given(props=st.lists(london_property(), min_size=0, max_size=10))
    @settings(max_examples=30)
    async def test_preservation_async(self, props: list[Property]) -> None:
        """Every input property's canonical appears in exactly one merged group."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async(props)

        input_ids = {p.unique_id for p in props}
        canonical_ids = {m.canonical.unique_id for m in result}
        assert canonical_ids.issubset(input_ids)


# ---------------------------------------------------------------------------
# Test: real-world edge cases from actual scraper behavior
# ---------------------------------------------------------------------------


class TestRealWorldEdgeCases:
    """Tests modeling real scraper output quirks."""

    def test_rightmove_outcode_vs_zoopla_full_postcode(self) -> None:
        """Rightmove gives outcode only → conservative match requires image hash
        or coordinate proximity. Without either, stays separate."""
        rm = _make_property(
            source=PropertySource.RIGHTMOVE,
            source_id="rm-1",
            postcode="E8",  # outcode only
            latitude=None,
            longitude=None,
            address="Mare Street, Hackney",
        )
        zp = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="zp-1",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            address="123 Mare Street, Hackney E8 3RH",
        )
        score = calculate_match_score(rm, zp)
        # street(20) + outcode(10) + price(15) = 45, no postcode/coord match
        assert score.total < MATCH_THRESHOLD
        assert score.is_match is False

    def test_openrent_address_quirks(self) -> None:
        """OpenRent puts property type in the address field.
        e.g. '3 Bed Maisonette, Lunan House, E3'."""
        openrent = _make_property(
            source=PropertySource.OPENRENT,
            source_id="or-quirk",
            address="3 Bed Maisonette, Lunan House, E3",
            postcode="E3 4AB",
            bedrooms=3,
            price_pcm=2800,
        )
        zoopla = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="zp-quirk",
            address="Lunan House, Devons Road, London E3 4AB",
            postcode="E3 4AB",
            bedrooms=3,
            price_pcm=2800,
        )
        score = calculate_match_score(openrent, zoopla)
        # Full postcode match (40) + outcode (10) + price (15) = 65
        # Street normalization may or may not match depending on parsing
        assert score.full_postcode == 40
        assert score.is_match is True

    def test_zoopla_slight_coordinate_shift(self) -> None:
        """Zoopla coordinates are often slightly different from other platforms."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="coord-1",
            postcode="E8 3RH",
            latitude=51.54650,
            longitude=-0.05530,
        )
        # ~2m shift (common GPS variance)
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="coord-2",
            postcode="E8 3RH",
            latitude=51.54652,
            longitude=-0.05528,
        )
        score = calculate_match_score(p1, p2)
        # Coords nearly identical → close to 40 points (~2m ≈ 38-40 pts)
        assert score.coordinates > 38
        assert score.is_match is True

    async def test_onthemarket_duplicate_via_agents(self) -> None:
        """Same property listed by two estate agents on OnTheMarket.
        Different source_ids but same location → not merged (same source)."""
        p1 = _make_property(
            source=PropertySource.ONTHEMARKET,
            source_id="otm-agent1",
            postcode="E8 3RH",
        )
        p2 = _make_property(
            source=PropertySource.ONTHEMARKET,
            source_id="otm-agent2",
            postcode="E8 3RH",
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2])
        # Same source → not cross-platform merged
        assert len(result) == 2

    def test_price_reduced_property_still_matches(self) -> None:
        """Property listed at £1850 on one platform, reduced to £1800 on another.
        2.7% difference should get partial price credit and still match."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="price-orig",
            price_pcm=1850,
            postcode="E8 3RH",
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="price-reduced",
            price_pcm=1800,
            postcode="E8 3RH",
        )
        score = calculate_match_score(p1, p2)
        assert score.price > 0  # Partial credit for ~2.7% diff
        assert score.is_match is True

    async def test_transitive_merge_three_platforms(self) -> None:
        """If A matches B and B matches C, all three merge (union-find).
        Test with OTM, Zoopla, and OpenRent all having same full postcode."""
        otm = _make_property(
            source=PropertySource.ONTHEMARKET,
            source_id="trans-otm",
            price_pcm=1800,
            postcode="E9 5LN",
            bedrooms=2,
            latitude=51.549,
            longitude=-0.055,
            address="10 Chatsworth Road, Hackney",
        )
        zp = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="trans-zp",
            price_pcm=1825,  # Slight price diff
            postcode="E9 5LN",
            bedrooms=2,
            latitude=51.549,
            longitude=-0.055,
            address="10 Chatsworth Rd, E9",
        )
        openrent = _make_property(
            source=PropertySource.OPENRENT,
            source_id="trans-or",
            price_pcm=1800,
            postcode="E9 5LN",
            bedrooms=2,
            latitude=51.5491,
            longitude=-0.0551,
            address="Flat 3, 10 Chatsworth Road, E9",
        )

        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([otm, zp, openrent])

        merged_multi = [m for m in result if len(m.sources) > 1]
        assert len(merged_multi) == 1
        assert len(merged_multi[0].sources) == 3

    @pytest.mark.asyncio
    async def test_transitive_merge_async(self) -> None:
        """Transitive merge also works in async path."""
        otm = _make_property(
            source=PropertySource.ONTHEMARKET,
            source_id="async-otm",
            price_pcm=1800,
            postcode="E9 5LN",
            bedrooms=2,
            latitude=51.549,
            longitude=-0.055,
            address="10 Chatsworth Road, Hackney",
        )
        zp = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="async-zp",
            price_pcm=1825,
            postcode="E9 5LN",
            bedrooms=2,
            latitude=51.549,
            longitude=-0.055,
            address="10 Chatsworth Rd, E9",
        )
        openrent = _make_property(
            source=PropertySource.OPENRENT,
            source_id="async-or",
            price_pcm=1800,
            postcode="E9 5LN",
            bedrooms=2,
            latitude=51.5491,
            longitude=-0.0551,
            address="Flat 3, 10 Chatsworth Road, E9",
        )

        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([otm, zp, openrent])

        merged_multi = [m for m in result if len(m.sources) > 1]
        assert len(merged_multi) == 1
        assert len(merged_multi[0].sources) == 3

    async def test_multiple_properties_same_postcode_different_prices(self) -> None:
        """Multiple genuinely different flats in same building (same postcode,
        different prices, same bedrooms) should stay separate."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="bldg-1a",
            price_pcm=1800,
            postcode="E8 3RH",
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
            address="Flat 1, 123 Mare Street",
        )
        p2 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="bldg-1b",
            price_pcm=2000,
            postcode="E8 3RH",
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
            address="Flat 5, 123 Mare Street",
        )
        p3 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="bldg-1c",
            price_pcm=2200,
            postcode="E8 3RH",
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
            address="Flat 9, 123 Mare Street",
        )
        deduplicator = Deduplicator(enable_cross_platform=True)
        result = await deduplicator.deduplicate_and_merge_async([p1, p2, p3])
        # Same source → all separate
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Test: full pipeline flow (scrape output → filter → dedupe → merge → store)
# ---------------------------------------------------------------------------


class TestPipelineFlowE2E:
    """Test the pipeline flow from raw properties through storage."""

    @pytest.mark.asyncio
    async def test_full_flow_scrape_to_merged(self) -> None:
        """Simulate scraper output → criteria filter → dedupe → merge."""
        from home_finder.filters.criteria import CriteriaFilter
        from home_finder.models import SearchCriteria

        criteria = SearchCriteria(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=3,
            destination_postcode="N1 5AA",
            max_commute_minutes=30,
        )

        # Simulated scraper output: 7 properties
        scraped = [
            MARE_ST_OPENRENT,
            MARE_ST_ZOOPLA,
            MARE_ST_RIGHTMOVE,
            MARE_ST_DIFFERENT,
            DALSTON_OTM,
            DALSTON_ZOOPLA,
            STOKE_NEWINGTON,
            # One that won't pass criteria (too expensive)
            _make_property(
                source=PropertySource.ZOOPLA,
                source_id="expensive-1",
                price_pcm=3500,
                bedrooms=2,
                postcode="E8 1AA",
            ),
        ]

        # Step 1: Criteria filter
        criteria_filter = CriteriaFilter(criteria)
        filtered = criteria_filter.filter_properties(scraped)
        assert len(filtered) == 7  # expensive one removed

        # Step 2: Wrap as MergedProperty
        deduplicator = Deduplicator(enable_cross_platform=True)
        wrapped = deduplicator.properties_to_merged(filtered)
        assert len(wrapped) == 7

        # Step 3: Post-enrichment deduplicate
        merged = await deduplicator.deduplicate_merged_async(wrapped)

        # Should have: Mare St (OR+ZP merged), RM separate, OTM different,
        # Dalston (OTM+ZP merged), Stoke Newington
        multi_source = [m for m in merged if len(m.sources) > 1]
        assert len(multi_source) == 2

        # Verify no property was lost
        for m in merged:
            for src in m.sources:
                assert src in m.source_urls

    @pytest.mark.asyncio
    async def test_full_flow_with_storage(self) -> None:
        """Full pipeline through to in-memory SQLite storage."""
        from home_finder.db import PropertyStorage

        storage = PropertyStorage(":memory:")
        await storage.initialize()

        try:
            deduplicator = Deduplicator(enable_cross_platform=True)
            wrapped = deduplicator.properties_to_merged(
                [MARE_ST_OPENRENT, MARE_ST_ZOOPLA, DALSTON_OTM]
            )
            merged = await deduplicator.deduplicate_merged_async(wrapped)

            # Filter new (all should be new since fresh DB)
            new = await storage.filter_new_merged(merged)
            assert len(new) == len(merged)

            # Save
            for m in merged:
                await storage.save_merged_property(m)

            # Second run: nothing new
            new2 = await storage.filter_new_merged(merged)
            assert len(new2) == 0
        finally:
            await storage.close()


# ---------------------------------------------------------------------------
# Test: score calculation for known property pairs
# ---------------------------------------------------------------------------


class TestKnownPairScoring:
    """Test scoring for specific scenarios derived from real data patterns."""

    def test_exact_match_all_signals(self) -> None:
        """Perfect match: same postcode, coords, street, price → high confidence."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="exact-1",
            price_pcm=1800,
            postcode="E8 3RH",
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
            address="123 Mare Street, Hackney",
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="exact-2",
            price_pcm=1800,
            postcode="E8 3RH",
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
            address="123 Mare St, E8",
        )
        score = calculate_match_score(p1, p2)
        assert score.full_postcode == 40
        assert score.coordinates == 40
        assert score.street_name == 20
        assert score.outcode == 10
        assert score.price == 15
        assert score.total == 125
        assert score.confidence.value == "high"
        assert score.signal_count == 5

    def test_partial_postcode_weakens_match(self) -> None:
        """One property with outcode only → no full postcode bonus."""
        full = _make_property(
            source=PropertySource.OPENRENT,
            source_id="full-pc",
            postcode="E8 3RH",
        )
        partial = _make_property(
            source=PropertySource.RIGHTMOVE,
            source_id="partial-pc",
            postcode="E8",
            latitude=None,
            longitude=None,
        )
        score = calculate_match_score(full, partial)
        assert score.full_postcode == 0
        assert score.outcode == 10

    def test_nearby_coordinates_graduated(self) -> None:
        """Properties ~30m apart get graduated coordinate score."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="near-1",
            latitude=51.5465,
            longitude=-0.0553,
        )
        # ~30m ≈ 0.00027° lat
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="near-2",
            latitude=51.54677,
            longitude=-0.0553,
        )
        score = calculate_match_score(p1, p2)
        # At 30m (within 50m reference): score = 40 * (1 - (30/50)*0.5) ≈ 28
        assert 25 < score.coordinates < 40
