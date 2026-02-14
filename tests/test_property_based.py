"""Property-based tests using Hypothesis.

Tests invariants of core algorithms: dedup scoring, address normalization,
postcode extraction. These discover edge cases that example-based tests miss.
"""

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from home_finder.filters.scoring import (
    COORDINATE_DISTANCE_METERS,
    MATCH_THRESHOLD,
    MINIMUM_SIGNALS,
    PRICE_TOLERANCE,
    MatchScore,
    calculate_match_score,
    graduated_coordinate_score,
    graduated_price_score,
    haversine_distance,
    is_full_postcode,
)
from home_finder.models import Property, PropertySource
from home_finder.utils.address import extract_outcode, is_outcode, normalize_street_name


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# UK-like prices (realistic range for London rentals)
prices = st.integers(min_value=1, max_value=50000)

# Coordinates in Greater London area
london_lat = st.floats(min_value=51.3, max_value=51.7, allow_nan=False, allow_infinity=False)
london_lon = st.floats(min_value=-0.5, max_value=0.3, allow_nan=False, allow_infinity=False)

# Valid UK outcode patterns (matching FULL_POSTCODE_PATTERN's outcode part)
uk_outcodes = st.from_regex(r"[A-Z]{1,2}[0-9][0-9A-Z]?", fullmatch=True)

# Valid full UK postcodes (matching FULL_POSTCODE_PATTERN exactly, with ASCII space)
uk_postcodes = st.from_regex(r"[A-Z]{1,2}[0-9][0-9A-Z]? [0-9][A-Z]{2}", fullmatch=True)


# ---------------------------------------------------------------------------
# graduated_price_score
# ---------------------------------------------------------------------------


class TestGraduatedPriceScoreProperties:
    @given(prices)
    def test_exact_match_is_max(self, price: int) -> None:
        """Identical prices always get 1.0."""
        assert graduated_price_score(price, price) == 1.0

    @given(prices, prices)
    def test_symmetry(self, a: int, b: int) -> None:
        """Score(A, B) == Score(B, A)."""
        assert graduated_price_score(a, b) == graduated_price_score(b, a)

    @given(prices, prices)
    def test_range_is_zero_to_one(self, a: int, b: int) -> None:
        """Score is always in [0.0, 1.0]."""
        score = graduated_price_score(a, b)
        assert 0.0 <= score <= 1.0

    @given(prices)
    def test_zero_price_gives_zero(self, price: int) -> None:
        """Zero price always yields 0.0 (unless both are zero → 1.0)."""
        assert graduated_price_score(0, price) == 0.0
        assert graduated_price_score(price, 0) == 0.0

    def test_both_zero_is_exact_match(self) -> None:
        assert graduated_price_score(0, 0) == 1.0

    @given(prices)
    def test_monotonic_decay(self, base_price: int) -> None:
        """Score decreases (or stays same) as difference grows."""
        assume(base_price >= 100)  # avoid rounding issues with tiny prices
        scores = []
        for pct in [0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.10]:
            other = int(base_price * (1 + pct))
            scores.append(graduated_price_score(base_price, other))
        # Each score should be >= the next (non-increasing)
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1] - 1e-10  # float tolerance


# ---------------------------------------------------------------------------
# graduated_coordinate_score
# ---------------------------------------------------------------------------


class TestGraduatedCoordinateScoreProperties:
    @given(london_lat, london_lon)
    def test_same_point_is_max(self, lat: float, lon: float) -> None:
        """Same coordinates always get 1.0 (when both are non-zero/truthy)."""
        # The code uses truthiness checks (`if not prop.latitude`), so 0.0 is treated as missing
        assume(lat != 0.0 and lon != 0.0)
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="test",
            url="https://example.com/1",
            title="Test",
            price_pcm=1800,
            bedrooms=1,
            address="Test",
            latitude=lat,
            longitude=lon,
        )
        assert graduated_coordinate_score(prop, prop) == 1.0

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_symmetry(self, lat1: float, lon1: float, lat2: float, lon2: float) -> None:
        """Score(A, B) == Score(B, A)."""
        assume(all(v != 0.0 for v in [lat1, lon1, lat2, lon2]))
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="a",
            url="https://example.com/a",
            title="A",
            price_pcm=1800,
            bedrooms=1,
            address="A",
            latitude=lat1,
            longitude=lon1,
        )
        prop2 = Property(
            source=PropertySource.ZOOPLA,
            source_id="b",
            url="https://example.com/b",
            title="B",
            price_pcm=1800,
            bedrooms=1,
            address="B",
            latitude=lat2,
            longitude=lon2,
        )
        assert graduated_coordinate_score(prop1, prop2) == graduated_coordinate_score(prop2, prop1)

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_range_is_zero_to_one(self, lat1: float, lon1: float, lat2: float, lon2: float) -> None:
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="a",
            url="https://example.com/a",
            title="A",
            price_pcm=1800,
            bedrooms=1,
            address="A",
            latitude=lat1,
            longitude=lon1,
        )
        prop2 = Property(
            source=PropertySource.ZOOPLA,
            source_id="b",
            url="https://example.com/b",
            title="B",
            price_pcm=1800,
            bedrooms=1,
            address="B",
            latitude=lat2,
            longitude=lon2,
        )
        score = graduated_coordinate_score(prop1, prop2)
        assert 0.0 <= score <= 1.0

    def test_missing_coords_gives_zero(self) -> None:
        prop_with = Property(
            source=PropertySource.OPENRENT,
            source_id="a",
            url="https://example.com/a",
            title="A",
            price_pcm=1800,
            bedrooms=1,
            address="A",
            latitude=51.5,
            longitude=-0.1,
        )
        prop_without = Property(
            source=PropertySource.ZOOPLA,
            source_id="b",
            url="https://example.com/b",
            title="B",
            price_pcm=1800,
            bedrooms=1,
            address="B",
        )
        assert graduated_coordinate_score(prop_with, prop_without) == 0.0
        assert graduated_coordinate_score(prop_without, prop_with) == 0.0


# ---------------------------------------------------------------------------
# haversine_distance
# ---------------------------------------------------------------------------


class TestHaversineProperties:
    @given(london_lat, london_lon)
    def test_same_point_is_zero(self, lat: float, lon: float) -> None:
        assert haversine_distance(lat, lon, lat, lon) == 0.0

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_symmetry(self, lat1: float, lon1: float, lat2: float, lon2: float) -> None:
        d1 = haversine_distance(lat1, lon1, lat2, lon2)
        d2 = haversine_distance(lat2, lon2, lat1, lon1)
        assert abs(d1 - d2) < 1e-6

    @given(london_lat, london_lon, london_lat, london_lon)
    def test_non_negative(self, lat1: float, lon1: float, lat2: float, lon2: float) -> None:
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
        assert d13 <= d12 + d23 + 1e-6  # float tolerance


# ---------------------------------------------------------------------------
# MatchScore properties
# ---------------------------------------------------------------------------


class TestMatchScoreProperties:
    def test_empty_score_is_not_match(self) -> None:
        score = MatchScore()
        assert not score.is_match
        assert score.total == 0
        assert score.signal_count == 0

    def test_single_signal_not_enough(self) -> None:
        """Even a full 40-point signal needs a second signal for MINIMUM_SIGNALS."""
        score = MatchScore(full_postcode=40.0)
        assert score.signal_count == 1
        assert not score.is_match

    def test_two_signals_above_threshold_is_match(self) -> None:
        score = MatchScore(full_postcode=40.0, coordinates=40.0)
        assert score.total == 80.0
        assert score.signal_count == 2
        assert score.is_match


# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------


class TestNormalizeStreetProperties:
    @given(st.text(min_size=0, max_size=500))
    def test_never_crashes(self, address: str) -> None:
        """normalize_street_name should handle any input without exceptions."""
        result = normalize_street_name(address)
        assert isinstance(result, str)

    @given(st.text(min_size=0, max_size=200))
    def test_result_is_lowercase(self, address: str) -> None:
        result = normalize_street_name(address)
        assert result == result.lower()

    @given(st.text(min_size=0, max_size=200))
    def test_no_leading_trailing_whitespace(self, address: str) -> None:
        result = normalize_street_name(address)
        assert result == result.strip()

    @given(st.text(min_size=0, max_size=200))
    def test_no_double_spaces(self, address: str) -> None:
        result = normalize_street_name(address)
        assert "  " not in result


# ---------------------------------------------------------------------------
# Postcode extraction
# ---------------------------------------------------------------------------


class TestExtractOutcodeProperties:
    @given(uk_postcodes)
    def test_valid_postcode_extracts_outcode(self, postcode: str) -> None:
        """Any valid UK postcode should yield a non-None outcode."""
        result = extract_outcode(postcode)
        assert result is not None
        assert len(result) >= 2

    @given(uk_postcodes)
    def test_outcode_is_prefix_of_postcode(self, postcode: str) -> None:
        """Extracted outcode should be a prefix of the normalized postcode."""
        result = extract_outcode(postcode)
        assert result is not None
        assert postcode.upper().strip().startswith(result)

    def test_none_returns_none(self) -> None:
        assert extract_outcode(None) is None

    def test_empty_returns_none(self) -> None:
        assert extract_outcode("") is None


# ---------------------------------------------------------------------------
# is_outcode
# ---------------------------------------------------------------------------


class TestIsOutcodeProperties:
    @given(uk_outcodes)
    def test_valid_outcodes_recognized(self, outcode: str) -> None:
        assert is_outcode(outcode) is True

    @given(st.text(alphabet=st.characters(whitelist_categories=("Ll",)), min_size=5, max_size=20))
    def test_lowercase_words_not_outcodes(self, word: str) -> None:
        """Long lowercase-only strings should not be outcodes."""
        assert is_outcode(word) is False


# ---------------------------------------------------------------------------
# is_full_postcode
# ---------------------------------------------------------------------------


class TestIsFullPostcodeProperties:
    @given(uk_postcodes)
    def test_valid_full_postcodes(self, postcode: str) -> None:
        assert is_full_postcode(postcode) is True

    @given(uk_outcodes)
    def test_outcodes_are_not_full_postcodes(self, outcode: str) -> None:
        """An outcode alone should not be considered a full postcode."""
        assert is_full_postcode(outcode) is False

    def test_none_is_not_full_postcode(self) -> None:
        assert is_full_postcode(None) is False


# ---------------------------------------------------------------------------
# calculate_match_score invariants
# ---------------------------------------------------------------------------


class TestCalculateMatchScoreProperties:
    def _make_prop(
        self,
        source: PropertySource = PropertySource.OPENRENT,
        source_id: str = "1",
        bedrooms: int = 1,
        price: int = 1800,
        postcode: str | None = "E8 3RH",
        lat: float | None = 51.5465,
        lon: float | None = -0.0553,
    ) -> Property:
        return Property(
            source=source,
            source_id=source_id,
            url=f"https://example.com/{source_id}",
            title="Test",
            price_pcm=price,
            bedrooms=bedrooms,
            address="123 Test St",
            postcode=postcode,
            latitude=lat,
            longitude=lon,
        )

    @given(st.integers(1, 5), st.integers(1, 5))
    def test_different_bedrooms_gives_zero(self, beds1: int, beds2: int) -> None:
        """Properties with different bedroom counts should score 0."""
        assume(beds1 != beds2)
        prop1 = self._make_prop(bedrooms=beds1)
        prop2 = self._make_prop(source=PropertySource.ZOOPLA, source_id="2", bedrooms=beds2)
        score = calculate_match_score(prop1, prop2)
        assert score.total == 0.0

    def test_identical_properties_score_high(self) -> None:
        prop1 = self._make_prop()
        prop2 = self._make_prop(source=PropertySource.ZOOPLA, source_id="2")
        score = calculate_match_score(prop1, prop2)
        assert score.total > MATCH_THRESHOLD
        assert score.signal_count >= MINIMUM_SIGNALS
        assert score.is_match
