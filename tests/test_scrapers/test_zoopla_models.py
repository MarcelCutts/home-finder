"""Tests for Zoopla Pydantic models."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from home_finder.scrapers.zoopla_models import (
    ZooplaFeature,
    ZooplaImage,
    ZooplaListing,
    ZooplaListingsAdapter,
    ZooplaListingUris,
    ZooplaNextData,
    ZooplaPosition,
)


class TestZooplaListingUris:
    """Tests for ZooplaListingUris model."""

    def test_parse_valid_uris(self) -> None:
        """Test parsing valid listing URIs."""
        uris = ZooplaListingUris.model_validate({"detail": "/to-rent/details/123/"})
        assert uris.detail == "/to-rent/details/123/"

    def test_default_empty_detail(self) -> None:
        """Test that detail defaults to empty string."""
        uris = ZooplaListingUris.model_validate({})
        assert uris.detail == ""

    def test_ignores_extra_fields(self) -> None:
        """Test that extra fields are ignored."""
        uris = ZooplaListingUris.model_validate(
            {
                "detail": "/test/",
                "contact": "/contact/",
                "unknown": "value",
            }
        )
        assert uris.detail == "/test/"
        assert not hasattr(uris, "contact")


class TestZooplaFeature:
    """Tests for ZooplaFeature model."""

    def test_parse_bed_feature(self) -> None:
        """Test parsing a bedroom feature."""
        feature = ZooplaFeature.model_validate({"iconId": "bed", "content": 2})
        assert feature.icon_id == "bed"
        assert feature.content == 2

    def test_parse_feature_with_string_content(self) -> None:
        """Test parsing feature with string content."""
        feature = ZooplaFeature.model_validate({"iconId": "bath", "content": "1"})
        assert feature.icon_id == "bath"
        assert feature.content == "1"

    def test_defaults(self) -> None:
        """Test default values."""
        feature = ZooplaFeature.model_validate({})
        assert feature.icon_id == ""
        assert feature.content is None


class TestZooplaImage:
    """Tests for ZooplaImage model."""

    def test_parse_image(self) -> None:
        """Test parsing image data."""
        image = ZooplaImage.model_validate({"src": "//example.com/img.jpg"})
        assert image.src == "//example.com/img.jpg"

    def test_default_empty_src(self) -> None:
        """Test that src defaults to empty string."""
        image = ZooplaImage.model_validate({})
        assert image.src == ""


class TestZooplaPosition:
    """Tests for ZooplaPosition model."""

    def test_parse_position(self) -> None:
        """Test parsing position data."""
        pos = ZooplaPosition.model_validate({"lat": 51.5, "lng": -0.1})
        assert pos.lat == 51.5
        assert pos.lng == -0.1

    def test_defaults_to_none(self) -> None:
        """Test that coordinates default to None."""
        pos = ZooplaPosition.model_validate({})
        assert pos.lat is None
        assert pos.lng is None


class TestZooplaListing:
    """Tests for ZooplaListing model."""

    @pytest.fixture
    def valid_listing_json(self) -> str:
        """Valid listing JSON for testing."""
        return """
        {
            "listingId": 12345,
            "listingUris": {"detail": "/to-rent/details/12345/"},
            "price": "£1,900 pcm",
            "priceUnformatted": 1900,
            "features": [{"iconId": "bed", "content": 2}],
            "title": "2 bed flat to rent",
            "address": "123 Main St, London E8 1AB",
            "image": {"src": "//images.zoopla.co.uk/test.jpg"},
            "pos": {"lat": 51.5, "lng": -0.1}
        }
        """

    def test_parse_valid_listing(self, valid_listing_json: str) -> None:
        """Test parsing a complete valid listing."""
        listing = ZooplaListing.model_validate_json(valid_listing_json)
        assert listing.listing_id == 12345
        assert listing.get_detail_url() == "/to-rent/details/12345/"
        assert listing.get_price_pcm() == 1900
        assert listing.get_bedrooms() == 2
        assert listing.get_image_url() == "https://images.zoopla.co.uk/test.jpg"

    def test_missing_required_field_raises_error(self) -> None:
        """Test that missing listingId raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ZooplaListing.model_validate_json('{"price": "£1000"}')

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("listingId",)
        assert errors[0]["type"] == "missing"

    def test_wrong_type_raises_error(self) -> None:
        """Test that wrong type for listingId raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ZooplaListing.model_validate_json('{"listingId": "not-a-number"}')

        errors = exc_info.value.errors()
        assert errors[0]["loc"] == ("listingId",)
        assert "int" in errors[0]["type"]

    def test_ignores_extra_fields(self) -> None:
        """Test that unknown fields are ignored."""
        listing = ZooplaListing.model_validate_json("""
        {
            "listingId": 123,
            "unknownField": "ignored",
            "anotherUnknown": {"nested": "value"}
        }
        """)
        assert listing.listing_id == 123
        assert not hasattr(listing, "unknownField")

    def test_get_detail_url_from_listing_uris(self) -> None:
        """Test getting detail URL from listingUris (RSC format)."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "listingUris": {"detail": "/to-rent/details/1/"},
            }
        )
        assert listing.get_detail_url() == "/to-rent/details/1/"

    def test_get_detail_url_from_detail_url(self) -> None:
        """Test getting detail URL from detailUrl (old format)."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "detailUrl": "/to-rent/details/1/",
            }
        )
        assert listing.get_detail_url() == "/to-rent/details/1/"

    def test_get_detail_url_none_when_missing(self) -> None:
        """Test that get_detail_url returns None when no URL provided."""
        listing = ZooplaListing.model_validate({"listingId": 1})
        assert listing.get_detail_url() is None

    def test_get_price_pcm_from_unformatted(self) -> None:
        """Test getting price from priceUnformatted field."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "priceUnformatted": 1850,
                "price": "£1,850 pcm",
            }
        )
        assert listing.get_price_pcm() == 1850

    def test_get_price_pcm_from_formatted_string(self) -> None:
        """Test parsing price from formatted string."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "price": "£2,300 pcm",
            }
        )
        assert listing.get_price_pcm() == 2300

    def test_get_price_pcm_weekly_conversion(self) -> None:
        """Test that weekly prices are converted to monthly."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "price": "£500 pw",
            }
        )
        # £500 * 52 / 12 = £2166.67, rounded down to 2166
        assert listing.get_price_pcm() == 2166

    def test_get_price_pcm_none_when_missing(self) -> None:
        """Test that get_price_pcm returns None when no price."""
        listing = ZooplaListing.model_validate({"listingId": 1})
        assert listing.get_price_pcm() is None

    def test_get_bedrooms_from_features_list(self) -> None:
        """Test getting bedrooms from features array (RSC format)."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "features": [
                    {"iconId": "bed", "content": 2},
                    {"iconId": "bath", "content": 1},
                ],
            }
        )
        assert listing.get_bedrooms() == 2

    def test_get_bedrooms_from_features_dict(self) -> None:
        """Test getting bedrooms from features dict (old format)."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "features": {"beds": 3, "baths": 2},
            }
        )
        assert listing.get_bedrooms() == 3

    def test_get_bedrooms_from_title(self) -> None:
        """Test getting bedrooms from title when not in features."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "title": "2 bedroom flat to rent",
                "features": [],
            }
        )
        assert listing.get_bedrooms() == 2

    def test_get_bedrooms_studio(self) -> None:
        """Test that studio is recognized as 0 bedrooms."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "title": "Studio flat to rent",
                "features": [],
            }
        )
        assert listing.get_bedrooms() == 0

    def test_get_bedrooms_none_when_missing(self) -> None:
        """Test that get_bedrooms returns None when not found."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "title": "Property to rent",
                "features": [],
            }
        )
        assert listing.get_bedrooms() is None

    def test_get_image_url_adds_protocol(self) -> None:
        """Test that protocol-relative URLs get https added."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "image": {"src": "//example.com/img.jpg"},
            }
        )
        assert listing.get_image_url() == "https://example.com/img.jpg"

    def test_get_image_url_preserves_https(self) -> None:
        """Test that https URLs are preserved."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "image": {"src": "https://example.com/img.jpg"},
            }
        )
        assert listing.get_image_url() == "https://example.com/img.jpg"

    def test_get_image_url_none_when_missing(self) -> None:
        """Test that get_image_url returns None when no image."""
        listing = ZooplaListing.model_validate({"listingId": 1})
        assert listing.get_image_url() is None

    def test_get_address_falls_back_to_title(self) -> None:
        """Test that get_address falls back to title when address is empty."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "title": "1 bed flat, London",
                "address": "",
            }
        )
        assert listing.get_address() == "1 bed flat, London"

    def test_get_title_falls_back_to_address(self) -> None:
        """Test that get_title falls back to address when title is empty."""
        listing = ZooplaListing.model_validate(
            {
                "listingId": 1,
                "title": "",
                "address": "123 Main Street, London",
            }
        )
        assert listing.get_title() == "123 Main Street, London"


class TestZooplaNextData:
    """Tests for ZooplaNextData model."""

    def test_parse_full_structure(self) -> None:
        """Test parsing the full Next.js data structure."""
        data = ZooplaNextData.model_validate_json("""
        {
            "props": {
                "pageProps": {
                    "regularListingsFormatted": [
                        {"listingId": 1, "title": "First"},
                        {"listingId": 2, "title": "Second"}
                    ]
                }
            }
        }
        """)
        listings = data.get_listings()
        assert len(listings) == 2
        assert listings[0].listing_id == 1
        assert listings[1].listing_id == 2

    def test_parse_empty_listings(self) -> None:
        """Test parsing with empty listings array."""
        data = ZooplaNextData.model_validate_json("""
        {
            "props": {
                "pageProps": {
                    "regularListingsFormatted": []
                }
            }
        }
        """)
        assert data.get_listings() == []

    def test_parse_missing_structure(self) -> None:
        """Test parsing with missing nested structure."""
        data = ZooplaNextData.model_validate_json('{"props": {}}')
        assert data.get_listings() == []

    def test_parse_minimal(self) -> None:
        """Test parsing minimal valid JSON."""
        data = ZooplaNextData.model_validate_json("{}")
        assert data.get_listings() == []


class TestZooplaListingsAdapter:
    """Tests for ZooplaListingsAdapter TypeAdapter."""

    def test_validate_json_list(self) -> None:
        """Test validating JSON array of listings."""
        json_str = """
        [
            {"listingId": 1, "title": "First"},
            {"listingId": 2, "title": "Second"}
        ]
        """
        listings = ZooplaListingsAdapter.validate_json(json_str)
        assert len(listings) == 2
        assert listings[0].listing_id == 1
        assert listings[1].listing_id == 2

    def test_validate_empty_array(self) -> None:
        """Test validating empty JSON array."""
        listings = ZooplaListingsAdapter.validate_json("[]")
        assert listings == []

    def test_validation_error_in_list(self) -> None:
        """Test that validation error in list item is caught."""
        with pytest.raises(ValidationError) as exc_info:
            ZooplaListingsAdapter.validate_json('[{"listingId": "invalid"}]')

        errors = exc_info.value.errors()
        # Error should indicate position in list
        assert errors[0]["loc"][0] == 0
        assert errors[0]["loc"][1] == "listingId"


class TestZooplaModelIntegration:
    """Integration tests for Zoopla models with real fixture data."""

    def test_parse_fixture_data(self, fixtures_path: Path) -> None:
        """Test parsing the actual fixture file."""
        fixture_path = fixtures_path / "zoopla_nextdata.json"
        if not fixture_path.exists():
            pytest.skip("Fixture file not found")

        json_content = fixture_path.read_text()
        data = ZooplaNextData.model_validate_json(json_content)
        listings = data.get_listings()

        assert len(listings) == 4

        # Verify first listing
        listing1 = listings[0]
        assert listing1.listing_id == 67123456
        assert listing1.get_price_pcm() == 1850
        assert listing1.get_bedrooms() == 1
        assert "Victoria Park Road" in listing1.address
        assert listing1.pos is not None
        assert listing1.pos.lat == 51.5465

        # Verify studio listing
        listing3 = listings[2]
        assert listing3.get_bedrooms() == 0  # Studio

        # Verify weekly price conversion
        listing4 = listings[3]
        assert listing4.get_price_pcm() == 1950  # £450 * 52 / 12

    @pytest.fixture
    def fixtures_path(self) -> Path:
        """Path to test fixtures."""
        return Path(__file__).parent.parent / "fixtures"
