"""Tests for matching listing addresses to EPC certificates.

Every address and postcode here is made up.
"""

import pytest
from matcher import (
    Certificate,
    Evidence,
    ListingAddress,
    NoMatchReason,
    Outcome,
    group_homes,
    match_address,
    match_listing,
    normalise_postcode,
    outcode_of,
    parse_listing_address,
)


def cert(
    number: str,
    *lines: str,
    postcode: str = "Q9 9ZZ",
    uprn: str | None = None,
    band: str = "C",
    date: str = "2020-01-01",
) -> Certificate:
    return Certificate(
        certificate_number=number,
        address_lines=lines,
        postcode=postcode,
        uprn=uprn,
        band=band,
        registration_date=date,
    )


# A postcode with a converted house (three flats), two houses and a block.
POSTCODE_CERTS = [
    cert("c1", "Flat A", "12 Quillon Road", uprn="1"),
    cert("c2", "Flat B", "12 Quillon Road", uprn="2"),
    cert("c3", "Flat C, 12 Quillon Road", uprn="3"),
    cert("c4", "14, Quillon Road", uprn="4"),
    cert("c5", "16 Quillon Rd", uprn="5"),
    cert("c6", "Flat 1 Vexley House", "Quillon Road", uprn="6"),
    cert("c7", "Flat 2 Vexley House", "Quillon Road", uprn="7"),
    cert("c8", "3 Marrow Lane", uprn="8"),
]


class TestPostcodes:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("q9 9zz", "Q9 9ZZ"), ("Q99ZZ", "Q9 9ZZ"), (" QX12 3AB ", "QX12 3AB")],
    )
    def test_full_postcodes_are_normalised(self, raw: str, expected: str) -> None:
        assert normalise_postcode(raw) == expected

    @pytest.mark.parametrize("raw", ["Q9", "", None, "not a postcode"])
    def test_districts_and_junk_are_not_full_postcodes(self, raw: str | None) -> None:
        assert normalise_postcode(raw) is None

    def test_outcode_comes_from_full_or_partial_postcode(self) -> None:
        assert outcode_of("q9 9zz") == "Q9"
        assert outcode_of("QX12") == "QX12"
        assert outcode_of(None) is None


class TestParseListingAddress:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("Quillon Road", ListingAddress(streets=("quillon road",))),
            (
                "2 Bed Flat, Quillon Road, Q9",
                ListingAddress(streets=("quillon road",)),
            ),
            (
                "12 Quillon Rd, London Q9 9ZZ",
                ListingAddress(streets=("quillon road",), numbered="12 quillon road"),
            ),
            (
                "Flat 3, Vexley House, Quillon Road, Upper Brack, London, Q9",
                ListingAddress(streets=("quillon road",), buildings=("vexley house",), flat="3"),
            ),
            (
                "Vexley House, 1 Quillon Road, Upper Brack Q9",
                ListingAddress(
                    streets=("quillon road",),
                    buildings=("vexley house",),
                    numbered="1 quillon road",
                ),
            ),
            # Without a street the first part is taken as the building.
            (
                "The Vexley Building, Upper Brack, London, Q9",
                ListingAddress(buildings=("vexley building",)),
            ),
            # "Park", "Court", "Mews" can name a street when nothing else does.
            ("Studio Flat, Quillon Park, Q9", ListingAddress(streets=("quillon park",))),
            (
                "2 Quillon Mews, Upper Brack Q9",
                ListingAddress(streets=("quillon mews",), numbered="2 quillon mews"),
            ),
            # A second street after the first is kept as a fallback.
            (
                "Quillon Road, Marrow Street, Upper Brack Q9",
                ListingAddress(streets=("quillon road", "marrow street")),
            ),
            # "St" opening a part is Saint; closing it, Street.
            ("St Agnes Place, Q9", ListingAddress(streets=("saint agnes place",))),
            ("Agnes St, Q9", ListingAddress(streets=("agnes street",))),
            # A street word need not come last.
            (
                "1 Bed Flat, High Road Quillford, Q9",
                ListingAddress(streets=("high road quillford",)),
            ),
        ],
    )
    def test_portal_address_formats(self, address: str, expected: ListingAddress) -> None:
        assert parse_listing_address(address) == expected

    @pytest.mark.parametrize("address", ["London, Q9", "Q9", "Greater London"])
    def test_postcodes_and_london_are_never_names(self, address: str) -> None:
        assert parse_listing_address(address).is_empty

    def test_an_area_without_a_street_is_read_as_a_building(self) -> None:
        # With no street, a name no certificate carries means no match (TestMatchAddress).
        assert parse_listing_address("Upper Brack, London") == ListingAddress(
            buildings=("upper brack",)
        )


class TestGroupHomes:
    def test_certificates_for_one_address_are_one_home_newest_first(self) -> None:
        older = cert("old", "14, Quillon Road", uprn="4", date="2012-05-01")
        newer = cert("new", "14 Quillon Rd", uprn="4", date="2021-05-01")
        (home,) = group_homes([older, newer])
        assert [c.certificate_number for c in home.certificates] == ["new", "old"]

    def test_flats_sharing_the_buildings_uprn_stay_separate_homes(self) -> None:
        ground = cert("g", "Ground Floor Flat", "2 Quillon Road", uprn="9")
        first = cert("f", "First Floor Flat", "2 Quillon Road", uprn="9")
        assert len(group_homes([ground, first])) == 2

    def test_flats_in_one_house_are_separate_homes(self) -> None:
        assert len(group_homes(POSTCODE_CERTS[:3])) == 3


class TestMatchAddress:
    def test_house_number_picks_one_home(self) -> None:
        result = match_address(parse_listing_address("14 Quillon Road, Q9"), POSTCODE_CERTS)
        assert result.outcome is Outcome.ONE
        assert result.evidence is Evidence.NUMBER
        assert result.homes[0].newest.certificate_number == "c4"

    def test_abbreviated_street_still_matches(self) -> None:
        result = match_address(parse_listing_address("16 Quillon Road"), POSTCODE_CERTS)
        assert result.outcome is Outcome.ONE
        assert result.homes[0].newest.certificate_number == "c5"

    def test_street_alone_is_ambiguous_on_a_shared_postcode(self) -> None:
        result = match_address(parse_listing_address("Quillon Road, Q9"), POSTCODE_CERTS)
        assert result.outcome is Outcome.AMBIGUOUS
        assert result.evidence is Evidence.STREET
        assert len(result.homes) == 7  # Marrow Lane is excluded

    def test_number_of_a_converted_house_is_ambiguous_between_its_flats(self) -> None:
        result = match_address(parse_listing_address("12 Quillon Road"), POSTCODE_CERTS)
        assert result.outcome is Outcome.AMBIGUOUS
        assert {h.newest.certificate_number for h in result.homes} == {"c1", "c2", "c3"}

    def test_flat_letter_picks_one_flat_in_the_house(self) -> None:
        result = match_address(parse_listing_address("Flat C, 12 Quillon Road"), POSTCODE_CERTS)
        assert result.outcome is Outcome.ONE
        assert result.evidence is Evidence.FLAT
        assert result.homes[0].newest.certificate_number == "c3"

    def test_building_name_narrows_to_its_flats(self) -> None:
        result = match_address(parse_listing_address("Vexley House, Quillon Road"), POSTCODE_CERTS)
        assert result.evidence is Evidence.BUILDING
        assert {h.newest.certificate_number for h in result.homes} == {"c6", "c7"}

    def test_building_name_missing_from_certificates_keeps_the_street(self) -> None:
        result = match_address(
            parse_listing_address("Brackwater Court, Quillon Road"), POSTCODE_CERTS
        )
        assert result.evidence is Evidence.STREET
        assert len(result.homes) == 7

    @pytest.mark.parametrize(
        ("address", "reason"),
        [
            ("Nowhere Road", NoMatchReason.STREET_NOT_FOUND),
            ("99 Quillon Road", NoMatchReason.NUMBER_NOT_FOUND),
            ("Flat D, 12 Quillon Road", NoMatchReason.FLAT_NOT_FOUND),
            ("The Brackwater Building, Upper Brack", NoMatchReason.BUILDING_NOT_FOUND),
        ],
    )
    def test_a_stated_fact_no_certificate_carries_means_no_match(
        self, address: str, reason: NoMatchReason
    ) -> None:
        result = match_address(parse_listing_address(address), POSTCODE_CERTS)
        assert result.outcome is Outcome.NONE
        assert result.reason is reason

    def test_no_certificates_means_no_match(self) -> None:
        result = match_address(parse_listing_address("14 Quillon Road"), [])
        assert result.reason is NoMatchReason.NO_CERTIFICATES

    def test_a_house_alone_on_its_street_is_one_match_from_the_street(self) -> None:
        result = match_address(parse_listing_address("Marrow Lane"), POSTCODE_CERTS)
        assert result.outcome is Outcome.ONE
        assert result.evidence is Evidence.STREET


class TestMatchListing:
    def test_the_most_specific_portal_address_wins(self) -> None:
        result = match_listing(["Quillon Road, Q9", "14 Quillon Road, Q9"], POSTCODE_CERTS)
        assert result.outcome is Outcome.ONE
        assert result.homes[0].newest.certificate_number == "c4"

    def test_portal_addresses_picking_different_homes_are_ambiguous(self) -> None:
        result = match_listing(["14 Quillon Road", "16 Quillon Road"], POSTCODE_CERTS)
        assert result.outcome is Outcome.AMBIGUOUS
        assert result.variants_disagree
        assert len(result.homes) == 2

    def test_fewest_candidates_wins_among_ambiguous_addresses(self) -> None:
        result = match_listing(["Quillon Road", "12 Quillon Road"], POSTCODE_CERTS)
        assert result.outcome is Outcome.AMBIGUOUS
        assert len(result.homes) == 3

    def test_listing_without_street_or_building_has_no_match(self) -> None:
        result = match_listing(["London, Q9"], POSTCODE_CERTS)
        assert result.outcome is Outcome.NONE
        assert result.reason is NoMatchReason.NO_ADDRESS


class TestHouseNumbers:
    def test_a_flat_number_is_not_a_house_number(self) -> None:
        certs = [cert("f12", "Flat 12, Quillon Road"), cert("h14", "14 Quillon Road")]
        result = match_address(parse_listing_address("12 Quillon Road"), certs)
        assert result.reason is NoMatchReason.NUMBER_NOT_FOUND

    def test_a_house_number_after_a_flat_still_counts(self) -> None:
        certs = [cert("a", "Flat 2, 12 Quillon Road"), cert("b", "Flat 12, Quillon Road")]
        result = match_address(parse_listing_address("12 Quillon Road"), certs)
        assert result.outcome is Outcome.ONE
        assert result.homes[0].newest.certificate_number == "a"
