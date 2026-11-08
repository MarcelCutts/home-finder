"""Stored anchors: homes loaded for matching, and refreshing an anchor's sources.

``get_recent_properties_for_dedup`` loads recent homes as anchors for matching.
``update_merged_sources`` links absorbed source listings to an anchor and
rebuilds its sources, URLs and price range from those source listings.
"""

import json

from pydantic import HttpUrl

from home_finder.db.row_mappers import row_to_merged_property
from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    Property,
    PropertyImage,
    PropertySource,
)


def _openrent_anchor(make_property, **overrides) -> MergedProperty:
    prop = make_property(source=PropertySource.OPENRENT, source_id="anchor", **overrides)
    return MergedProperty(
        canonical=prop,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
        descriptions={PropertySource.OPENRENT: "OR desc"},
    )


def _with_zoopla(anchor: MergedProperty, zoopla: Property, **updates) -> MergedProperty:
    """The matcher's proposal: the anchor plus the Zoopla listing, prices left stale."""
    return anchor.model_copy(
        update={
            "sources": (*anchor.sources, PropertySource.ZOOPLA),
            "source_urls": {**anchor.source_urls, PropertySource.ZOOPLA: zoopla.url},
            **updates,
        }
    )


async def _properties_row(storage, unique_id: str):
    conn = await storage._get_connection()
    cursor = await conn.execute("SELECT * FROM properties WHERE unique_id = ?", (unique_id,))
    return await cursor.fetchone()


class TestLoadAnchors:
    async def test_recent_home_is_loaded_with_its_images(self, storage, make_property) -> None:
        anchor = _openrent_anchor(make_property, price_pcm=2000)
        await storage.save_merged_property(anchor)
        await storage.save_property_images(
            anchor.unique_id,
            [
                PropertyImage(
                    url=HttpUrl("https://example.com/img1.jpg"),
                    source=PropertySource.OPENRENT,
                    image_type="gallery",
                ),
                PropertyImage(
                    url=HttpUrl("https://example.com/floor.jpg"),
                    source=PropertySource.OPENRENT,
                    image_type="floorplan",
                ),
            ],
        )

        [loaded] = await storage.get_recent_properties_for_dedup(days=7)
        assert loaded.canonical.unique_id == anchor.unique_id
        assert (loaded.min_price, loaded.max_price) == (2000, 2000)
        assert [str(image.url) for image in loaded.images] == ["https://example.com/img1.jpg"]
        assert loaded.floorplan is not None

    async def test_homes_older_than_the_lookback_window_are_not_anchors(
        self, storage, make_property
    ) -> None:
        from datetime import UTC, datetime, timedelta

        old = make_property(source_id="old-1", first_seen=datetime.now(UTC) - timedelta(days=60))
        recent = make_property(source=PropertySource.ZOOPLA, source_id="recent-1")
        await storage.save_property(old)
        await storage.save_property(recent)

        loaded = await storage.get_recent_properties_for_dedup(days=7)
        assert [m.unique_id for m in loaded] == [recent.unique_id]

    async def test_linked_source_listings_rebuild_a_multi_source_anchor(
        self, storage, make_property
    ) -> None:
        anchor = _openrent_anchor(make_property, price_pcm=2000, description="OR desc")
        zoopla = make_property(
            source=PropertySource.ZOOPLA, source_id="z-1", price_pcm=1950, description="ZP desc"
        )
        await storage.save_merged_property(
            _with_zoopla(
                anchor,
                zoopla,
                min_price=1950,
                descriptions={PropertySource.OPENRENT: "OR desc", PropertySource.ZOOPLA: "ZP desc"},
            )
        )
        await storage.upsert_source_listings([zoopla])
        await storage.link_source_listings([(zoopla.unique_id, anchor.unique_id)])

        [loaded] = await storage.get_recent_properties_for_dedup(days=7)
        assert set(loaded.sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}
        assert (loaded.min_price, loaded.max_price) == (1950, 2000)
        assert set(loaded.descriptions) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}

    async def test_two_listings_from_one_platform_yield_one_source(
        self, storage, make_property
    ) -> None:
        anchor = _openrent_anchor(make_property, price_pcm=2000)
        await storage.save_merged_property(anchor)
        zoopla_listings = [
            make_property(source=PropertySource.ZOOPLA, source_id="z-1", price_pcm=1950),
            make_property(source=PropertySource.ZOOPLA, source_id="z-2", price_pcm=1900),
        ]
        await storage.upsert_source_listings(zoopla_listings)
        await storage.link_source_listings(
            [(listing.unique_id, anchor.unique_id) for listing in zoopla_listings]
        )

        [loaded] = await storage.get_recent_properties_for_dedup(days=7)
        assert [s.value for s in loaded.sources] == ["openrent", "zoopla"]
        assert (loaded.min_price, loaded.max_price) == (1900, 2000)

    async def test_duplicated_sources_json_is_read_once_per_platform(
        self, storage, make_property
    ) -> None:
        """A row without linked source listings falls back to its sources JSON."""
        anchor = _openrent_anchor(make_property)
        await storage.save_merged_property(anchor)
        conn = await storage._get_connection()
        await conn.execute(
            "UPDATE properties SET sources = ? WHERE unique_id = ?",
            (json.dumps(["openrent", "zoopla", "zoopla"]), anchor.unique_id),
        )
        await conn.commit()

        row = await _properties_row(storage, anchor.unique_id)
        loaded = await row_to_merged_property(row, load_images=False)
        assert [s.value for s in loaded.sources] == ["openrent", "zoopla"]

    async def test_homes_awaiting_enrichment_are_not_anchors(
        self, storage, make_merged_property
    ) -> None:
        awaiting = make_merged_property(source_id="unenriched-1")
        await storage.processing.save_unenriched_property(awaiting)
        established = make_merged_property(
            sources=(PropertySource.RIGHTMOVE,), source_id="enriched-1"
        )
        await storage.save_merged_property(established)

        anchors = await storage.get_recent_properties_for_dedup(days=7)
        assert {a.unique_id for a in anchors} == {established.unique_id}


class TestUpdateAnchorSources:
    """Each test records the Zoopla listing at scrape time before folding it in."""

    async def test_new_source_keeps_the_legacy_sent_status(self, storage, make_property) -> None:
        # mark_notified sets only the legacy notification_status projection.
        anchor = _openrent_anchor(make_property)
        await storage.save_merged_property(anchor)
        await storage.mark_notified(anchor.unique_id)
        zoopla = make_property(source=PropertySource.ZOOPLA, source_id="z-1")
        await storage.upsert_source_listings([zoopla])

        await storage.update_merged_sources(
            anchor.unique_id, _with_zoopla(anchor, zoopla), absorbed_ids=[zoopla.unique_id]
        )

        tracked = await storage.get_property(anchor.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT

    async def test_price_range_is_rebuilt_from_the_linked_listings(
        self, storage, make_property
    ) -> None:
        anchor = _openrent_anchor(make_property, price_pcm=2000)
        await storage.save_merged_property(anchor)
        zoopla = make_property(source=PropertySource.ZOOPLA, source_id="z-1", price_pcm=1950)
        await storage.upsert_source_listings([zoopla])

        # The proposal still carries the anchor's 2000-2000 range.
        await storage.update_merged_sources(
            anchor.unique_id, _with_zoopla(anchor, zoopla), absorbed_ids=[zoopla.unique_id]
        )

        row = await _properties_row(storage, anchor.unique_id)
        assert (row["min_price"], row["max_price"]) == (1950, 2000)

    async def test_sources_urls_and_descriptions_include_the_new_source(
        self, storage, make_property
    ) -> None:
        anchor = _openrent_anchor(make_property)
        await storage.save_merged_property(anchor)
        zoopla = make_property(source=PropertySource.ZOOPLA, source_id="z-1")
        await storage.upsert_source_listings([zoopla])

        proposal = _with_zoopla(
            anchor,
            zoopla,
            descriptions={PropertySource.OPENRENT: "OR desc", PropertySource.ZOOPLA: "ZP desc"},
        )
        await storage.update_merged_sources(
            anchor.unique_id, proposal, absorbed_ids=[zoopla.unique_id]
        )

        row = await _properties_row(storage, anchor.unique_id)
        assert json.loads(row["sources"]) == ["openrent", "zoopla"]
        assert set(json.loads(row["source_urls"])) == {"openrent", "zoopla"}
        assert json.loads(row["descriptions_json"]) == {"openrent": "OR desc", "zoopla": "ZP desc"}

    async def test_images_from_the_new_source_are_saved(self, storage, make_property) -> None:
        anchor = _openrent_anchor(make_property)
        await storage.save_merged_property(anchor)
        zoopla = make_property(source=PropertySource.ZOOPLA, source_id="z-1")
        await storage.upsert_source_listings([zoopla])
        new_image = PropertyImage(
            url=HttpUrl("https://zoopla.co.uk/img-new.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        await storage.update_merged_sources(
            anchor.unique_id,
            _with_zoopla(anchor, zoopla, images=(new_image,)),
            absorbed_ids=[zoopla.unique_id],
        )

        assert await storage.get_property_images(anchor.unique_id) == [new_image]

    async def test_two_listings_from_one_platform_are_recorded_once(
        self, storage, make_property
    ) -> None:
        anchor = _openrent_anchor(make_property, price_pcm=2000)
        await storage.save_merged_property(anchor)
        zoopla_1 = make_property(source=PropertySource.ZOOPLA, source_id="z-1", price_pcm=1950)
        zoopla_2 = make_property(source=PropertySource.ZOOPLA, source_id="z-2", price_pcm=1900)
        await storage.upsert_source_listings([zoopla_1, zoopla_2])

        await storage.update_merged_sources(
            anchor.unique_id,
            _with_zoopla(anchor, zoopla_2),
            absorbed_ids=[zoopla_1.unique_id, zoopla_2.unique_id],
        )

        row = await _properties_row(storage, anchor.unique_id)
        assert json.loads(row["sources"]) == ["openrent", "zoopla"]
        assert (row["min_price"], row["max_price"]) == (1900, 2000)

    async def test_missing_anchor_without_listings_is_a_noop(self, storage, make_property) -> None:
        ghost = _openrent_anchor(make_property)

        await storage.update_merged_sources("nonexistent:999", ghost)

        assert await storage.get_property("nonexistent:999") is None
        assert await storage.get_recent_properties_for_dedup(days=7) == []
