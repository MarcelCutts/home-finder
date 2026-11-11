"""Media admission (filters.media): a source floorplan, an exempt source, or enough photos.

Rightmove and Zoopla homes need a source-declared floorplan; OpenRent-only homes
are exempt. Tests that check the floorplan requirement therefore use non-exempt
sources.
"""

from pydantic import HttpUrl

from home_finder.filters.media import filter_by_floorplan, is_floorplan_exempt
from home_finder.models import PropertyImage, PropertySource


def _floorplan(origin: str = "source_declared", source=PropertySource.RIGHTMOVE) -> PropertyImage:
    return PropertyImage(
        url=HttpUrl("https://example.com/floor.jpg"),
        source=source,
        image_type="floorplan",
        floorplan_origin=origin,
    )


def _gallery(count: int, source=PropertySource.RIGHTMOVE) -> tuple[PropertyImage, ...]:
    return tuple(
        PropertyImage(
            url=HttpUrl(f"https://example.com/img{i}.jpg"), source=source, image_type="gallery"
        )
        for i in range(count)
    )


class TestFilterByFloorplan:
    def test_drops_a_home_without_a_floorplan(self, make_merged_property) -> None:
        with_fp = make_merged_property(sources=(PropertySource.RIGHTMOVE,), floorplan=_floorplan())
        without_fp = make_merged_property(sources=(PropertySource.RIGHTMOVE,), source_id="456")

        assert filter_by_floorplan([with_fp, without_fp]) == [with_fp]

    def test_admits_every_home_with_a_source_floorplan(self, make_merged_property) -> None:
        homes = [
            make_merged_property(
                sources=(PropertySource.RIGHTMOVE,), source_id=str(i), floorplan=_floorplan()
            )
            for i in range(3)
        ]

        assert filter_by_floorplan(homes) == homes

    def test_drops_every_home_without_a_floorplan(self, make_merged_property) -> None:
        homes = [
            make_merged_property(sources=(PropertySource.RIGHTMOVE,), source_id=str(i))
            for i in range(3)
        ]

        assert filter_by_floorplan(homes) == []

    def test_openrent_only_home_is_exempt(self, make_merged_property) -> None:
        openrent = make_merged_property(sources=(PropertySource.OPENRENT,), source_id="111")
        rightmove = make_merged_property(sources=(PropertySource.RIGHTMOVE,), source_id="222")

        assert filter_by_floorplan([openrent, rightmove]) == [openrent]

    def test_home_also_listed_elsewhere_loses_the_openrent_exemption(
        self, make_merged_property
    ) -> None:
        mixed = make_merged_property(
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA), source_id="333"
        )

        assert filter_by_floorplan([mixed]) == []

    def test_empty_input_admits_nothing(self) -> None:
        assert filter_by_floorplan([]) == []

    def test_unknown_origin_plan_needs_the_photo_alternative(self, make_merged_property) -> None:
        """A plan whose role the source never declared does not satisfy the gate."""
        home = make_merged_property(
            sources=(PropertySource.RIGHTMOVE,),
            floorplan=_floorplan(origin="unknown"),
            images=_gallery(1),
        )

        assert filter_by_floorplan([home]) == []
        assert filter_by_floorplan([home], min_gallery_for_photo_inference=1) == [home]


class TestPhotoAlternative:
    """With min_gallery_for_photo_inference set, enough gallery photos replace a floorplan."""

    def test_enough_photos_admit_a_home_without_a_floorplan(self, make_merged_property) -> None:
        home = make_merged_property(sources=(PropertySource.RIGHTMOVE,), images=_gallery(8))

        assert filter_by_floorplan([home], min_gallery_for_photo_inference=8) == [home]

    def test_too_few_photos_do_not(self, make_merged_property) -> None:
        home = make_merged_property(sources=(PropertySource.RIGHTMOVE,), images=_gallery(5))

        assert filter_by_floorplan([home], min_gallery_for_photo_inference=8) == []

    def test_default_threshold_of_zero_disables_the_alternative(self, make_merged_property) -> None:
        home = make_merged_property(sources=(PropertySource.RIGHTMOVE,), images=_gallery(10))

        assert filter_by_floorplan([home]) == []

    def test_a_source_floorplan_admits_without_photos(self, make_merged_property) -> None:
        home = make_merged_property(
            sources=(PropertySource.RIGHTMOVE,), images=(), floorplan=_floorplan()
        )

        assert filter_by_floorplan([home], min_gallery_for_photo_inference=8) == [home]

    def test_threshold_is_inclusive(self, make_merged_property) -> None:
        seven, eight = (
            make_merged_property(
                sources=(PropertySource.ZOOPLA,),
                source_id=str(n),
                images=_gallery(n, source=PropertySource.ZOOPLA),
            )
            for n in (7, 8)
        )

        assert filter_by_floorplan([seven, eight], min_gallery_for_photo_inference=8) == [eight]


class TestIsFloorplanExempt:
    def test_openrent_only_is_exempt(self) -> None:
        assert is_floorplan_exempt((PropertySource.OPENRENT,)) is True

    def test_rightmove_is_not_exempt(self) -> None:
        assert is_floorplan_exempt((PropertySource.RIGHTMOVE,)) is False

    def test_openrent_with_another_source_is_not_exempt(self) -> None:
        assert is_floorplan_exempt((PropertySource.OPENRENT, PropertySource.ZOOPLA)) is False

    def test_accepts_a_set_of_sources(self) -> None:
        assert is_floorplan_exempt({PropertySource.OPENRENT}) is True
