"""Match grouping keeps independent homes apart and never repeats a platform in a home."""

from collections.abc import Iterable

from hypothesis import given
from hypothesis import strategies as st

from home_finder.matching.deduplication import Deduplicator, _group_items_greedy
from home_finder.models import MergedProperty, Property, PropertySource
from tests.support.matching import as_aggregate, make_listing


def _source_ids(groups: Iterable[list[MergedProperty]]) -> list[list[str]]:
    return sorted(sorted(mp.canonical.source_id for mp in group) for group in groups)


_single_source_homes = st.builds(
    as_aggregate,
    st.builds(
        make_listing,
        st.sampled_from(list(PropertySource)),
        st.text(min_size=1, max_size=8, alphabet="abcdefghijklmnop0123456789"),
        price_pcm=st.integers(min_value=1000, max_value=3000),
        postcode=st.sampled_from(["E8 3RH", "E8 4AB"]),
        latitude=st.floats(min_value=51.54, max_value=51.56),
        longitude=st.floats(min_value=-0.06, max_value=-0.04),
    ),
)


@st.composite
def _multi_source_homes(draw: st.DrawFn) -> MergedProperty:
    """A stored home that already carries one to three platforms from an earlier merge.

    The canonical listing's platform is always the first of its sources.
    """
    sources = tuple(
        draw(st.lists(st.sampled_from(list(PropertySource)), min_size=1, max_size=3, unique=True))
    )
    listing = draw(
        st.builds(
            make_listing,
            st.just(sources[0]),
            st.text(min_size=1, max_size=8, alphabet="abcdefghijklmnop0123456789"),
            price_pcm=st.integers(min_value=1000, max_value=3000),
            postcode=st.sampled_from(["E8 3RH", "E8 4AB"]),
            latitude=st.floats(min_value=51.54, max_value=51.56),
            longitude=st.floats(min_value=-0.06, max_value=-0.04),
        )
    )
    return as_aggregate(listing, sources=sources, source_urls=dict.fromkeys(sources, listing.url))


def _distinct_homes(
    homes: st.SearchStrategy[MergedProperty],
) -> st.SearchStrategy[list[MergedProperty]]:
    return st.lists(homes, max_size=12, unique_by=lambda mp: mp.canonical.unique_id)


# Listings spread over two outcodes and two bedroom counts (four blocks) plus the
# no-postcode path, close enough in rent and location that many of them match. The
# narrow ID range makes repeated unique IDs common.
_listings = st.builds(
    make_listing,
    st.sampled_from(list(PropertySource)),
    st.integers(min_value=1, max_value=20).map(str),
    price_pcm=st.integers(min_value=1700, max_value=1900),
    bedrooms=st.integers(min_value=1, max_value=2),
    postcode=st.sampled_from(["E8 3RH", "E8 4AA", "N16 0AS", "E8", ""]),
    latitude=st.floats(min_value=51.5460, max_value=51.5470),
)


class TestCascadePrevention:
    async def test_same_building_flats_pair_by_rent_without_repeating_a_platform(self) -> None:
        """Three flats at one address, each on Rightmove and Zoopla at its own rent,
        plus an OnTheMarket listing at the lowest rent.

        Location signals match every cross-platform pair, so the homes are decided
        by rent (equal rents score highest) and by the one-listing-per-platform rule.
        """
        items = [
            as_aggregate(make_listing(source, f"{prefix}{i}", price_pcm=1800 + i * 200))
            for source, prefix in [(PropertySource.RIGHTMOVE, "rm"), (PropertySource.ZOOPLA, "zp")]
            for i in range(3)
        ]
        items.append(as_aggregate(make_listing(PropertySource.ONTHEMARKET, "otm0", price_pcm=1800)))

        groups = _group_items_greedy(items, {})

        assert _source_ids(groups) == [["otm0", "rm0", "zp0"], ["rm1", "zp1"], ["rm2", "zp2"]]

    async def test_intermediary_does_not_chain_two_listings_from_one_platform(self) -> None:
        """A (OpenRent) matches B (Zoopla) and B matches C (OpenRent); A and C stay apart."""
        a = as_aggregate(make_listing(PropertySource.OPENRENT, "a"))
        b = as_aggregate(make_listing(PropertySource.ZOOPLA, "b"))
        c = as_aggregate(make_listing(PropertySource.OPENRENT, "c"))

        groups = _group_items_greedy([a, b, c], {})

        assert _source_ids(groups) == [["a", "b"], ["c"]]

    async def test_cascade_stops_at_one_listing_per_platform(self) -> None:
        """Six identical listings on four platforms fill one home, then start another."""
        sources = [
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
            PropertySource.RIGHTMOVE,
            PropertySource.ONTHEMARKET,
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
        ]
        items = [as_aggregate(make_listing(src, f"p{i}")) for i, src in enumerate(sources)]

        groups = _group_items_greedy(items, {})

        assert _source_ids(groups) == [["p0", "p1", "p2", "p3"], ["p4", "p5"]]


class TestSameSourceCollisionGuard:
    async def test_identical_listings_from_one_platform_never_merge(self) -> None:
        items = [
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm1")),
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm2")),
        ]

        groups = _group_items_greedy(items, {})

        assert _source_ids(groups) == [["rm1"], ["rm2"]]

    async def test_best_scoring_pair_wins_a_contested_listing(self) -> None:
        """B and C are both Zoopla; B's equal rent scores higher than C's, so A takes B.

        C comes first in the input, so index order alone would pair A with C.
        """
        a = as_aggregate(make_listing(PropertySource.OPENRENT, "a", price_pcm=1800))
        c = as_aggregate(make_listing(PropertySource.ZOOPLA, "c", price_pcm=1810))
        b = as_aggregate(make_listing(PropertySource.ZOOPLA, "b", price_pcm=1800))

        groups = _group_items_greedy([a, c, b], {})

        assert _source_ids(groups) == [["a", "b"], ["c"]]


class TestGenuineMatchesGroup:
    async def test_same_home_on_two_platforms_groups(self) -> None:
        items = [
            as_aggregate(make_listing(PropertySource.OPENRENT, "or1")),
            as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1")),
        ]

        groups = _group_items_greedy(items, {})

        assert _source_ids(groups) == [["or1", "zp1"]]

    async def test_same_home_on_three_platforms_groups(self) -> None:
        items = [
            as_aggregate(make_listing(PropertySource.ONTHEMARKET, "otm1")),
            as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1")),
            as_aggregate(make_listing(PropertySource.OPENRENT, "or1")),
        ]

        groups = _group_items_greedy(items, {})

        assert _source_ids(groups) == [["or1", "otm1", "zp1"]]

    async def test_same_home_on_all_four_platforms_groups(self) -> None:
        items = [
            as_aggregate(make_listing(PropertySource.OPENRENT, "or1")),
            as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1")),
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm1")),
            as_aggregate(make_listing(PropertySource.ONTHEMARKET, "otm1")),
        ]

        groups = _group_items_greedy(items, {})

        assert _source_ids(groups) == [["or1", "otm1", "rm1", "zp1"]]

    async def test_distinct_homes_in_one_batch_group_separately(self) -> None:
        """Three homes each listed on two platforms, plus one home listed once."""

        def listing(
            source: PropertySource,
            source_id: str,
            price_pcm: int,
            postcode: str,
            address: str,
            location: tuple[float, float],
        ) -> MergedProperty:
            return as_aggregate(
                make_listing(
                    source,
                    source_id,
                    price_pcm=price_pcm,
                    postcode=postcode,
                    address=address,
                    latitude=location[0],
                    longitude=location[1],
                )
            )

        mare_street = (1800, "E8 3RH", "123 Mare Street", (51.5465, -0.0553))
        upper_street = (2500, "N1 5AB", "45 Upper Street", (51.5380, -0.1030))
        bethnal_green = (2200, "E2 9PQ", "78 Bethnal Green Road", (51.5240, -0.0680))
        items = [
            listing(PropertySource.OPENRENT, "pair1-or", *mare_street),
            listing(PropertySource.ZOOPLA, "pair1-zp", *mare_street),
            listing(PropertySource.RIGHTMOVE, "pair2-rm", *upper_street),
            listing(PropertySource.ONTHEMARKET, "pair2-otm", *upper_street),
            listing(PropertySource.OPENRENT, "pair3-or", *bethnal_green),
            listing(PropertySource.RIGHTMOVE, "pair3-rm", *bethnal_green),
            listing(
                PropertySource.ZOOPLA,
                "singleton-zp",
                3500,
                "SW1A 1AA",
                "10 Downing Street",
                (51.5034, -0.1276),
            ),
        ]

        groups = _group_items_greedy(items, {})

        assert _source_ids(groups) == [
            ["pair1-or", "pair1-zp"],
            ["pair2-otm", "pair2-rm"],
            ["pair3-or", "pair3-rm"],
            ["singleton-zp"],
        ]


class TestMultiSourceCollisionGuard:
    """Stored homes from an earlier run may carry platforms beyond their canonical one."""

    async def test_homes_sharing_a_platform_never_merge(self) -> None:
        """Zoopla+Rightmove and OnTheMarket+Rightmove would repeat Rightmove."""
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_otm = make_listing(PropertySource.ONTHEMARKET, "otm1")
        home_a = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={PropertySource.ZOOPLA: p_zp.url, PropertySource.RIGHTMOVE: p_zp.url},
        )
        home_b = as_aggregate(
            p_otm,
            sources=(PropertySource.ONTHEMARKET, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ONTHEMARKET: p_otm.url,
                PropertySource.RIGHTMOVE: p_otm.url,
            },
        )

        groups = _group_items_greedy([home_a, home_b], {})

        assert _source_ids(groups) == [["otm1"], ["zp1"]]

    async def test_new_listing_joins_a_multi_platform_home_without_overlap(self) -> None:
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_or = make_listing(PropertySource.OPENRENT, "or1")
        stored = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={PropertySource.ZOOPLA: p_zp.url, PropertySource.RIGHTMOVE: p_zp.url},
        )

        groups = _group_items_greedy([stored, as_aggregate(p_or)], {})

        assert _source_ids(groups) == [["or1", "zp1"]]

    async def test_new_listing_cannot_join_a_home_that_already_has_its_platform(self) -> None:
        """The canonical platforms differ (Zoopla, Rightmove), but the source sets overlap."""
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_rm = make_listing(PropertySource.RIGHTMOVE, "rm_new")
        stored = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={PropertySource.ZOOPLA: p_zp.url, PropertySource.RIGHTMOVE: p_zp.url},
        )

        groups = _group_items_greedy([stored, as_aggregate(p_rm)], {})

        assert _source_ids(groups) == [["rm_new"], ["zp1"]]

    async def test_partial_overlap_keeps_the_overlapping_home_alone(self) -> None:
        """A (Zoopla, Rightmove) and B (OnTheMarket, Rightmove) both match C (OpenRent).

        A takes C first, after which B would repeat Rightmove in that home.
        """
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_otm = make_listing(PropertySource.ONTHEMARKET, "otm1")
        p_or = make_listing(PropertySource.OPENRENT, "or1")
        home_a = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={PropertySource.ZOOPLA: p_zp.url, PropertySource.RIGHTMOVE: p_zp.url},
        )
        home_b = as_aggregate(
            p_otm,
            sources=(PropertySource.ONTHEMARKET, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ONTHEMARKET: p_otm.url,
                PropertySource.RIGHTMOVE: p_otm.url,
            },
        )

        groups = _group_items_greedy([home_a, home_b, as_aggregate(p_or)], {})

        assert _source_ids(groups) == [["or1", "zp1"], ["otm1"]]


class TestGroupingInvariants:
    @given(items=_distinct_homes(_single_source_homes))
    def test_no_group_repeats_a_platform(self, items: list[MergedProperty]) -> None:
        for group in _group_items_greedy(items, {}):
            sources = [mp.canonical.source for mp in group]
            assert len(sources) == len(set(sources)), [s.value for s in sources]

    @given(items=_distinct_homes(_single_source_homes))
    def test_no_group_exceeds_four_listings(self, items: list[MergedProperty]) -> None:
        for group in _group_items_greedy(items, {}):
            assert len(group) <= 4

    @given(items=_distinct_homes(_single_source_homes))
    def test_every_item_lands_in_exactly_one_group(self, items: list[MergedProperty]) -> None:
        grouped = [
            mp.canonical.unique_id for group in _group_items_greedy(items, {}) for mp in group
        ]

        assert sorted(grouped) == sorted(mp.canonical.unique_id for mp in items)

    @given(items=_distinct_homes(_multi_source_homes()))
    def test_no_group_repeats_a_platform_across_stored_source_sets(
        self, items: list[MergedProperty]
    ) -> None:
        for group in _group_items_greedy(items, {}):
            all_sources = [s for mp in group for s in mp.sources]
            assert len(all_sources) == len(set(all_sources)), [s.value for s in all_sources]


class TestDeduplicatorInvariants:
    @given(listings=st.lists(_listings, max_size=10))
    def test_wrapping_keeps_one_single_source_home_per_unique_id(
        self, listings: list[Property]
    ) -> None:
        wrapped = Deduplicator().properties_to_merged(listings)

        assert sorted(home.canonical.unique_id for home in wrapped) == sorted(
            {listing.unique_id for listing in listings}
        )
        assert all(home.sources == (home.canonical.source,) for home in wrapped)

    @given(listings=st.lists(_listings, max_size=10))
    async def test_every_listing_lands_in_exactly_one_home(self, listings: list[Property]) -> None:
        deduplicator = Deduplicator(enable_cross_platform=True)

        homes = await deduplicator.deduplicate_merged_async(
            deduplicator.properties_to_merged(listings)
        )

        placed = [str(url) for home in homes for url in home.source_urls.values()]
        assert sorted(placed) == sorted({str(listing.url) for listing in listings})
