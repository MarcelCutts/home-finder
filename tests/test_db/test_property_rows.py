"""Single-listing rows in the properties table, their images and dependent rows."""

from pydantic import HttpUrl

from home_finder.models import NotificationStatus, PropertyImage, PropertySource, TransportMode


class TestSaveAndGetProperty:
    async def test_saved_listing_reads_back_as_pending(self, storage, make_property) -> None:
        prop = make_property(source_id="12345", price_pcm=1900)
        await storage.save_property(prop)

        tracked = await storage.get_property(prop.unique_id)
        assert tracked is not None
        assert tracked.property.source_id == "12345"
        assert tracked.property.price_pcm == 1900
        assert tracked.notification_status == NotificationStatus.PENDING

    async def test_commute_is_saved_with_the_listing(self, storage, make_property) -> None:
        prop = make_property()
        await storage.save_property(prop, commute_minutes=20, transport_mode=TransportMode.CYCLING)

        tracked = await storage.get_property(prop.unique_id)
        assert tracked is not None
        assert (tracked.commute_minutes, tracked.transport_mode) == (20, TransportMode.CYCLING)

    async def test_unknown_id_reads_as_none(self, storage) -> None:
        assert await storage.get_property("nonexistent:123") is None

    async def test_every_saved_listing_is_listed(self, storage, make_property) -> None:
        first = make_property()
        second = make_property(source=PropertySource.RIGHTMOVE)
        await storage.save_property(first)
        await storage.save_property(second)

        listed = await storage.get_all_properties()
        assert {t.property.unique_id for t in listed} == {first.unique_id, second.unique_id}

    async def test_saving_a_listing_again_updates_its_row(self, storage, make_property) -> None:
        prop = make_property(price_pcm=1900)
        await storage.save_property(prop)
        await storage.save_property(prop.model_copy(update={"price_pcm": 1800}))

        [tracked] = await storage.get_all_properties()
        assert tracked.property.price_pcm == 1800


class TestGetPropertyImagesAndRow:
    async def test_returns_images_and_property(self, storage, make_property) -> None:
        prop = make_property()
        await storage.save_property(prop)
        image = PropertyImage(
            url=HttpUrl("https://example.com/img.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        await storage.save_property_images(prop.unique_id, [image])

        images, row, _text = await storage.get_property_images_and_row(prop.unique_id)
        assert images == [image]
        assert row is not None
        assert row.unique_id == prop.unique_id

    async def test_unknown_id_has_no_images_or_row(self, storage) -> None:
        images, row, _text = await storage.get_property_images_and_row("nonexistent:999")
        assert images == []
        assert row is None


_CHILD_TABLES = (
    "property_images",
    "quality_analyses",
    "status_events",
    "viewing_messages",
    "price_history",
    "enquiry_log",
)


async def test_deleting_a_properties_row_cascades_to_its_child_rows(storage, make_property):
    """Deleted directly, bypassing delete_property's merge-safety checks."""
    prop = make_property()
    uid = prop.unique_id
    await storage.save_property(prop)
    await storage.save_property_images(
        uid,
        [
            PropertyImage(
                source=prop.source, url=HttpUrl("https://example.com/img.jpg"), image_type="gallery"
            )
        ],
    )
    conn = await storage._get_connection()
    await conn.execute(
        "INSERT INTO quality_analyses (property_unique_id, analysis_json) VALUES (?, ?)",
        (uid, '{"overall_rating": 4}'),
    )
    await conn.execute(
        "INSERT INTO status_events (property_unique_id, to_status) VALUES (?, ?)",
        (uid, "shortlisted"),
    )
    await conn.execute(
        "INSERT INTO viewing_messages (property_unique_id, message) VALUES (?, ?)", (uid, "Hello")
    )
    await conn.execute(
        "INSERT INTO price_history (property_unique_id, old_price, new_price, change_amount)"
        " VALUES (?, ?, ?, ?)",
        (uid, 2000, 1900, -100),
    )
    await conn.execute(
        "INSERT INTO enquiry_log (property_unique_id, portal, message) VALUES (?, ?, ?)",
        (uid, "openrent", "Hi there"),
    )
    await conn.commit()

    async def child_counts() -> dict[str, int]:
        counts = {}
        for table in _CHILD_TABLES:
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE property_unique_id = ?", (uid,)
            )
            counts[table] = (await cursor.fetchone())[0]
        return counts

    assert await child_counts() == dict.fromkeys(_CHILD_TABLES, 1)
    await conn.execute("DELETE FROM properties WHERE unique_id = ?", (uid,))
    await conn.commit()
    assert await child_counts() == dict.fromkeys(_CHILD_TABLES, 0)
