"""Match grouping preserves independent homes and source ownership."""

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import HttpUrl

from home_finder.matching.deduplication import (
    Deduplicator,
    _group_items_greedy,
)
from home_finder.models import MergedProperty, Property, PropertySource
from tests.support.matching import (
    as_aggregate,
    make_listing,
)

_SOURCE_STRATEGY = st.sampled_from(list(PropertySource))


@st.composite
def _merged_property_st(draw: st.DrawFn) -> MergedProperty:
    """Generate a random single-source MergedProperty for property-based testing."""
    source = draw(_SOURCE_STRATEGY)
    source_id = draw(st.text(min_size=1, max_size=8, alphabet="abcdefghijklmnop0123456789"))
    price = draw(st.integers(min_value=1000, max_value=3000))
    lat = draw(st.floats(min_value=51.54, max_value=51.56))
    lon = draw(st.floats(min_value=-0.06, max_value=-0.04))
    postcode = draw(st.sampled_from(["E8 3RH", "E8 4AB", "E8 3RH"]))

    prop = Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/{source.value}/{source_id}"),
        title=f"Test {source_id}",
        price_pcm=price,
        bedrooms=2,
        address="123 Mare Street",
        postcode=postcode,
        latitude=lat,
        longitude=lon,
    )
    return MergedProperty(
        canonical=prop,
        sources=(source,),
        source_urls={source: prop.url},
        images=(),
        floorplan=None,
        min_price=price,
        max_price=price,
        descriptions={},
    )


@st.composite
def _multi_source_merged_property_st(draw: st.DrawFn) -> MergedProperty:
    """Generate a MergedProperty with 1-3 sources for multi-source fuzz testing.

    Simulates DB anchors that carry multiple sources (e.g. after a previous
    merge run). The canonical source is always sources[0].
    """
    all_sources = list(PropertySource)
    num_sources = draw(st.integers(min_value=1, max_value=min(3, len(all_sources))))
    selected = draw(
        st.lists(
            st.sampled_from(all_sources),
            min_size=num_sources,
            max_size=num_sources,
        )
    )
    # Deduplicate while preserving draw order
    seen: set[PropertySource] = set()
    unique_sources: list[PropertySource] = []
    for s in selected:
        if s not in seen:
            seen.add(s)
            unique_sources.append(s)
    sources = tuple(unique_sources)
    canonical_source = sources[0]

    source_id = draw(st.text(min_size=1, max_size=8, alphabet="abcdefghijklmnop0123456789"))
    price = draw(st.integers(min_value=1000, max_value=3000))
    lat = draw(st.floats(min_value=51.54, max_value=51.56))
    lon = draw(st.floats(min_value=-0.06, max_value=-0.04))
    postcode = draw(st.sampled_from(["E8 3RH", "E8 4AB", "E8 3RH"]))

    prop = Property(
        source=canonical_source,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/{canonical_source.value}/{source_id}"),
        title=f"Test {source_id}",
        price_pcm=price,
        bedrooms=2,
        address="123 Mare Street",
        postcode=postcode,
        latitude=lat,
        longitude=lon,
    )
    source_urls = dict.fromkeys(sources, prop.url)
    return MergedProperty(
        canonical=prop,
        sources=sources,
        source_urls=source_urls,
        images=(),
        floorplan=None,
        min_price=price,
        max_price=price,
        descriptions={},
    )


class TestFalsePositiveCascadePrevention:
    """Verify the greedy algorithm prevents transitive cascade false positives."""

    async def test_six_flats_same_building_stay_separate(self) -> None:
        """3 RM + 3 ZP + 1 OTM at same postcode/coords/street with different prices.

        No group should have duplicate sources.
        """
        items = []
        for i in range(3):
            items.append(
                as_aggregate(
                    make_listing(
                        PropertySource.RIGHTMOVE,
                        f"rm{i}",
                        price_pcm=1800 + i * 200,
                    )
                )
            )
        for i in range(3):
            items.append(
                as_aggregate(
                    make_listing(
                        PropertySource.ZOOPLA,
                        f"zp{i}",
                        price_pcm=1800 + i * 200,
                    )
                )
            )
        items.append(as_aggregate(make_listing(PropertySource.ONTHEMARKET, "otm0", price_pcm=1800)))

        groups = _group_items_greedy(items, {})

        for group in groups:
            sources = [mp.canonical.source for mp in group]
            assert len(sources) == len(set(sources)), f"Group has duplicate sources: {sources}"

    async def test_same_price_same_building_max_one_per_source(self) -> None:
        """2 RM with identical data -> 2 separate groups (never merge same-source)."""
        items = [
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm1")),
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm2")),
        ]

        groups = _group_items_greedy(items, {})

        # Should be 2 singletons — same-source pairs are never matched
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_no_transitive_chain_through_intermediary(self) -> None:
        """A(OR) matches B(ZP), B(ZP) matches C(OR) -> A and C never in same group."""
        a = as_aggregate(make_listing(PropertySource.OPENRENT, "a", price_pcm=1800))
        b = as_aggregate(make_listing(PropertySource.ZOOPLA, "b", price_pcm=1800))
        c = as_aggregate(make_listing(PropertySource.OPENRENT, "c", price_pcm=1800))

        groups = _group_items_greedy([a, b, c], {})

        # A and C are both OpenRent — they can't be in the same group
        for group in groups:
            sources = [mp.canonical.source for mp in group]
            assert len(sources) == len(set(sources))

    async def test_cascade_limited_to_four(self) -> None:
        """6 properties that would all chain, but max group size is 4."""
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

        for group in groups:
            assert len(group) <= 4
            sources_in_group = [mp.canonical.source for mp in group]
            assert len(sources_in_group) == len(set(sources_in_group))


class TestSameSourceCollisionGuard:
    async def test_two_rightmove_never_merge(self) -> None:
        items = [
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm1")),
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm2")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_four_platforms_merge_into_one(self) -> None:
        """Same property on all 4 platforms -> 1 group of 4."""
        items = [
            as_aggregate(make_listing(PropertySource.OPENRENT, "or1")),
            as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1")),
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm1")),
            as_aggregate(make_listing(PropertySource.ONTHEMARKET, "otm1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 4

    async def test_greedy_best_score_wins(self) -> None:
        """OR_A, ZP_B (strong match), ZP_C (weak match to A) -> A+B merge, C separate."""
        a = as_aggregate(make_listing(PropertySource.OPENRENT, "a", price_pcm=1800))
        # B: very close match to A
        b = as_aggregate(make_listing(PropertySource.ZOOPLA, "b", price_pcm=1800))
        # C: also Zoopla but slightly different price (still matching)
        c = as_aggregate(make_listing(PropertySource.ZOOPLA, "c", price_pcm=1810))

        groups = _group_items_greedy([a, b, c], {})

        # A and B should merge (best match). C stays separate (same source as B).
        merged_group = [g for g in groups if len(g) > 1]
        assert len(merged_group) == 1
        merged_sources = {mp.canonical.source for mp in merged_group[0]}
        assert PropertySource.OPENRENT in merged_sources
        assert PropertySource.ZOOPLA in merged_sources


class TestRegressionLegitMerges:
    async def test_identical_listing_two_platforms(self) -> None:
        """Same property OR + ZP still merges."""
        items = [
            as_aggregate(make_listing(PropertySource.OPENRENT, "or1")),
            as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 2

    async def test_three_platforms_genuine_same_property(self) -> None:
        """OTM + ZP + OR -> 1 group."""
        items = [
            as_aggregate(make_listing(PropertySource.ONTHEMARKET, "otm1")),
            as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1")),
            as_aggregate(make_listing(PropertySource.OPENRENT, "or1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 3

    async def test_four_platforms_genuine_same_property(self) -> None:
        """All 4 -> 1 group."""
        items = [
            as_aggregate(make_listing(PropertySource.OPENRENT, "or1")),
            as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1")),
            as_aggregate(make_listing(PropertySource.RIGHTMOVE, "rm1")),
            as_aggregate(make_listing(PropertySource.ONTHEMARKET, "otm1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 4

    async def test_mixed_batch_correct_grouping(self) -> None:
        """7 properties (3 genuine pairs + 1 singleton) -> 4 groups.

        Each pair has a distinct address+postcode+coords+price so they don't
        cross-match with other pairs.
        """
        # Pair 1: same property on OR + ZP (E8 3RH, Mare St)
        pair1_a = as_aggregate(
            make_listing(
                PropertySource.OPENRENT,
                "pair1-or",
                price_pcm=1800,
                postcode="E8 3RH",
                address="123 Mare Street",
                latitude=51.5465,
                longitude=-0.0553,
            )
        )
        pair1_b = as_aggregate(
            make_listing(
                PropertySource.ZOOPLA,
                "pair1-zp",
                price_pcm=1800,
                postcode="E8 3RH",
                address="123 Mare Street",
                latitude=51.5465,
                longitude=-0.0553,
            )
        )

        # Pair 2: same property on RM + OTM (N1 5AB, Upper St — different area)
        pair2_a = as_aggregate(
            make_listing(
                PropertySource.RIGHTMOVE,
                "pair2-rm",
                price_pcm=2500,
                postcode="N1 5AB",
                address="45 Upper Street",
                latitude=51.5380,
                longitude=-0.1030,
            )
        )
        pair2_b = as_aggregate(
            make_listing(
                PropertySource.ONTHEMARKET,
                "pair2-otm",
                price_pcm=2500,
                postcode="N1 5AB",
                address="45 Upper Street",
                latitude=51.5380,
                longitude=-0.1030,
            )
        )

        # Pair 3: same property on OR + RM (E2 9PQ, Bethnal Green Rd)
        pair3_a = as_aggregate(
            make_listing(
                PropertySource.OPENRENT,
                "pair3-or",
                price_pcm=2200,
                postcode="E2 9PQ",
                address="78 Bethnal Green Road",
                latitude=51.5240,
                longitude=-0.0680,
            )
        )
        pair3_b = as_aggregate(
            make_listing(
                PropertySource.RIGHTMOVE,
                "pair3-rm",
                price_pcm=2200,
                postcode="E2 9PQ",
                address="78 Bethnal Green Road",
                latitude=51.5240,
                longitude=-0.0680,
            )
        )

        # Singleton: unique property (SW1 area, completely different)
        singleton = as_aggregate(
            make_listing(
                PropertySource.ZOOPLA,
                "singleton-zp",
                price_pcm=3500,
                postcode="SW1A 1AA",
                address="10 Downing Street",
                latitude=51.5034,
                longitude=-0.1276,
            )
        )

        items = [pair1_a, pair1_b, pair2_a, pair2_b, pair3_a, pair3_b, singleton]
        groups = _group_items_greedy(items, {})

        assert len(groups) == 4

        # Verify all items appear exactly once
        all_ids = {mp.canonical.source_id for group in groups for mp in group}
        assert len(all_ids) == 7


class TestGreedyInvariants:
    @given(items=st.lists(_merged_property_st(), min_size=0, max_size=12))
    @settings(max_examples=50)
    def test_no_same_source_collision_invariant(self, items: list[MergedProperty]) -> None:
        """No group should have duplicate sources."""
        # Deduplicate by unique_id first (hypothesis may generate same source_id)
        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())

        groups = _group_items_greedy(unique_items, {})

        for group in groups:
            sources = [mp.canonical.source for mp in group]
            assert len(sources) == len(set(sources)), (
                f"Same-source collision: {[s.value for s in sources]}"
            )

    @given(items=st.lists(_merged_property_st(), min_size=0, max_size=12))
    @settings(max_examples=50)
    def test_max_group_size_invariant(self, items: list[MergedProperty]) -> None:
        """No group should exceed 4 members."""
        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())

        groups = _group_items_greedy(unique_items, {})

        for group in groups:
            assert len(group) <= 4

    @given(items=st.lists(_merged_property_st(), min_size=1, max_size=12))
    @settings(max_examples=50)
    def test_no_property_lost_invariant(self, items: list[MergedProperty]) -> None:
        """Every input unique_id appears in exactly one output group."""
        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())
        input_ids = {mp.canonical.unique_id for mp in unique_items}

        groups = _group_items_greedy(unique_items, {})

        output_ids: set[str] = set()
        for group in groups:
            for mp in group:
                uid = mp.canonical.unique_id
                assert uid not in output_ids, f"Duplicate output: {uid}"
                output_ids.add(uid)

        assert input_ids == output_ids

    @given(items=st.lists(_merged_property_st(), min_size=2, max_size=6))
    @settings(max_examples=30)
    def test_scoring_symmetry_preserved(self, items: list[MergedProperty]) -> None:
        """score(A,B) == score(B,A) for all pairs."""
        from home_finder.matching.scoring import calculate_match_score

        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())

        for i in range(len(unique_items)):
            for j in range(i + 1, len(unique_items)):
                a = unique_items[i].canonical
                b = unique_items[j].canonical
                score_ab = calculate_match_score(a, b, {})
                score_ba = calculate_match_score(b, a, {})
                assert abs(score_ab.total - score_ba.total) < 0.01, (
                    f"Asymmetric: {score_ab.total} vs {score_ba.total}"
                )


class TestDeduplicatorIntegration:
    async def test_end_to_end_greedy_merge(self) -> None:
        """Full pipeline: properties from different sources merge correctly."""
        dedup = Deduplicator(enable_cross_platform=True)

        # Same property on OR and ZP (same postcode, coords, price)
        mp1 = as_aggregate(make_listing(PropertySource.OPENRENT, "or1"))
        mp2 = as_aggregate(make_listing(PropertySource.ZOOPLA, "zp1"))

        # Different property (different outcode entirely — won't match)
        mp3 = as_aggregate(
            make_listing(
                PropertySource.RIGHTMOVE,
                "rm1",
                price_pcm=3000,
                postcode="N1 5AB",
                address="45 Upper Street",
                latitude=51.57,
                longitude=-0.08,
            )
        )

        result = await dedup.deduplicate_merged_async([mp1, mp2, mp3])

        assert len(result) == 2
        multi = [r for r in result if len(r.sources) > 1]
        assert len(multi) == 1
        assert set(multi[0].sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}

    async def test_best_of_canonical_in_merge(self) -> None:
        """Merged properties should have best-of canonical fields.

        Both properties share the same full postcode, address, and price
        so they score high enough to merge (postcode 40 + street 20 + outcode 10
        + price 15 = 85). The RM property has outcode-only postcode and no coords,
        but we give it a full postcode match to ensure the merge actually happens.
        """
        dedup = Deduplicator(enable_cross_platform=True)

        # RM has full postcode but no coords
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            postcode="E8 3RH",
            latitude=None,
            longitude=None,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )

        mp1 = as_aggregate(p1)
        mp2 = as_aggregate(p2)

        result = await dedup.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 1
        canon = result[0].canonical
        # Should have coordinates from ZP (backfilled)
        assert canon.latitude == 51.5465
        assert canon.longitude == -0.0553
        # Identity stays from RM (earlier first_seen)
        assert canon.source == PropertySource.RIGHTMOVE


class TestMultiSourceCollisionGuard:
    """Verify that multi-source MergedProperties (DB anchors) don't collide."""

    async def test_overlapping_source_sets_not_merged(self) -> None:
        """Two MergedProperties sharing a source (rightmove) stay separate.

        Simulates DB anchors that were already merged in a previous run:
        - Anchor A: canonical=zoopla, sources=('zoopla', 'rightmove')
        - Anchor B: canonical=onthemarket, sources=('onthemarket', 'rightmove')
        These share 'rightmove', so merging them would produce a group with
        rightmove appearing twice.
        """
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_otm = make_listing(PropertySource.ONTHEMARKET, "otm1")

        anchor_a = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ZOOPLA: p_zp.url,
                PropertySource.RIGHTMOVE: p_zp.url,
            },
        )
        anchor_b = as_aggregate(
            p_otm,
            sources=(PropertySource.ONTHEMARKET, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ONTHEMARKET: p_otm.url,
                PropertySource.RIGHTMOVE: p_otm.url,
            },
        )

        groups = _group_items_greedy([anchor_a, anchor_b], {})

        # Should be 2 singletons — source sets overlap on RIGHTMOVE
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_multi_source_anchor_plus_new_single_source(self) -> None:
        """DB anchor with (zoopla, rightmove) + new openrent → merged OK.

        No source overlap, so this should still merge normally.
        """
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_or = make_listing(PropertySource.OPENRENT, "or1")

        anchor = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ZOOPLA: p_zp.url,
                PropertySource.RIGHTMOVE: p_zp.url,
            },
        )
        new_prop = as_aggregate(p_or)

        groups = _group_items_greedy([anchor, new_prop], {})

        # No overlap → should merge into one group
        assert len(groups) == 1
        assert len(groups[0]) == 2

    async def test_new_single_source_blocked_by_anchor_containing_same_source(
        self,
    ) -> None:
        """New rightmove prop blocked from merging with anchor containing rightmove.

        Anchor: canonical=zoopla, sources=('zoopla', 'rightmove')
        New: canonical=rightmove (different ID)
        The canonical sources differ (zoopla != rightmove), but the full source
        sets overlap on rightmove.
        """
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_rm = make_listing(PropertySource.RIGHTMOVE, "rm_new")

        anchor = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ZOOPLA: p_zp.url,
                PropertySource.RIGHTMOVE: p_zp.url,
            },
        )
        new_prop = as_aggregate(p_rm)

        groups = _group_items_greedy([anchor, new_prop], {})

        # rightmove overlaps → 2 singletons
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_three_anchors_partial_overlap(self) -> None:
        """Three DB anchors where A overlaps B but not C, B overlaps A but not C.

        A: (zoopla, rightmove)
        B: (onthemarket, rightmove)
        C: (openrent,)

        A and C can merge (no overlap). B stays separate (overlaps A on rightmove).
        """
        p_zp = make_listing(PropertySource.ZOOPLA, "zp1")
        p_otm = make_listing(PropertySource.ONTHEMARKET, "otm1")
        p_or = make_listing(PropertySource.OPENRENT, "or1")

        anchor_a = as_aggregate(
            p_zp,
            sources=(PropertySource.ZOOPLA, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ZOOPLA: p_zp.url,
                PropertySource.RIGHTMOVE: p_zp.url,
            },
        )
        anchor_b = as_aggregate(
            p_otm,
            sources=(PropertySource.ONTHEMARKET, PropertySource.RIGHTMOVE),
            source_urls={
                PropertySource.ONTHEMARKET: p_otm.url,
                PropertySource.RIGHTMOVE: p_otm.url,
            },
        )
        anchor_c = as_aggregate(p_or)

        groups = _group_items_greedy([anchor_a, anchor_b, anchor_c], {})

        # A+C should merge (no overlap), B stays singleton
        merged_groups = [g for g in groups if len(g) > 1]
        singleton_groups = [g for g in groups if len(g) == 1]

        assert len(merged_groups) == 1
        assert len(singleton_groups) == 1

        # The merged group should contain A and C
        merged_sources: set[PropertySource] = set()
        for mp in merged_groups[0]:
            merged_sources.update(mp.sources)
        assert PropertySource.ZOOPLA in merged_sources
        assert PropertySource.OPENRENT in merged_sources

        # The singleton should be B (onthemarket + rightmove)
        assert PropertySource.ONTHEMARKET in singleton_groups[0][0].sources


class TestGreedyInvariantsMultiSource:
    """Hypothesis tests ensuring source uniqueness across full source tuples.

    Uses _multi_source_merged_property_st so hypothesis generates items with
    2-3 sources (simulating DB anchors from previous merge runs). This exercises
    the full source-set overlap check in _group_items_greedy, not just the
    canonical-source check.
    """

    @given(items=st.lists(_multi_source_merged_property_st(), min_size=0, max_size=12))
    @settings(max_examples=50)
    def test_no_source_appears_twice_in_group(self, items: list[MergedProperty]) -> None:
        """No merged group has any PropertySource appearing more than once
        across all member `sources` tuples."""
        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())

        groups = _group_items_greedy(unique_items, {})

        for group in groups:
            all_sources = [s for mp in group for s in mp.sources]
            assert len(all_sources) == len(set(all_sources)), (
                f"Source collision in group: {[s.value for s in all_sources]}"
            )
