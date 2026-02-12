"""Tests for cross-platform deduplication across pipeline runs.

Verifies that the same physical property appearing on a different aggregator
in a later run is detected as a duplicate, merged into the existing DB record,
and NOT re-notified or re-analyzed.
"""

from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.filters.deduplication import Deduplicator
from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    Property,
    PropertyImage,
    PropertySource,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_property(
    source: PropertySource = PropertySource.OPENRENT,
    source_id: str = "1",
    price_pcm: int = 2000,
    bedrooms: int = 2,
    address: str = "Flat 4, 123 Mare Street, Hackney",
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


async def _split_dedup_results(
    new_properties: list[MergedProperty],
    db_anchors: list[MergedProperty],
) -> tuple[list[MergedProperty], list[tuple[str, MergedProperty]]]:
    """Run dedup and split results into genuinely new vs updated anchors.

    Uses URL-based detection (same logic as main.py) to handle cases where
    the deduplicator picks a different canonical than the DB anchor.

    Returns:
        (genuinely_new, anchors_updated) where anchors_updated is a list of
        (anchor_unique_id, merged_result) tuples.
    """
    # Build URL → anchor mapping
    anchor_url_to_id: dict[str, str] = {}
    anchor_by_id: dict[str, MergedProperty] = {}
    for anchor in db_anchors:
        anchor_by_id[anchor.canonical.unique_id] = anchor
        for url in anchor.source_urls.values():
            anchor_url_to_id[str(url)] = anchor.canonical.unique_id

    deduplicator = Deduplicator(enable_cross_platform=True)
    combined = new_properties + db_anchors
    dedup_results = await deduplicator.deduplicate_merged_async(combined)

    genuinely_new: list[MergedProperty] = []
    anchors_updated: list[tuple[str, MergedProperty]] = []

    for merged in dedup_results:
        matched_anchor_id: str | None = None
        for url in merged.source_urls.values():
            aid = anchor_url_to_id.get(str(url))
            if aid is not None:
                matched_anchor_id = aid
                break

        if matched_anchor_id is not None:
            original = anchor_by_id[matched_anchor_id]
            if set(merged.sources) != set(original.sources):
                anchors_updated.append((matched_anchor_id, merged))
        else:
            genuinely_new.append(merged)

    return genuinely_new, anchors_updated


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


# Properties representing the same physical flat on different platforms
OPENRENT_FLAT = _make_property(
    source=PropertySource.OPENRENT,
    source_id="OR-100",
    price_pcm=2000,
    bedrooms=2,
    address="Flat 4, 123 Mare Street, Hackney",
    postcode="E8 3RH",
    latitude=51.5465,
    longitude=-0.0553,
    description="Lovely 2-bed flat near the park.",
    first_seen=datetime(2026, 2, 1, 10, 0),
)

ZOOPLA_SAME_FLAT = _make_property(
    source=PropertySource.ZOOPLA,
    source_id="ZP-200",
    price_pcm=1950,  # slightly different price
    bedrooms=2,
    address="123 Mare Street, Hackney E8 3RH",
    postcode="E8 3RH",
    latitude=51.54652,
    longitude=-0.05528,
    description="Spacious two bed near Mare Street.",
    first_seen=datetime(2026, 2, 8, 14, 0),
)

# A genuinely different property
DIFFERENT_FLAT = _make_property(
    source=PropertySource.ZOOPLA,
    source_id="ZP-300",
    price_pcm=1800,
    bedrooms=1,
    address="45 Kingsland Road, E8 2PB",
    postcode="E8 2PB",
    latitude=51.5491,
    longitude=-0.0764,
    description="1-bed in Dalston.",
    first_seen=datetime(2026, 2, 8, 15, 0),
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCrossPlatformRerun:
    """Test cross-platform dedup across separate pipeline runs."""

    @pytest.mark.asyncio
    async def test_property_on_platform_a_then_b_detected_as_duplicate(
        self, storage: PropertyStorage
    ) -> None:
        """Run 1: OpenRent saved. Run 2: same flat on Zoopla detected as dup."""
        # Run 1: Save the OpenRent listing
        merged_or = _make_merged(OPENRENT_FLAT)
        await storage.save_merged_property(merged_or)
        await storage.mark_notified(OPENRENT_FLAT.unique_id)

        # Run 2: Zoopla listing appears as "new" (different unique_id)
        merged_zp = _make_merged(ZOOPLA_SAME_FLAT)

        # Load DB anchors and combine with new properties for dedup
        db_anchors = await storage.get_recent_properties_for_dedup(days=30)
        assert len(db_anchors) == 1

        genuinely_new, anchors_updated = await _split_dedup_results([merged_zp], db_anchors)

        assert len(genuinely_new) == 0, "Zoopla listing should not be genuinely new"
        assert len(anchors_updated) == 1, "Anchor should have gained a source"

        # Update the DB
        anchor_id, updated = anchors_updated[0]
        await storage.update_merged_sources(anchor_id, updated)

        # Verify DB state
        tracked = await storage.get_property(OPENRENT_FLAT.unique_id)
        assert tracked is not None

        # Check sources were updated (via raw DB read)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT sources, source_urls, min_price, max_price FROM properties WHERE unique_id = ?",
            (OPENRENT_FLAT.unique_id,),
        )
        row = await cursor.fetchone()
        import json

        sources = json.loads(row["sources"])
        assert "openrent" in sources
        assert "zoopla" in sources
        assert len(sources) == 2

    @pytest.mark.asyncio
    async def test_property_on_platform_b_still_notified_if_no_match(
        self, storage: PropertyStorage
    ) -> None:
        """Run 1: OpenRent saved. Run 2: different flat from Zoopla → new."""
        # Run 1: Save the OpenRent listing
        merged_or = _make_merged(OPENRENT_FLAT)
        await storage.save_merged_property(merged_or)

        # Run 2: Different flat from Zoopla
        merged_diff = _make_merged(DIFFERENT_FLAT)

        db_anchors = await storage.get_recent_properties_for_dedup(days=30)
        genuinely_new, anchors_updated = await _split_dedup_results([merged_diff], db_anchors)
        assert len(genuinely_new) == 1
        assert genuinely_new[0].canonical.unique_id == DIFFERENT_FLAT.unique_id

    @pytest.mark.asyncio
    async def test_cross_platform_updates_price_range(self, storage: PropertyStorage) -> None:
        """OpenRent at £2000, Zoopla at £1950 → merged min=1950, max=2000."""
        # Save OpenRent listing
        merged_or = _make_merged(OPENRENT_FLAT)
        await storage.save_merged_property(merged_or)

        # Load anchors and simulate dedup
        db_anchors = await storage.get_recent_properties_for_dedup(days=30)
        merged_zp = _make_merged(ZOOPLA_SAME_FLAT)

        _, anchors_updated = await _split_dedup_results([merged_zp], db_anchors)
        assert len(anchors_updated) == 1

        anchor_id, updated = anchors_updated[0]
        await storage.update_merged_sources(anchor_id, updated)

        # Check price range in DB

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT min_price, max_price FROM properties WHERE unique_id = ?",
            (OPENRENT_FLAT.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["min_price"] == 1950
        assert row["max_price"] == 2000

    @pytest.mark.asyncio
    async def test_cross_platform_three_runs_three_platforms(
        self, storage: PropertyStorage
    ) -> None:
        """Run 1: Rightmove (outcode only). Run 2: Zoopla (full postcode).
        Run 3: OpenRent matches Zoopla."""
        # Rightmove has outcode only → won't cross-match
        rm = _make_property(
            source=PropertySource.RIGHTMOVE,
            source_id="RM-500",
            price_pcm=2000,
            bedrooms=2,
            address="Mare Street, Hackney",
            postcode="E8",
            latitude=None,
            longitude=None,
            first_seen=datetime(2026, 2, 1),
        )
        merged_rm = _make_merged(rm)
        await storage.save_merged_property(merged_rm)

        # Run 2: Zoopla with full postcode + coords
        merged_zp = _make_merged(ZOOPLA_SAME_FLAT)
        db_anchors = await storage.get_recent_properties_for_dedup(days=30)

        genuinely_new, _ = await _split_dedup_results([merged_zp], db_anchors)

        # Zoopla should be genuinely new (RM in different outcode block, no match)
        assert len(genuinely_new) == 1
        await storage.save_merged_property(genuinely_new[0])

        # Run 3: OpenRent appears — should match with Zoopla (full postcode + coords)
        merged_or = _make_merged(OPENRENT_FLAT)
        db_anchors = await storage.get_recent_properties_for_dedup(days=30)

        genuinely_new, anchors_updated = await _split_dedup_results([merged_or], db_anchors)

        # OpenRent should have been absorbed into Zoopla anchor
        assert len(genuinely_new) == 0
        assert len(anchors_updated) == 1

    @pytest.mark.asyncio
    async def test_anchor_properties_not_re_notified(self, storage: PropertyStorage) -> None:
        """Property with notification_status=sent, new source merges in →
        no new notification, status unchanged."""
        merged_or = _make_merged(OPENRENT_FLAT)
        await storage.save_merged_property(merged_or)
        await storage.mark_notified(OPENRENT_FLAT.unique_id)

        # Verify it's sent
        tracked = await storage.get_property(OPENRENT_FLAT.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT

        # Simulate cross-run dedup → update sources
        db_anchors = await storage.get_recent_properties_for_dedup(days=30)
        merged_zp = _make_merged(ZOOPLA_SAME_FLAT)

        _, anchors_updated = await _split_dedup_results([merged_zp], db_anchors)
        for anchor_id, merged in anchors_updated:
            await storage.update_merged_sources(anchor_id, merged)

        # notification_status must remain "sent"
        tracked = await storage.get_property(OPENRENT_FLAT.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT

    @pytest.mark.asyncio
    async def test_anchor_properties_not_re_quality_analyzed(
        self, storage: PropertyStorage
    ) -> None:
        """Property with quality analysis → new source merges in → analysis preserved."""
        from home_finder.models import (
            ConditionAnalysis,
            KitchenAnalysis,
            LightSpaceAnalysis,
            PropertyQualityAnalysis,
            SpaceAnalysis,
        )

        merged_or = _make_merged(OPENRENT_FLAT)
        await storage.save_merged_property(merged_or)

        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(overall_quality="modern"),
            condition=ConditionAnalysis(overall_condition="good"),
            light_space=LightSpaceAnalysis(natural_light="good"),
            space=SpaceAnalysis(living_room_sqm=20.0),
            overall_rating=4,
            summary="Good flat.",
        )
        await storage.save_quality_analysis(OPENRENT_FLAT.unique_id, analysis)

        # Update sources
        db_anchors = await storage.get_recent_properties_for_dedup(days=30)
        merged_zp = _make_merged(ZOOPLA_SAME_FLAT)

        _, anchors_updated = await _split_dedup_results([merged_zp], db_anchors)
        for anchor_id, merged in anchors_updated:
            await storage.update_merged_sources(anchor_id, merged)

        # Quality analysis must still exist
        qa = await storage.get_quality_analysis(OPENRENT_FLAT.unique_id)
        assert qa is not None
        assert qa.overall_rating == 4
        assert qa.summary == "Good flat."
