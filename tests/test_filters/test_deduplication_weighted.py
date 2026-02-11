"""Tests for weighted scoring deduplication."""

import pytest

from home_finder.filters.deduplication import (
    MATCH_THRESHOLD,
    MINIMUM_SIGNALS,
    MatchConfidence,
    MatchScore,
    calculate_match_score,
    graduated_coordinate_score,
    graduated_price_score,
)
from home_finder.models import Property, PropertySource
from home_finder.utils.address import extract_outcode, normalize_street_name


class TestMatchScore:
    """Tests for MatchScore dataclass."""

    def test_total_calculation(self) -> None:
        score = MatchScore(image_hash=40, outcode=10, price=15)
        assert score.total == 65

    def test_signal_count(self) -> None:
        score = MatchScore(image_hash=40, outcode=10, price=15)
        assert score.signal_count == 3

    def test_confidence_high(self) -> None:
        score = MatchScore(image_hash=40, full_postcode=40, price=15)
        assert score.confidence == MatchConfidence.HIGH
        assert score.is_match is True

    def test_confidence_medium(self) -> None:
        score = MatchScore(full_postcode=40, price=15, outcode=10)  # 65, 3 signals
        assert score.confidence == MatchConfidence.MEDIUM
        assert score.is_match is True

    def test_confidence_low_below_threshold(self) -> None:
        score = MatchScore(image_hash=40, outcode=10)  # 50 points, 2 signals
        assert score.total == 50
        assert score.signal_count == 2
        assert score.confidence == MatchConfidence.LOW
        assert score.is_match is False  # Below 60 threshold

    def test_two_signals_at_threshold(self) -> None:
        score = MatchScore(image_hash=40, street_name=20)  # 60 points, 2 signals
        assert score.total == 60
        assert score.signal_count == 2
        assert score.confidence == MatchConfidence.MEDIUM
        assert score.is_match is True

    def test_below_threshold_two_signals(self) -> None:
        """image_hash (40) + price (15) = 55 is below threshold 60."""
        score = MatchScore(image_hash=40, price=15)
        assert score.total == 55
        assert score.signal_count == 2
        assert score.confidence == MatchConfidence.LOW
        assert score.is_match is False

    def test_single_signal_not_enough(self) -> None:
        """Single signal alone should not be a match."""
        score = MatchScore(full_postcode=40)
        assert score.signal_count == 1
        assert score.is_match is False

    def test_image_hash_alone_not_enough(self) -> None:
        """Image hash alone (40 pts, 1 signal) should not match."""
        score = MatchScore(image_hash=40)
        assert score.total == 40
        assert score.signal_count == 1
        assert score.is_match is False

    def test_to_dict(self) -> None:
        score = MatchScore(image_hash=40, outcode=10)
        result = score.to_dict()
        assert result["image_hash"] == 40
        assert result["outcode"] == 10
        assert result["total"] == 50
        assert result["signal_count"] == 2
        assert result["confidence"] == "low"

    def test_float_fields(self) -> None:
        """MatchScore fields are float for graduated scoring."""
        score = MatchScore(coordinates=35.6, price=10.5)
        assert score.total == pytest.approx(46.1)
        assert score.signal_count == 2


class TestStreetNormalization:
    """Tests for street name normalization."""

    def test_basic_abbreviation(self) -> None:
        assert normalize_street_name("Mare St") == "mare street"

    def test_with_flat_number(self) -> None:
        assert normalize_street_name("Flat 2, Mare Street") == "mare street"

    def test_with_building_name(self) -> None:
        result = normalize_street_name("The Towers, 123 Mare Street, London")
        assert result == "mare street"

    def test_road_abbreviation(self) -> None:
        assert normalize_street_name("Victoria Rd") == "victoria road"

    def test_with_postcode(self) -> None:
        result = normalize_street_name("Mare Street, E8 3RH")
        assert "e8" not in result
        assert result == "mare street"

    def test_removes_london(self) -> None:
        result = normalize_street_name("Mare Street, Hackney, London")
        assert "london" not in result
        assert "hackney" not in result

    def test_avenue_abbreviation(self) -> None:
        assert normalize_street_name("Green Ave") == "green avenue"

    def test_gardens_abbreviation(self) -> None:
        assert normalize_street_name("Rose Gdns") == "rose gardens"

    def test_with_house_number(self) -> None:
        result = normalize_street_name("123 Mare Street")
        assert result == "mare street"

    def test_with_letter_suffix(self) -> None:
        result = normalize_street_name("45a Victoria Road")
        assert result == "victoria road"


class TestExtractOutcode:
    """Tests for outcode extraction."""

    def test_full_postcode(self) -> None:
        assert extract_outcode("E8 3RH") == "E8"

    def test_partial_postcode(self) -> None:
        assert extract_outcode("E8") == "E8"

    def test_longer_outcode(self) -> None:
        assert extract_outcode("SW1A 1AA") == "SW1A"

    def test_ec_postcode(self) -> None:
        assert extract_outcode("EC1V 9BD") == "EC1V"

    def test_none_input(self) -> None:
        assert extract_outcode(None) is None

    def test_invalid_input(self) -> None:
        assert extract_outcode("invalid") is None

    def test_lowercase_input(self) -> None:
        assert extract_outcode("e8 3rh") == "E8"


class TestCalculateMatchScore:
    """Tests for match score calculation."""

    @pytest.fixture
    def base_property(self) -> Property:
        return Property(
            source=PropertySource.OPENRENT,
            source_id="123",
            url="https://openrent.com/123",
            title="2 bed flat",
            price_pcm=1500,
            bedrooms=2,
            address="Flat 1, 123 Mare Street, London",
            postcode="E8 3RH",
            latitude=51.5,
            longitude=-0.05,
        )

    def test_different_bedrooms_no_match(self, base_property: Property) -> None:
        prop2 = base_property.model_copy(update={"source_id": "456", "bedrooms": 3})
        score = calculate_match_score(base_property, prop2)
        assert score.total == 0

    def test_full_postcode_and_price_match(self, base_property: Property) -> None:
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
                "price_pcm": 1530,  # Within 3% → graduated price
            }
        )
        score = calculate_match_score(base_property, prop2)
        assert score.full_postcode == 40
        # Price is graduated: ~2% diff → partial credit (not full 15)
        assert 9 < score.price < 15
        assert score.is_match is True

    def test_coordinates_match_graduated(self, base_property: Property) -> None:
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
                "latitude": 51.5001,  # ~13m away
                "longitude": -0.0501,
            }
        )
        score = calculate_match_score(base_property, prop2)
        # Graduated: ~13m → score ≈ 40 * 0.87 ≈ 34.8
        assert 30 < score.coordinates < 40

    def test_coordinates_exact_match(self, base_property: Property) -> None:
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
            }
        )
        score = calculate_match_score(base_property, prop2)
        # Exact same coordinates → full score
        assert score.coordinates == 40

    def test_street_name_match(self, base_property: Property) -> None:
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
                "address": "456 Mare St, E8",  # Abbreviated form
            }
        )
        score = calculate_match_score(base_property, prop2)
        assert score.street_name == 20

    def test_rightmove_scenario_without_image(self, base_property: Property) -> None:
        """Rightmove with outcode only cannot match without image hash."""
        rightmove = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="RM123",
            url="https://rightmove.co.uk/RM123",
            title="2 bed flat",
            price_pcm=1500,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8",  # Only outcode
            latitude=None,
            longitude=None,
        )

        # Without image hash, only street (20) + outcode (10) + price (15) = 45
        score = calculate_match_score(base_property, rightmove)
        assert score.street_name == 20
        assert score.outcode == 10
        assert score.price == 15
        assert score.total == 45
        assert score.is_match is False  # Below 60

    def test_rightmove_scenario_with_image(self, base_property: Property) -> None:
        """Rightmove with outcode only CAN match with image hash."""
        rightmove = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="RM123",
            url="https://rightmove.co.uk/RM123",
            title="2 bed flat",
            price_pcm=1500,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8",  # Only outcode
            latitude=None,
            longitude=None,
        )

        # With image hash: 40 + 20 + 10 + 15 = 85
        image_hashes = {
            base_property.unique_id: "a" * 16,
            rightmove.unique_id: "a" * 16,  # Same hash
        }
        score = calculate_match_score(base_property, rightmove, image_hashes)
        assert score.image_hash == 40
        assert score.street_name == 20
        assert score.outcode == 10
        assert score.price == 15
        assert score.total == 85
        assert score.is_match is True
        assert score.confidence == MatchConfidence.HIGH

    def test_price_mismatch_reduces_score(self, base_property: Property) -> None:
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
                "price_pcm": 1700,  # ~12.5% diff, well outside 6% graduated range
            }
        )
        score = calculate_match_score(base_property, prop2)
        assert score.price == 0
        # Still matches on postcode + coords + street + outcode
        assert score.full_postcode == 40
        assert score.coordinates == 40  # Exact same coords → full score
        assert score.is_match is True  # 40 + 40 + 20 + 10 = 110

    def test_different_streets_no_street_points(self, base_property: Property) -> None:
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
                "address": "456 Victoria Road, E8",
            }
        )
        score = calculate_match_score(base_property, prop2)
        assert score.street_name == 0


class TestMatchThresholdConstants:
    """Tests for threshold constants."""

    def test_threshold_value(self) -> None:
        assert MATCH_THRESHOLD == 60

    def test_minimum_signals_value(self) -> None:
        assert MINIMUM_SIGNALS == 2

    def test_postcode_plus_price_plus_outcode_meets_threshold(self) -> None:
        """Full postcode (40) + price (15) + outcode (10) = 65 meets threshold.

        In practice, outcode always fires when full_postcode fires
        (E8 3RH → outcode E8).
        """
        score = MatchScore(full_postcode=40, price=15, outcode=10)
        assert score.total == 65
        assert score.signal_count >= MINIMUM_SIGNALS
        assert score.is_match is True

    def test_postcode_plus_price_below_threshold(self) -> None:
        """Full postcode (40) + price (15) = 55 below threshold without outcode."""
        score = MatchScore(full_postcode=40, price=15)
        assert score.total == 55
        assert score.is_match is False  # 55 < 60

    def test_coords_plus_price_plus_outcode_meets_threshold(self) -> None:
        """Coordinates (40) + price (15) + outcode (10) = 65 meets threshold."""
        score = MatchScore(coordinates=40, price=15, outcode=10)
        assert score.total == 65
        assert score.is_match is True


class TestGraduatedScoring:
    """Tests for graduated coordinate and price scoring."""

    def test_coordinate_exact(self) -> None:
        """Exact same coordinates → 1.0."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="test",
            price_pcm=1000,
            bedrooms=1,
            address="Test St",
            latitude=51.5,
            longitude=-0.05,
        )
        prop2 = prop.model_copy(update={"source_id": "2"})
        assert graduated_coordinate_score(prop, prop2) == 1.0

    def test_coordinate_at_half_range(self) -> None:
        """At ~50m → 0.5."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="test",
            price_pcm=1000,
            bedrooms=1,
            address="Test St",
            latitude=51.5,
            longitude=-0.05,
        )
        # ~50m away (approx 0.00045° lat at 51.5°)
        prop2 = prop1.model_copy(
            update={"source_id": "2", "latitude": 51.50045}
        )
        score = graduated_coordinate_score(prop1, prop2)
        assert 0.45 < score < 0.55

    def test_coordinate_beyond_double_range(self) -> None:
        """Beyond 100m → 0.0."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="test",
            price_pcm=1000,
            bedrooms=1,
            address="Test St",
            latitude=51.5,
            longitude=-0.05,
        )
        # ~150m away
        prop2 = prop1.model_copy(
            update={"source_id": "2", "latitude": 51.5014}
        )
        assert graduated_coordinate_score(prop1, prop2) == 0.0

    def test_coordinate_missing(self) -> None:
        """Missing coordinates → 0.0."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url="https://example.com/1",
            title="test",
            price_pcm=1000,
            bedrooms=1,
            address="Test St",
            latitude=51.5,
            longitude=-0.05,
        )
        prop2 = prop1.model_copy(
            update={"source_id": "2", "latitude": None, "longitude": None}
        )
        assert graduated_coordinate_score(prop1, prop2) == 0.0

    def test_price_exact(self) -> None:
        """Exact same price → 1.0."""
        assert graduated_price_score(1500, 1500) == 1.0

    def test_price_at_tolerance(self) -> None:
        """At 3% difference → 0.5."""
        # 3% of avg(1500, 1545) = 1522.5 → diff = 45.675
        # 1500 vs 1546: diff/avg = 46/1523 ≈ 3.02%
        score = graduated_price_score(1500, 1546)
        assert 0.45 < score < 0.55

    def test_price_beyond_double_tolerance(self) -> None:
        """Beyond 6% → 0.0."""
        # 1500 vs 1600: diff=100, avg=1550, pct=6.45%
        assert graduated_price_score(1500, 1600) == 0.0

    def test_price_zero(self) -> None:
        """Zero price → 0.0."""
        assert graduated_price_score(0, 1500) == 0.0
        assert graduated_price_score(1500, 0) == 0.0
