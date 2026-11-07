"""Hypothesis properties of match scoring and the signals it combines.

The street key, outcode key and outcode detection properties at the end exercise
``home_finder.utils.address``.
"""

from itertools import pairwise

from hypothesis import assume, given
from hypothesis import strategies as st

from home_finder.matching.scoring import (
    SCORE_COORDINATES,
    SCORE_FULL_POSTCODE,
    SCORE_IMAGE_HASH,
    SCORE_OUTCODE,
    SCORE_PRICE,
    SCORE_STREET_NAME,
    MatchConfidence,
    MatchScore,
    calculate_match_score,
    graduated_coordinate_score,
    graduated_price_score,
    haversine_distance,
    is_full_postcode,
)
from home_finder.models import Property, PropertySource
from home_finder.utils.address import extract_outcode, is_outcode, normalize_street_name
from tests.support.matching import make_listing

rents = st.integers(min_value=1, max_value=50000)

# Greater London, which spans the Greenwich meridian.
london_lat = st.floats(min_value=51.3, max_value=51.7, allow_nan=False, allow_infinity=False)
london_lon = st.floats(min_value=-0.5, max_value=0.3, allow_nan=False, allow_infinity=False)

# The outcode and full forms accepted by FULL_POSTCODE_PATTERN, with an ASCII space.
uk_outcodes = st.from_regex(r"[A-Z]{1,2}[0-9][0-9A-Z]?", fullmatch=True)
uk_postcodes = st.from_regex(r"[A-Z]{1,2}[0-9][0-9A-Z]? [0-9][A-Z]{2}", fullmatch=True)

_STREETS = [
    "Mare Street",
    "Kingsland Road",
    "Church Street",
    "High Street",
    "Victoria Road",
    "Green Lanes",
    "Stoke Newington Road",
    "Morning Lane",
    "Graham Road",
    "Dalston Lane",
]

london_listings = st.builds(
    make_listing,
    st.sampled_from(list(PropertySource)),
    st.integers(min_value=1, max_value=999999).map(str),
    price_pcm=st.integers(min_value=800, max_value=3500),
    bedrooms=st.integers(min_value=0, max_value=4),
    postcode=st.sampled_from(
        ["E8 3RH", "E8 4AA", "E9 5LN", "E3 4AB", "N16 0AS", "N16 7AB", "E17 4RD", "E5 8QJ"]
    ),
    latitude=st.floats(min_value=51.50, max_value=51.60),
    longitude=st.floats(min_value=-0.10, max_value=0.02),
    address=st.sampled_from([f"123 {street}, London" for street in _STREETS]),
)


def _located_at(source_id: str, latitude: float | None, longitude: float | None) -> Property:
    return make_listing(PropertySource.OPENRENT, source_id, latitude=latitude, longitude=longitude)


class TestGraduatedPriceCreditProperties:
    @given(rents)
    def test_equal_rents_earn_full_credit(self, rent: int) -> None:
        assert graduated_price_score(rent, rent) == 1.0

    @given(rents, rents)
    def test_credit_is_symmetric(self, a: int, b: int) -> None:
        assert graduated_price_score(a, b) == graduated_price_score(b, a)

    @given(rents, rents)
    def test_credit_stays_between_zero_and_one(self, a: int, b: int) -> None:
        assert 0.0 <= graduated_price_score(a, b) <= 1.0

    @given(rents)
    def test_zero_rent_earns_no_credit(self, rent: int) -> None:
        assert graduated_price_score(0, rent) == 0.0
        assert graduated_price_score(rent, 0) == 0.0

    def test_two_zero_rents_count_as_equal(self) -> None:
        assert graduated_price_score(0, 0) == 1.0

    @given(rents)
    def test_credit_never_rises_as_the_difference_grows(self, base_rent: int) -> None:
        assume(base_rent >= 100)  # integer rounding distorts percentages of tiny rents
        credits = [
            graduated_price_score(base_rent, int(base_rent * (1 + pct)))
            for pct in [0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.10]
        ]

        for nearer, farther in pairwise(credits):
            assert nearer >= farther - 1e-10


class TestGraduatedCoordinateCreditProperties:
    @given(london_lat, london_lon)
    def test_same_point_earns_full_credit(self, lat: float, lon: float) -> None:
        listing = _located_at("a", lat, lon)

        assert graduated_coordinate_score(listing, listing) == 1.0

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_credit_is_symmetric(self, lat1: float, lon1: float, lat2: float, lon2: float) -> None:
        a = _located_at("a", lat1, lon1)
        b = _located_at("b", lat2, lon2)

        assert graduated_coordinate_score(a, b) == graduated_coordinate_score(b, a)

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_credit_stays_between_zero_and_one(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        a = _located_at("a", lat1, lon1)
        b = _located_at("b", lat2, lon2)

        assert 0.0 <= graduated_coordinate_score(a, b) <= 1.0

    def test_missing_coordinates_on_either_side_earn_nothing(self) -> None:
        located = _located_at("a", 51.5, -0.1)
        unlocated = _located_at("b", None, None)

        assert graduated_coordinate_score(located, unlocated) == 0.0
        assert graduated_coordinate_score(unlocated, located) == 0.0


class TestHaversineProperties:
    @given(london_lat, london_lon)
    def test_same_point_is_zero(self, lat: float, lon: float) -> None:
        assert haversine_distance(lat, lon, lat, lon) == 0.0

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_distance_is_symmetric(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        d1 = haversine_distance(lat1, lon1, lat2, lon2)
        d2 = haversine_distance(lat2, lon2, lat1, lon1)

        assert abs(d1 - d2) < 1e-6

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_distance_is_non_negative(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        assert haversine_distance(lat1, lon1, lat2, lon2) >= 0.0

    @given(london_lat, london_lon, london_lat, london_lon, london_lat, london_lon)
    def test_triangle_inequality(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        lat3: float,
        lon3: float,
    ) -> None:
        d12 = haversine_distance(lat1, lon1, lat2, lon2)
        d23 = haversine_distance(lat2, lon2, lat3, lon3)
        d13 = haversine_distance(lat1, lon1, lat3, lon3)

        assert d13 <= d12 + d23 + 1e-6


class TestPairScoreProperties:
    @given(london_listings)
    def test_self_comparison_earns_every_location_and_rent_signal(self, listing: Property) -> None:
        score = calculate_match_score(listing, listing)

        assert score.total == (
            SCORE_FULL_POSTCODE
            + SCORE_COORDINATES
            + SCORE_STREET_NAME
            + SCORE_OUTCODE
            + SCORE_PRICE
        )
        assert score.confidence is MatchConfidence.HIGH

    @given(london_listings, london_listings)
    def test_score_is_symmetric(self, a: Property, b: Property) -> None:
        forward = calculate_match_score(a, b)
        backward = calculate_match_score(b, a)

        assert abs(forward.total - backward.total) < 0.01
        assert forward.signal_count == backward.signal_count
        assert forward.is_match == backward.is_match

    @given(london_listings)
    def test_different_bedroom_counts_score_nothing(self, listing: Property) -> None:
        other = listing.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "other",
                "bedrooms": (listing.bedrooms + 1) % 5,
            }
        )

        assert calculate_match_score(listing, other) == MatchScore()

    @given(london_listings, london_listings)
    def test_each_signal_stays_within_its_weight(self, a: Property, b: Property) -> None:
        score = calculate_match_score(a, b)

        assert 0 <= score.image_hash <= SCORE_IMAGE_HASH
        assert 0 <= score.full_postcode <= SCORE_FULL_POSTCODE
        assert 0 <= score.coordinates <= SCORE_COORDINATES
        assert 0 <= score.street_name <= SCORE_STREET_NAME
        assert 0 <= score.outcode <= SCORE_OUTCODE
        assert 0 <= score.price <= SCORE_PRICE


class TestFullPostcodeProperties:
    @given(uk_postcodes)
    def test_outcode_with_incode_is_full(self, postcode: str) -> None:
        assert is_full_postcode(postcode) is True

    @given(uk_outcodes)
    def test_outcode_alone_is_not_full(self, outcode: str) -> None:
        assert is_full_postcode(outcode) is False


class TestStreetKeyProperties:
    @given(st.text(max_size=500))
    def test_key_is_trimmed_lowercase_and_single_spaced(self, address: str) -> None:
        key = normalize_street_name(address)

        assert key == key.lower()
        assert key == key.strip()
        assert "  " not in key


class TestOutcodeKeyProperties:
    @given(uk_postcodes)
    def test_outcode_is_the_postcode_part_before_the_space(self, postcode: str) -> None:
        assert extract_outcode(postcode) == postcode.split(" ")[0]


class TestOutcodeDetectionProperties:
    @given(uk_outcodes)
    def test_outcode_forms_are_recognised(self, outcode: str) -> None:
        assert is_outcode(outcode) is True

    @given(st.text(alphabet=st.characters(whitelist_categories=("Ll",)), min_size=5, max_size=20))  # type: ignore[arg-type]
    def test_lowercase_words_are_not_outcodes(self, word: str) -> None:
        assert is_outcode(word) is False
