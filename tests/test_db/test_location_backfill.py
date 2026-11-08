"""Ward and commute backfill for homes saved without them."""

from home_finder.models import MergedProperty, Property, PropertySource, TransportMode


def _home(prop: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


class TestWards:
    async def test_ward_given_at_save_is_shown_on_the_detail_page(
        self, storage, make_property
    ) -> None:
        home = _home(make_property())
        await storage.save_merged_property(home, ward="London Fields")

        detail = await storage.web.get_property_detail(home.unique_id)
        assert detail is not None
        assert detail["ward"] == "London Fields"

    async def test_only_homes_saved_without_a_ward_need_one(self, storage, make_property) -> None:
        with_ward = _home(make_property())
        without_ward = _home(make_property(source=PropertySource.RIGHTMOVE))
        await storage.save_merged_property(with_ward, ward="London Fields")
        await storage.save_merged_property(without_ward)

        detail = await storage.web.get_property_detail(without_ward.unique_id)
        assert detail is not None and detail.get("ward") is None
        needing = await storage.get_properties_without_ward()
        assert [row["unique_id"] for row in needing] == [without_ward.unique_id]

    async def test_update_wards_sets_each_home_and_counts_them(
        self, storage, make_property
    ) -> None:
        first = _home(make_property())
        second = _home(make_property(source=PropertySource.RIGHTMOVE))
        await storage.save_merged_property(first)
        await storage.save_merged_property(second)

        updated = await storage.update_wards(
            {first.unique_id: "London Fields", second.unique_id: "Dalston"}
        )

        assert updated == 2
        for home, ward in ((first, "London Fields"), (second, "Dalston")):
            detail = await storage.web.get_property_detail(home.unique_id)
            assert detail is not None and detail["ward"] == ward
        assert await storage.get_properties_without_ward() == []

    async def test_empty_ward_map_updates_nothing(self, storage) -> None:
        assert await storage.update_wards({}) == 0


class TestCommuteBackfill:
    async def test_only_homes_with_coordinates_and_no_commute_need_one(
        self, storage, make_property
    ) -> None:
        needs = make_property(latitude=51.5465, longitude=-0.0553)
        measured = make_property()
        no_coordinates = make_property(
            source=PropertySource.RIGHTMOVE, postcode=None, latitude=None, longitude=None
        )
        await storage.save_property(needs)
        await storage.save_property(
            measured, commute_minutes=15, transport_mode=TransportMode.CYCLING
        )
        await storage.save_property(no_coordinates)

        [needing] = await storage.get_properties_needing_commute()
        assert needing.unique_id == needs.unique_id
        assert needing.latitude == 51.5465

    async def test_update_commute_data_sets_minutes_and_mode(self, storage, make_property) -> None:
        prop = make_property()
        await storage.save_property(prop)

        updated = await storage.update_commute_data({prop.unique_id: (15, TransportMode.CYCLING)})

        assert updated == 1
        tracked = await storage.get_property(prop.unique_id)
        assert tracked is not None
        assert (tracked.commute_minutes, tracked.transport_mode) == (15, TransportMode.CYCLING)
        assert await storage.get_properties_needing_commute() == []

    async def test_empty_commute_lookup_updates_nothing(self, storage) -> None:
        assert await storage.update_commute_data({}) == 0
