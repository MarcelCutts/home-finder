"""The deduplicator merges one home's listings across platforms and keeps other homes apart.

Listings are modelled on scraper output for Hackney and Stoke Newington. Most tests
call ``deduplicate_and_merge_async``. The production pipeline instead wraps listings
with ``properties_to_merged`` and merges them after enrichment with
``deduplicate_merged_async``; both entry points run the same grouping.
"""

from datetime import datetime

import pytest
from pydantic import HttpUrl

from home_finder.matching.deduplication import Deduplicator
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource


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
    first_seen: datetime | None = None,
) -> Property:
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
        first_seen=first_seen or datetime(2026, 2, 1, 10, 0),
    )


def _make_merged(
    prop: Property,
    images: tuple[PropertyImage, ...] = (),
    floorplan: PropertyImage | None = None,
    descriptions: dict[PropertySource, str] | None = None,
) -> MergedProperty:
    """Wrap a listing as a single-source home, as enrichment leaves it."""
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


def _homes(result: list[MergedProperty]) -> set[frozenset[str]]:
    """Each home as the set of listing URLs it carries."""
    return {frozenset(str(url) for url in home.source_urls.values()) for home in result}


def _home_of(*listings: Property) -> frozenset[str]:
    return frozenset(str(listing.url) for listing in listings)


# One 2-bed flat on OpenRent, Zoopla and Rightmove. OpenRent and Zoopla give the full
# postcode; Rightmove gives only the outcode and no coordinates.
MARE_ST_OPENRENT = _make_property(
    source=PropertySource.OPENRENT,
    source_id="OR-2780001",
    price_pcm=1850,
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
    address="123 Mare Street, Hackney E8 3RH",
    postcode="E8 3RH",
    latitude=51.54652,  # GPS jitter of about 2 m
    longitude=-0.05528,
    title="2 bedroom flat to rent in Mare Street, E8",
    first_seen=datetime(2026, 2, 1, 11, 0),
)

MARE_ST_RIGHTMOVE = _make_property(
    source=PropertySource.RIGHTMOVE,
    source_id="RM-170001",
    price_pcm=1850,
    address="Mare Street, Hackney",
    postcode="E8",
    latitude=None,
    longitude=None,
    title="2 bed flat to rent",
    first_seen=datetime(2026, 2, 1, 12, 0),
)

# A different 2-bed flat further along the same street.
MARE_ST_DIFFERENT = _make_property(
    source=PropertySource.ONTHEMARKET,
    source_id="OTM-18650001",
    price_pcm=2200,
    address="456 Mare Street, Hackney, London",
    postcode="E8 4AA",
    latitude=51.54820,
    longitude=-0.05610,
    title="2 bed apartment, Mare Street",
    first_seen=datetime(2026, 2, 1, 13, 0),
)

# One 1-bed in Dalston on OnTheMarket and Zoopla, with rents 1.6% apart.
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
    price_pcm=1625,
    bedrooms=1,
    address="78 Kingsland Rd, London E8 2PB",
    postcode="E8 2PB",
    latitude=51.54912,
    longitude=-0.07638,
    title="1 bed flat to rent in Kingsland Road, E8",
    first_seen=datetime(2026, 2, 1, 10, 30),
)

# An unrelated 3-bed in another outcode.
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

MIXED_BATCH = [
    MARE_ST_OPENRENT,
    MARE_ST_ZOOPLA,
    MARE_ST_RIGHTMOVE,
    MARE_ST_DIFFERENT,
    DALSTON_OTM,
    DALSTON_ZOOPLA,
    STOKE_NEWINGTON,
]

MIXED_BATCH_HOMES = {
    _home_of(MARE_ST_OPENRENT, MARE_ST_ZOOPLA),
    _home_of(MARE_ST_RIGHTMOVE),
    _home_of(MARE_ST_DIFFERENT),
    _home_of(DALSTON_OTM, DALSTON_ZOOPLA),
    _home_of(STOKE_NEWINGTON),
}


class TestMergeDecisions:
    async def test_same_home_on_two_platforms_merges_under_the_earliest_listing(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, MARE_ST_ZOOPLA])

        assert _homes(result) == {_home_of(MARE_ST_OPENRENT, MARE_ST_ZOOPLA)}
        assert result[0].canonical.source == PropertySource.OPENRENT

    async def test_outcode_only_listing_stays_apart_without_image_evidence(self) -> None:
        """Rightmove's street, outcode and rent earn 45 points against either listing."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async(
            [MARE_ST_OPENRENT, MARE_ST_ZOOPLA, MARE_ST_RIGHTMOVE]
        )

        assert _homes(result) == {
            _home_of(MARE_ST_OPENRENT, MARE_ST_ZOOPLA),
            _home_of(MARE_ST_RIGHTMOVE),
        }

    async def test_different_home_on_the_same_street_stays_apart(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async(
            [MARE_ST_OPENRENT, MARE_ST_DIFFERENT]
        )

        assert len(result) == 2

    async def test_mixed_batch_merges_each_home_once(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async(MIXED_BATCH)

        assert len(result) == len(MIXED_BATCH_HOMES)
        assert _homes(result) == MIXED_BATCH_HOMES

    async def test_disabled_cross_platform_matching_keeps_every_listing_apart(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=False)

        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, MARE_ST_ZOOPLA])

        assert len(result) == 2
        assert all(len(m.sources) == 1 for m in result)

    async def test_listings_without_a_postcode_are_never_compared(self) -> None:
        """Their shared coordinates, street and rent would earn 75 points if compared."""
        p1 = _make_property(source=PropertySource.OPENRENT, source_id="nopc-1", postcode=None)
        p2 = _make_property(source=PropertySource.ZOOPLA, source_id="nopc-2", postcode=None)
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 2

    async def test_listings_in_different_outcodes_are_never_compared(self) -> None:
        """Their shared coordinates, street and rent would earn 75 points if compared."""
        p1 = _make_property(source=PropertySource.OPENRENT, source_id="e8-1", postcode="E8 3RH")
        p2 = _make_property(source=PropertySource.ZOOPLA, source_id="n16-1", postcode="N16 0AS")
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 2

    async def test_different_bedroom_counts_never_merge(self) -> None:
        p1 = _make_property(source=PropertySource.OPENRENT, source_id="bed-1", bedrooms=1)
        p2 = _make_property(source=PropertySource.ZOOPLA, source_id="bed-2", bedrooms=2)
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 2

    async def test_identical_listings_on_one_platform_never_merge(self) -> None:
        """Two agents can list the same flat on one platform; those stay separate."""
        p1 = _make_property(source=PropertySource.ZOOPLA, source_id="zp-1")
        p2 = _make_property(source=PropertySource.ZOOPLA, source_id="zp-2")
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 2

    @pytest.mark.parametrize(
        ("zoopla_rent", "merges"),
        [
            pytest.param(1818, True, id="1-percent-apart"),
            pytest.param(1950, False, id="8-percent-apart"),
        ],
    )
    async def test_rent_credit_decides_a_postcode_only_pair(
        self, zoopla_rent: int, merges: bool
    ) -> None:
        """Without coordinates or a shared street, postcode and outcode earn 50 points."""
        openrent = _make_property(
            source=PropertySource.OPENRENT,
            source_id="rent-or",
            address="12 Mare Street, Hackney",
            latitude=None,
            longitude=None,
        )
        zoopla = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="rent-zp",
            price_pcm=zoopla_rent,
            address="40 Graham Road, Hackney",
            latitude=None,
            longitude=None,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([openrent, zoopla])

        assert len(result) == (1 if merges else 2)

    async def test_rent_difference_does_not_veto_a_location_match(self) -> None:
        """Rents 8% apart earn no price credit; location still earns 110 points."""
        p1 = _make_property(source=PropertySource.OPENRENT, source_id="rent-1", price_pcm=1800)
        p2 = _make_property(source=PropertySource.ZOOPLA, source_id="rent-2", price_pcm=1950)
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 1

    @pytest.mark.parametrize(
        ("latitude_offset", "merges"),
        [
            pytest.param(0.000225, True, id="25-metres-apart"),
            pytest.param(0.0018, False, id="200-metres-apart"),
        ],
    )
    async def test_coordinate_credit_decides_an_outcode_only_pair(
        self, latitude_offset: float, merges: bool
    ) -> None:
        """Street, outcode and rent earn 45 points; coordinates must supply the rest."""
        p1 = _make_property(source=PropertySource.OPENRENT, source_id="near-1", postcode="E8")
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="near-2",
            postcode="E8",
            latitude=51.5465 + latitude_offset,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == (1 if merges else 2)

    async def test_distant_coordinates_do_not_veto_a_postcode_match(self) -> None:
        """Listings 200 m apart earn no coordinate credit; postcode, street, outcode and
        rent still earn 85 points."""
        p1 = _make_property(
            source=PropertySource.OPENRENT,
            source_id="far-1",
            address="123 Mare Street, Hackney",
            latitude=51.5465,
        )
        p2 = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="far-2",
            address="456 Mare St, E8",
            latitude=51.5483,
        )
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([p1, p2])

        assert len(result) == 1

    async def test_same_home_on_three_platforms_merges_into_one(self) -> None:
        """Rents, coordinates and address spellings vary slightly between platforms."""
        otm = _make_property(
            source=PropertySource.ONTHEMARKET,
            source_id="trans-otm",
            price_pcm=1800,
            postcode="E9 5LN",
            latitude=51.549,
            longitude=-0.055,
            address="10 Chatsworth Road, Hackney",
        )
        zp = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="trans-zp",
            price_pcm=1825,
            postcode="E9 5LN",
            latitude=51.549,
            longitude=-0.055,
            address="10 Chatsworth Rd, E9",
        )
        openrent = _make_property(
            source=PropertySource.OPENRENT,
            source_id="trans-or",
            price_pcm=1800,
            postcode="E9 5LN",
            latitude=51.5491,
            longitude=-0.0551,
            address="Flat 3, 10 Chatsworth Road, E9",
        )
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([otm, zp, openrent])

        assert _homes(result) == {_home_of(otm, zp, openrent)}


class TestPostEnrichmentMergeDecisions:
    async def test_identical_listings_on_one_platform_never_merge(self) -> None:
        p2 = _make_property(source=PropertySource.OPENRENT, source_id="or-dup")
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_merged_async(
            [_make_merged(MARE_ST_OPENRENT), _make_merged(p2)]
        )

        assert len(result) == 2

    async def test_disabled_cross_platform_matching_keeps_every_home_apart(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=False)

        result = await deduplicator.deduplicate_merged_async(
            [_make_merged(MARE_ST_OPENRENT), _make_merged(MARE_ST_ZOOPLA)]
        )

        assert len(result) == 2


class TestMergedHomeContents:
    async def test_merged_home_spans_both_platforms_rents(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([DALSTON_OTM, DALSTON_ZOOPLA])

        assert len(result) == 1
        merged = result[0]
        assert merged.min_price == 1600
        assert merged.max_price == 1625
        assert merged.price_varies is True

    async def test_merged_home_keeps_each_platform_url(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, MARE_ST_ZOOPLA])

        assert result[0].source_urls == {
            PropertySource.OPENRENT: MARE_ST_OPENRENT.url,
            PropertySource.ZOOPLA: MARE_ST_ZOOPLA.url,
        }

    async def test_merge_combines_photos_and_keeps_the_floorplan(self) -> None:
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
        assert merged.images == (img1, img2)
        assert merged.floorplan == fp
        assert set(merged.sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}

    async def test_merge_keeps_each_platform_description(self) -> None:
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
        assert result[0].descriptions == {
            PropertySource.OPENRENT: "OpenRent desc",
            PropertySource.ZOOPLA: "Zoopla desc",
        }

    async def test_merge_keeps_a_repeated_photo_url_once(self) -> None:
        shared_url = HttpUrl("https://cdn.example.com/shared.jpg")
        mp1 = _make_merged(
            MARE_ST_OPENRENT,
            images=(
                PropertyImage(url=shared_url, source=PropertySource.OPENRENT, image_type="gallery"),
            ),
        )
        mp2 = _make_merged(
            MARE_ST_ZOOPLA,
            images=(
                PropertyImage(url=shared_url, source=PropertySource.ZOOPLA, image_type="gallery"),
            ),
        )
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 1
        assert [str(image.url) for image in result[0].images] == [str(shared_url)]


class TestRepeatedObservations:
    async def test_repeat_observation_keeps_the_earliest_first_seen(self) -> None:
        repeat = MARE_ST_OPENRENT.model_copy(update={"first_seen": datetime(2026, 2, 2)})
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT, repeat])

        assert len(result) == 1
        assert result[0].canonical.first_seen == MARE_ST_OPENRENT.first_seen

    def test_wrapping_keeps_the_earliest_of_repeated_observations(self) -> None:
        repeat = MARE_ST_OPENRENT.model_copy(update={"first_seen": datetime(2026, 2, 5)})

        result = Deduplicator().properties_to_merged([repeat, MARE_ST_OPENRENT])

        assert len(result) == 1
        assert result[0].canonical.first_seen == MARE_ST_OPENRENT.first_seen

    def test_wrapping_makes_one_single_source_home_per_listing(self) -> None:
        result = Deduplicator().properties_to_merged(
            [MARE_ST_OPENRENT, DALSTON_OTM, STOKE_NEWINGTON]
        )

        assert [home.sources for home in result] == [
            (PropertySource.OPENRENT,),
            (PropertySource.ONTHEMARKET,),
            (PropertySource.OPENRENT,),
        ]

    async def test_empty_input_returns_no_homes(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        assert await deduplicator.deduplicate_and_merge_async([]) == []

    async def test_single_listing_becomes_its_own_home(self) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await deduplicator.deduplicate_and_merge_async([MARE_ST_OPENRENT])

        assert len(result) == 1
        assert result[0].canonical == MARE_ST_OPENRENT


class TestStoredHomes:
    async def test_new_listing_joins_its_stored_home_and_the_rest_stay_new(self) -> None:
        """Homes stored by an earlier run are deduplicated together with this run's listings."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        stored = [
            _make_merged(MARE_ST_OPENRENT),
            _make_merged(DALSTON_OTM),
            _make_merged(STOKE_NEWINGTON),
        ]
        stored_ids = {home.canonical.unique_id for home in stored}
        new_listings = [
            _make_merged(MARE_ST_ZOOPLA),  # the stored Mare Street flat on another platform
            _make_merged(
                _make_property(
                    source=PropertySource.ZOOPLA,
                    source_id="new-zp-1",
                    price_pcm=1700,
                    bedrooms=1,
                    postcode="N16 0AS",
                    latitude=51.5615,
                    longitude=-0.0765,
                    address="10 Church Street, N16",
                )
            ),
            _make_merged(
                _make_property(
                    source=PropertySource.OPENRENT,
                    source_id="new-or-1",
                    price_pcm=2500,
                    bedrooms=3,
                    postcode="E9 5LN",
                    latitude=51.549,
                    longitude=-0.055,
                    address="50 Chatsworth Road, E9",
                )
            ),
            _make_merged(
                _make_property(
                    source=PropertySource.ONTHEMARKET,
                    source_id="new-otm-1",
                    price_pcm=1400,
                    bedrooms=1,
                    postcode="E5 8QJ",
                    latitude=51.555,
                    longitude=-0.062,
                    address="20 Clapton Common, E5",
                )
            ),
            _make_merged(MARE_ST_DIFFERENT),
        ]

        result = await deduplicator.deduplicate_merged_async(new_listings + stored)

        still_new = [m for m in result if m.canonical.unique_id not in stored_ids]
        updated = [m for m in result if m.canonical.unique_id in stored_ids and len(m.sources) > 1]
        assert len(still_new) == 4
        assert len(updated) == 1
        assert updated[0].canonical.unique_id == MARE_ST_OPENRENT.unique_id
        assert set(updated[0].sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}


class TestPipelineStages:
    async def test_criteria_survivors_merge_after_wrapping(self) -> None:
        """Criteria filtering, wrapping and post-enrichment merging in pipeline order."""
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
        # Over budget, so the criteria filter removes it before matching.
        too_expensive = _make_property(
            source=PropertySource.ZOOPLA,
            source_id="expensive-1",
            price_pcm=3500,
            postcode="E8 1AA",
        )
        deduplicator = Deduplicator(enable_cross_platform=True)

        filtered = CriteriaFilter(criteria).filter_properties([*MIXED_BATCH, too_expensive])
        wrapped = deduplicator.properties_to_merged(filtered)
        merged = await deduplicator.deduplicate_merged_async(wrapped)

        assert filtered == MIXED_BATCH
        assert len(merged) == len(MIXED_BATCH_HOMES)
        assert _homes(merged) == MIXED_BATCH_HOMES

    async def test_merged_homes_are_new_until_saved(self) -> None:
        from home_finder.db import PropertyStorage

        storage = PropertyStorage(":memory:")
        await storage.initialize()
        try:
            deduplicator = Deduplicator(enable_cross_platform=True)
            wrapped = deduplicator.properties_to_merged(
                [MARE_ST_OPENRENT, MARE_ST_ZOOPLA, DALSTON_OTM]
            )
            merged = await deduplicator.deduplicate_merged_async(wrapped)

            assert len(await storage.filter_new_merged(merged)) == len(merged)

            for m in merged:
                await storage.save_merged_property(m)

            assert await storage.filter_new_merged(merged) == []
        finally:
            await storage.close()
