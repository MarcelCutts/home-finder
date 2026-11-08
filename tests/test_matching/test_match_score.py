"""A match score adds weighted signals for a pair of listings and grades the evidence."""

import pytest

from home_finder.matching.scoring import MatchConfidence, MatchScore, calculate_match_score
from home_finder.models import Property, PropertySource
from tests.support.matching import make_listing


class TestMatchScoreArithmetic:
    def test_total_sums_all_six_signals_including_partial_credit(self) -> None:
        score = MatchScore(
            image_hash=20,
            full_postcode=40,
            coordinates=35.6,
            street_name=20,
            outcode=10,
            price=10.5,
        )

        assert score.total == pytest.approx(136.1)
        assert score.signal_count == 6

    def test_signal_count_ignores_signals_without_credit(self) -> None:
        score = MatchScore(coordinates=0.0, outcode=10, price=0.0)

        assert score.signal_count == 1

    def test_to_dict_reports_breakdown_total_and_tier(self) -> None:
        result = MatchScore(image_hash=40, outcode=10).to_dict()

        assert result["image_hash"] == 40
        assert result["outcode"] == 10
        assert result["total"] == 50
        assert result["signal_count"] == 2
        assert result["confidence"] == "low"


class TestConfidenceTiers:
    """A match needs 60 points from at least two signals; HIGH needs 80 from three."""

    @pytest.mark.parametrize(
        ("score", "confidence", "is_match"),
        [
            pytest.param(
                MatchScore(full_postcode=40, coordinates=25, price=15),
                MatchConfidence.HIGH,
                True,
                id="80-points-3-signals",
            ),
            pytest.param(
                MatchScore(full_postcode=40, coordinates=24.9, price=15),
                MatchConfidence.MEDIUM,
                True,
                id="79.9-points-3-signals",
            ),
            pytest.param(
                MatchScore(full_postcode=40, coordinates=40),
                MatchConfidence.MEDIUM,
                True,
                id="80-points-2-signals",
            ),
            pytest.param(
                MatchScore(image_hash=40, street_name=20),
                MatchConfidence.MEDIUM,
                True,
                id="60-points-2-signals",
            ),
            pytest.param(
                MatchScore(image_hash=40, price=15),
                MatchConfidence.LOW,
                False,
                id="55-points-2-signals",
            ),
            # No real signal earns 60 points alone; the score is built directly to
            # isolate the minimum-signal rule from the point threshold.
            pytest.param(
                MatchScore(full_postcode=60),
                MatchConfidence.LOW,
                False,
                id="60-points-1-signal",
            ),
            pytest.param(
                MatchScore(image_hash=40),
                MatchConfidence.LOW,
                False,
                id="40-points-1-signal",
            ),
            pytest.param(
                MatchScore(coordinates=39.9),
                MatchConfidence.NONE,
                False,
                id="39.9-points",
            ),
            pytest.param(MatchScore(), MatchConfidence.NONE, False, id="no-signals"),
        ],
    )
    def test_tier_and_match_decision(
        self, score: MatchScore, confidence: MatchConfidence, is_match: bool
    ) -> None:
        assert score.confidence is confidence
        assert score.is_match is is_match


@pytest.fixture
def listing() -> Property:
    return make_listing(
        PropertySource.OPENRENT,
        "123",
        price_pcm=1500,
        address="Flat 1, 123 Mare Street, London",
        postcode="E8 3RH",
        latitude=51.5,
        longitude=-0.05,
    )


def _on_zoopla(listing: Property, **changes: object) -> Property:
    return listing.model_copy(
        update={"source": PropertySource.ZOOPLA, "source_id": "456", **changes}
    )


class TestPairScoring:
    def test_different_bedroom_counts_score_nothing(self, listing: Property) -> None:
        score = calculate_match_score(listing, _on_zoopla(listing, bedrooms=3))

        assert score == MatchScore()

    def test_same_home_earns_every_location_and_rent_signal(self, listing: Property) -> None:
        score = calculate_match_score(listing, _on_zoopla(listing, address="123 Mare St, E8"))

        assert score.full_postcode == 40
        assert score.coordinates == 40
        assert score.street_name == 20
        assert score.outcode == 10
        assert score.price == 15
        assert score.total == 125
        assert score.signal_count == 5
        assert score.confidence is MatchConfidence.HIGH

    def test_rent_within_tolerance_earns_more_than_half_price_credit(
        self, listing: Property
    ) -> None:
        # 30 / 1515 is 1.98% of the average rent, inside the 3% tolerance.
        score = calculate_match_score(listing, _on_zoopla(listing, price_pcm=1530))

        assert 7.5 < score.price < 15

    def test_rent_beyond_double_tolerance_earns_no_price_credit(self, listing: Property) -> None:
        # 200 / 1600 is 12.5% of the average rent.
        score = calculate_match_score(listing, _on_zoopla(listing, price_pcm=1700))

        assert score.price == 0
        assert score.total == 110
        assert score.is_match is True

    def test_coordinates_13_metres_apart_earn_partial_credit(self, listing: Property) -> None:
        score = calculate_match_score(
            listing, _on_zoopla(listing, latitude=51.5001, longitude=-0.0501)
        )

        assert 30 < score.coordinates < 40

    def test_coordinates_220_metres_apart_earn_nothing(self, listing: Property) -> None:
        score = calculate_match_score(listing, _on_zoopla(listing, latitude=51.502))

        assert score.coordinates == 0

    def test_abbreviated_street_earns_street_credit(self, listing: Property) -> None:
        score = calculate_match_score(listing, _on_zoopla(listing, address="456 Mare St, E8"))

        assert score.street_name == 20

    def test_different_streets_earn_no_street_credit(self, listing: Property) -> None:
        score = calculate_match_score(listing, _on_zoopla(listing, address="456 Victoria Road, E8"))

        assert score.street_name == 0

    def test_missing_postcode_on_one_side_earns_no_outcode_credit(self, listing: Property) -> None:
        score = calculate_match_score(listing, _on_zoopla(listing, postcode=None))

        assert score.outcode == 0

    def test_outcode_only_postcode_earns_outcode_but_not_full_postcode_credit(
        self, listing: Property
    ) -> None:
        rightmove = make_listing(
            PropertySource.RIGHTMOVE, "rm", postcode="E8", latitude=None, longitude=None
        )

        score = calculate_match_score(listing, rightmove)

        assert score.full_postcode == 0
        assert score.outcode == 10

    def test_openrent_property_type_address_matches_without_street_credit(self) -> None:
        """OpenRent puts the property type and building in the address field."""
        openrent = make_listing(
            PropertySource.OPENRENT,
            "or-quirk",
            address="3 Bed Maisonette, Lunan House, E3",
            postcode="E3 4AB",
            bedrooms=3,
            price_pcm=2800,
        )
        zoopla = make_listing(
            PropertySource.ZOOPLA,
            "zp-quirk",
            address="Lunan House, Devons Road, London E3 4AB",
            postcode="E3 4AB",
            bedrooms=3,
            price_pcm=2800,
        )

        score = calculate_match_score(openrent, zoopla)

        assert score.street_name == 0
        assert score.full_postcode == 40
        assert score.is_match is True


class TestGallerySignal:
    """Outcode-only listings modelled on a real pair that only gallery images could join.

    Without images the pair earns street (20) + outcode (10) + rent (15) = 45 points;
    the Rightmove coordinates point at the wrong area and earn nothing.
    """

    @pytest.fixture
    def onthemarket(self) -> Property:
        return make_listing(
            PropertySource.ONTHEMARKET,
            "18751817",
            address="Apex Gardens, Forster Road, Tottenham",
            postcode="N15",
            latitude=None,
            longitude=None,
        )

    @pytest.fixture
    def rightmove(self) -> Property:
        return make_listing(
            PropertySource.RIGHTMOVE,
            "172249586",
            address="Apex Gardens, Forster Road, Tottenham",
            postcode="N15",
            latitude=51.53,
            longitude=-0.23,
        )

    def test_without_gallery_hashes_the_pair_falls_short(
        self, onthemarket: Property, rightmove: Property
    ) -> None:
        score = calculate_match_score(onthemarket, rightmove)

        assert score.street_name == 20
        assert score.outcode == 10
        assert score.price == 15
        assert score.total == 45
        assert score.is_match is False

    def test_one_shared_gallery_image_earns_half_credit(
        self, onthemarket: Property, rightmove: Property
    ) -> None:
        image_hashes = {
            onthemarket.unique_id: ["a" * 16],
            rightmove.unique_id: ["a" * 16],
        }

        score = calculate_match_score(onthemarket, rightmove, image_hashes)

        assert score.image_hash == 20
        assert score.total == 65
        assert score.is_match is True

    def test_two_shared_gallery_images_earn_full_credit(
        self, onthemarket: Property, rightmove: Property
    ) -> None:
        image_hashes = {
            onthemarket.unique_id: ["a" * 16, "b" * 16],
            rightmove.unique_id: ["a" * 16, "b" * 16],
        }

        score = calculate_match_score(onthemarket, rightmove, image_hashes)

        assert score.image_hash == 40
        assert score.total == 85
        assert score.confidence is MatchConfidence.HIGH
