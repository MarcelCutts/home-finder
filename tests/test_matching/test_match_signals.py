"""Signals that feed a match score: distance, rent closeness, postcode form, street and outcode.

The street and outcode keys come from ``home_finder.utils.address``.
"""

import pytest

from home_finder.matching.scoring import (
    coordinates_match,
    graduated_coordinate_score,
    graduated_price_score,
    haversine_distance,
    is_full_postcode,
    prices_match,
)
from home_finder.models import Property, PropertySource
from tests.support.matching import make_listing


def _listing_at(latitude: float | None, longitude: float | None, source_id: str = "1") -> Property:
    return make_listing(PropertySource.OPENRENT, source_id, latitude=latitude, longitude=longitude)


class TestHaversineDistance:
    def test_landmark_distance_agrees_with_spherical_law_of_cosines(self) -> None:
        """London Eye (51.5033, -0.1195) to Big Ben (51.5007, -0.1246) is about 456 m.

        The expected value comes from the spherical law of cosines with the same
        6,371 km Earth radius, so a corrupted haversine term moves the result.
        """
        distance = haversine_distance(51.5033, -0.1195, 51.5007, -0.1246)

        assert distance == pytest.approx(456.3, abs=0.5)

    def test_longitude_offset_shrinks_with_latitude(self) -> None:
        """At 51.5 degrees north, 0.001 degrees of longitude spans R * cos(lat) * 0.001 rad."""
        distance = haversine_distance(51.5, -0.05, 51.5, -0.051)

        assert distance == pytest.approx(69.22, abs=0.01)


class TestPriceTolerance:
    """Rents match when they differ by at most 3% of their average."""

    def test_identical_rents_match(self) -> None:
        assert prices_match(2000, 2000) is True

    @pytest.mark.parametrize(
        ("rent_a", "rent_b"),
        [
            pytest.param(2000, 2030, id="1.5-percent"),
            pytest.param(1970, 2030, id="exactly-3-percent-of-average"),
            pytest.param(2030, 1970, id="exactly-3-percent-reversed"),
        ],
    )
    def test_difference_within_three_percent_of_average_matches(
        self, rent_a: int, rent_b: int
    ) -> None:
        assert prices_match(rent_a, rent_b) is True

    @pytest.mark.parametrize(
        ("rent_a", "rent_b"),
        [
            # 3% of the higher rent, but 3.05% of the average.
            pytest.param(1940, 2000, id="3.05-percent-of-average"),
            pytest.param(1969, 2031, id="3.1-percent-of-average"),
            pytest.param(2000, 2100, id="4.9-percent-of-average"),
        ],
    )
    def test_difference_beyond_three_percent_of_average_does_not_match(
        self, rent_a: int, rent_b: int
    ) -> None:
        assert prices_match(rent_a, rent_b) is False

    def test_zero_rent_matches_only_zero(self) -> None:
        assert prices_match(0, 0) is True
        assert prices_match(0, 2000) is False
        assert prices_match(2000, 0) is False


class TestFullPostcodeDetection:
    @pytest.mark.parametrize("postcode", ["E8 3RH", "SW1A 1AA", "EC1A 1BB", "W1A 0AX"])
    def test_outcode_with_incode_is_full(self, postcode: str) -> None:
        assert is_full_postcode(postcode) is True

    @pytest.mark.parametrize("postcode", ["E8", "SW1A", "EC1", None, ""])
    def test_outcode_alone_or_missing_is_not_full(self, postcode: str | None) -> None:
        assert is_full_postcode(postcode) is False


class TestCoordinateProximity:
    """``coordinates_match`` accepts listings up to 50 m apart."""

    def test_same_point_matches(self) -> None:
        assert coordinates_match(_listing_at(51.5074, -0.1278), _listing_at(51.5074, -0.1278))

    def test_listings_33_metres_apart_match(self) -> None:
        assert coordinates_match(_listing_at(51.5074, -0.1278), _listing_at(51.5077, -0.1278))

    def test_listings_511_metres_apart_do_not_match(self) -> None:
        assert not coordinates_match(_listing_at(51.5074, -0.1278), _listing_at(51.5120, -0.1278))

    @pytest.mark.parametrize("missing_side", ["first", "second"])
    def test_missing_coordinates_on_either_side_never_match(self, missing_side: str) -> None:
        located = _listing_at(51.5074, -0.1278)
        unlocated = _listing_at(None, None, source_id="2")
        pair = (unlocated, located) if missing_side == "first" else (located, unlocated)

        assert coordinates_match(*pair) is False


class TestGraduatedCoordinateCredit:
    """Credit falls linearly from 1.0 at 0 m to 0.5 at 50 m and to 0.0 at 100 m."""

    def test_half_credit_at_50_metres(self) -> None:
        # 0.00045 degrees of latitude is 50.04 m.
        score = graduated_coordinate_score(_listing_at(51.5, -0.05), _listing_at(51.50045, -0.05))

        assert score == pytest.approx(0.5, abs=0.01)

    def test_quarter_credit_at_75_metres(self) -> None:
        # 0.000675 degrees of latitude is 75.06 m.
        score = graduated_coordinate_score(_listing_at(51.5, -0.05), _listing_at(51.500675, -0.05))

        assert score == pytest.approx(0.25, abs=0.01)

    def test_no_credit_beyond_100_metres(self) -> None:
        # 0.0014 degrees of latitude is 156 m.
        score = graduated_coordinate_score(_listing_at(51.5, -0.05), _listing_at(51.5014, -0.05))

        assert score == 0.0


class TestGraduatedPriceCredit:
    """Credit falls linearly from 1.0 for equal rents to 0.5 at the 3% tolerance and
    to 0.0 at 6%, measuring the difference against the average of the two rents."""

    def test_half_credit_at_the_tolerance(self) -> None:
        # 46 / 1523 is 3.02% of the average rent.
        assert graduated_price_score(1500, 1546) == pytest.approx(0.5, abs=0.01)

    def test_partial_credit_between_tolerance_and_double_tolerance(self) -> None:
        # 68 / 1534 is 4.43% of the average rent, so credit is 0.5 - (1.43 / 3) * 0.5.
        assert graduated_price_score(1500, 1568) == pytest.approx(0.26, abs=0.01)

    def test_no_credit_beyond_double_tolerance(self) -> None:
        # 100 / 1550 is 6.45% of the average rent.
        assert graduated_price_score(1500, 1600) == 0.0
