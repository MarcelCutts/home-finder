"""Select canonical listing fields and floorplan evidence within a matched group."""

from datetime import UTC, datetime

from pydantic import HttpUrl

from home_finder.matching.deduplication import (
    _build_best_canonical,
    _select_best_floorplan,
)
from home_finder.models import PropertyImage, PropertySource
from tests.support.matching import (
    as_aggregate,
    make_listing,
)


class TestBestOfCanonicalFields:
    def test_full_postcode_from_secondary_source(self) -> None:
        """Canonical has outcode "E8", other has "E8 3RH" -> result has "E8 3RH"."""
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            postcode="E8",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            postcode="E8 3RH",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.postcode == "E8 3RH"
        # Identity fields stay from first
        assert result.source == PropertySource.RIGHTMOVE
        assert result.source_id == "rm1"

    def test_coordinates_backfill_from_other_source(self) -> None:
        """Canonical has None coords, other has coords -> result has coords."""
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            latitude=None,
            longitude=None,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            latitude=51.5465,
            longitude=-0.0553,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.latitude == 51.5465
        assert result.longitude == -0.0553

    def test_keeps_existing_full_postcode(self) -> None:
        """Canonical already has full postcode -> kept unchanged."""
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            postcode="E8 3RH",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            postcode="E8 4XY",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.postcode == "E8 3RH"

    def test_keeps_existing_coordinates(self) -> None:
        """Canonical already has coords -> kept unchanged."""
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            latitude=51.0,
            longitude=-0.1,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            latitude=51.9,
            longitude=-0.9,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.latitude == 51.0
        assert result.longitude == -0.1

    def test_available_from_earliest_selected(self) -> None:
        """Picks earliest non-null available_from date."""
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
            available_from=datetime(2025, 3, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
            available_from=datetime(2025, 2, 15, tzinfo=UTC),
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.available_from == datetime(2025, 2, 15, tzinfo=UTC)

    def test_available_from_null_filled_from_other(self) -> None:
        """Null available_from -> gets date from other source."""
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
            available_from=None,
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
            available_from=datetime(2025, 2, 15, tzinfo=UTC),
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.available_from == datetime(2025, 2, 15, tzinfo=UTC)

    def test_all_null_available_from_stays_null(self) -> None:
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
            available_from=None,
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
            available_from=None,
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.available_from is None

    def test_identity_fields_never_change(self) -> None:
        """source, source_id, url, first_seen always from earliest MP."""
        p1 = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            postcode="E8",
            latitude=None,
            longitude=None,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            postcode="E8 3RH",
            latitude=51.5,
            longitude=-0.05,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.source == PropertySource.RIGHTMOVE
        assert result.source_id == "rm1"
        assert result.url == p1.url
        assert result.first_seen == datetime(2025, 1, 1, tzinfo=UTC)
        assert result.title == p1.title


class TestFloorplanQualitySelection:
    def test_zoopla_preferred_over_openrent(self) -> None:
        fp_or = PropertyImage(
            url=HttpUrl("https://or.com/floor.jpg"),
            source=PropertySource.OPENRENT,
            image_type="floorplan",
        )
        fp_zp = PropertyImage(
            url=HttpUrl("https://zp.com/floor.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="floorplan",
        )

        p1 = make_listing(
            PropertySource.OPENRENT,
            "or1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )

        sorted_mps = [
            as_aggregate(p1, floorplan=fp_or),
            as_aggregate(p2, floorplan=fp_zp),
        ]

        result = _select_best_floorplan(sorted_mps)

        assert result is not None
        assert result.source == PropertySource.ZOOPLA

    def test_only_available_floorplan_selected(self) -> None:
        fp = PropertyImage(
            url=HttpUrl("https://rm.com/floor.jpg"),
            source=PropertySource.RIGHTMOVE,
            image_type="floorplan",
        )

        p1 = make_listing(PropertySource.OPENRENT, "or1")
        p2 = make_listing(PropertySource.RIGHTMOVE, "rm1")

        sorted_mps = [
            as_aggregate(p1),  # no floorplan
            as_aggregate(p2, floorplan=fp),
        ]

        result = _select_best_floorplan(sorted_mps)

        assert result is not None
        assert result.source == PropertySource.RIGHTMOVE

    def test_no_floorplans_returns_none(self) -> None:
        p1 = make_listing(PropertySource.OPENRENT, "or1")
        p2 = make_listing(PropertySource.ZOOPLA, "zp1")

        sorted_mps = [as_aggregate(p1), as_aggregate(p2)]

        result = _select_best_floorplan(sorted_mps)

        assert result is None
