"""Source listings and the home each one belongs to.

``source_listings.merged_id`` links a listing to its home (a ``properties`` row).
A linked listing is "seen" and skipped as new discovery; a listing that was only
recorded at scrape time has no link and is processed again. Every writer that
saves a home also links its other accepted source listings by URL.
"""

import pytest

from home_finder.models import MergedProperty, Property, PropertySource


def _single_source(prop: Property) -> MergedProperty:
    """A listing as the next run's selection presents it, before matching."""
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


class TestSeenListings:
    @pytest.mark.parametrize("recorded", ["never", "at_scrape_time_only"])
    async def test_unlinked_listing_is_not_seen(self, storage, make_property, recorded) -> None:
        prop = make_property(source=PropertySource.ZOOPLA)
        if recorded == "at_scrape_time_only":
            await storage.upsert_source_listings([prop])

        assert await storage.is_seen(prop.unique_id) is False
        assert await storage.filter_new_merged([_single_source(prop)]) == [_single_source(prop)]

    async def test_saved_listing_is_seen(self, storage, make_property) -> None:
        prop = make_property(source=PropertySource.ZOOPLA)
        await storage.save_property(prop)

        assert await storage.is_seen(prop.unique_id) is True
        assert await storage.filter_new_merged([_single_source(prop)]) == []

    async def test_linking_a_scraped_listing_makes_it_seen(self, storage, make_property) -> None:
        prop = make_property(source=PropertySource.ZOOPLA)
        await storage.upsert_source_listings([prop])
        assert await storage.is_seen(prop.unique_id) is False
        await storage.save_property(prop)

        await storage.link_source_listings([(prop.unique_id, prop.unique_id)])
        assert await storage.is_seen(prop.unique_id) is True

    async def test_legacy_filter_new_returns_only_unseen_listings(
        self, storage, make_property
    ) -> None:
        saved = make_property()
        unseen = make_property(source=PropertySource.RIGHTMOVE)
        await storage.save_property(saved)

        assert await storage.filter_new([saved, unseen]) == [unseen]

    async def test_seen_lookup_spans_more_than_one_500_id_chunk(
        self, storage, make_property
    ) -> None:
        ids = [f"openrent:{i}" for i in range(600)]
        for i in range(100):
            await storage.save_property(make_property(source_id=str(i)))

        seen = await storage._get_seen_ids(ids)
        assert seen == set(ids[:100])

    async def test_known_source_ids_include_scrape_time_listings(
        self, storage, make_property
    ) -> None:
        assert await storage.get_all_known_source_ids() == {}
        await storage.upsert_source_listings(
            [make_property(source=PropertySource.RIGHTMOVE, source_id="55555")]
        )

        known = await storage.get_all_known_source_ids()
        assert "55555" in known["rightmove"]


class TestWritersLinkAbsorbedSources:
    """In-run matching folds a Zoopla listing into an OpenRent home before saving."""

    @pytest.mark.parametrize(
        "writer",
        [
            "save_merged_property",
            "processing.save_pre_analysis_properties",
            "processing.save_dropped_properties",
        ],
    )
    async def test_both_listings_are_skipped_by_the_next_run(
        self, storage, make_property, writer
    ) -> None:
        canonical = make_property(source=PropertySource.OPENRENT, price_pcm=2000)
        absorbed = make_property(source=PropertySource.ZOOPLA, price_pcm=1950)
        await storage.upsert_source_listings([canonical, absorbed])
        home = MergedProperty(
            canonical=canonical,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: canonical.url,
                PropertySource.ZOOPLA: absorbed.url,
            },
            min_price=1950,
            max_price=2000,
        )

        if writer == "save_merged_property":
            await storage.save_merged_property(home)
        else:
            await getattr(storage.processing, writer.split(".")[1])([home], {})

        assert await storage.is_seen(absorbed.unique_id) is True
        await storage.upsert_source_listings([canonical, absorbed])
        next_run = [_single_source(canonical), _single_source(absorbed)]
        assert await storage.filter_new_merged(next_run) == []

    async def test_single_source_home_is_seen(self, storage, make_property) -> None:
        prop = make_property()
        await storage.save_merged_property(_single_source(prop))

        assert await storage.is_seen(prop.unique_id) is True
