"""Tests for property quality analysis filter."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic.types import ToolUseBlock
from pydantic import HttpUrl, ValidationError

from pydantic import BaseModel as _BaseModel

from home_finder.filters.quality import (
    _CIRCUIT_BREAKER_COOLDOWN,
    _CIRCUIT_BREAKER_THRESHOLD,
    _MODEL_PAIRS,
    _EvaluationResponse,
    _VisualAnalysisResponse,
    EVALUATION_TOOL,
    VISUAL_ANALYSIS_TOOL,
    APIUnavailableError,
    PropertyQualityFilter,
    assess_value,
    build_evaluation_prompt,
)
from home_finder.models import (
    BathroomAnalysis,
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    ListingRedFlags,
    MergedProperty,
    OutdoorSpaceAnalysis,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
    StorageAnalysis,
    ValueAnalysis,
    ViewingNotes,
)
from home_finder.utils.image_cache import is_valid_image_url


class TestKitchenAnalysis:
    """Tests for KitchenAnalysis model."""

    def test_valid_full_analysis(self) -> None:
        """Should create analysis with all fields."""
        analysis = KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher="yes",
            has_washing_machine="yes",
            notes="Modern kitchen with integrated appliances",
        )
        assert analysis.overall_quality == "modern"
        assert analysis.hob_type == "gas"

    def test_minimal_analysis(self) -> None:
        """Should create analysis with only defaults."""
        analysis = KitchenAnalysis()
        assert analysis.overall_quality == "unknown"
        assert analysis.notes == ""

    def test_invalid_kitchen_quality(self) -> None:
        """Should reject invalid kitchen quality."""
        with pytest.raises(ValidationError):
            KitchenAnalysis(overall_quality="excellent")  # type: ignore[arg-type]


class TestToolSchema:
    """Tests for VISUAL_ANALYSIS_TOOL and EVALUATION_TOOL schema structure."""

    def test_uses_plain_types_for_boolean_and_enum_fields(self) -> None:
        """Strict mode: boolean and enum fields use plain types (no anyOf)."""
        visual_schema = VISUAL_ANALYSIS_TOOL["input_schema"]["properties"]

        # Kitchen hob_type should be plain enum (no anyOf)
        hob_type = visual_schema["kitchen"]["properties"]["hob_type"]
        assert "anyOf" not in hob_type
        assert hob_type["type"] == "string"
        assert "unknown" in hob_type["enum"]

        # Kitchen has_dishwasher should be plain enum (tri-state like has_washing_machine)
        has_dishwasher = visual_schema["kitchen"]["properties"]["has_dishwasher"]
        assert "anyOf" not in has_dishwasher
        assert has_dishwasher["type"] == "string"
        assert has_dishwasher["enum"] == ["yes", "no", "unknown"]

        # Light space window_sizes should be plain enum with "unknown" sentinel
        window_sizes = visual_schema["light_space"]["properties"]["window_sizes"]
        assert "anyOf" not in window_sizes
        assert "unknown" in window_sizes["enum"]

        # Concern severity should be plain enum with "none" sentinel
        concern_severity = visual_schema["concern_severity"]
        assert "anyOf" not in concern_severity
        assert "none" in concern_severity["enum"]

    def test_tristate_fields_use_string_enum(self) -> None:
        """High-impact fields should use string enum yes/no/unknown."""
        visual_schema = VISUAL_ANALYSIS_TOOL["input_schema"]["properties"]
        eval_schema = EVALUATION_TOOL["input_schema"]["properties"]
        expected_enum = ["yes", "no", "unknown"]

        # condition tri-state fields (Phase 1)
        condition_props = visual_schema["condition"]["properties"]
        assert condition_props["has_visible_damp"]["type"] == "string"
        assert condition_props["has_visible_damp"]["enum"] == expected_enum
        assert condition_props["has_visible_mold"]["type"] == "string"
        assert condition_props["has_visible_mold"]["enum"] == expected_enum
        assert condition_props["has_worn_fixtures"]["type"] == "string"
        assert condition_props["has_worn_fixtures"]["enum"] == expected_enum

        # bathroom.has_bathtub (Phase 1)
        bathroom_props = visual_schema["bathroom"]["properties"]
        assert bathroom_props["has_bathtub"]["type"] == "string"
        assert bathroom_props["has_bathtub"]["enum"] == expected_enum

        # bedroom.has_built_in_wardrobe (Phase 1)
        bedroom_props = visual_schema["bedroom"]["properties"]
        assert bedroom_props["has_built_in_wardrobe"]["type"] == "string"
        assert bedroom_props["has_built_in_wardrobe"]["enum"] == expected_enum

        # storage tri-state fields (Phase 1)
        storage_props = visual_schema["storage"]["properties"]
        assert storage_props["has_built_in_wardrobes"]["type"] == "string"
        assert storage_props["has_built_in_wardrobes"]["enum"] == expected_enum
        assert storage_props["has_hallway_cupboard"]["type"] == "string"
        assert storage_props["has_hallway_cupboard"]["enum"] == expected_enum

        # flooring_noise.has_double_glazing (Phase 1)
        fn_props = visual_schema["flooring_noise"]["properties"]
        assert fn_props["has_double_glazing"]["type"] == "string"
        assert fn_props["has_double_glazing"]["enum"] == expected_enum

        # listing_extraction.bills_included and pets_allowed (Phase 2)
        le_props = eval_schema["listing_extraction"]["properties"]
        assert le_props["bills_included"]["type"] == "string"
        assert le_props["bills_included"]["enum"] == expected_enum
        assert le_props["pets_allowed"]["type"] == "string"
        assert le_props["pets_allowed"]["enum"] == expected_enum

        # kitchen.has_washing_machine (Phase 1)
        kitchen_props = visual_schema["kitchen"]["properties"]
        assert kitchen_props["has_washing_machine"]["type"] == "string"
        assert kitchen_props["has_washing_machine"]["enum"] == expected_enum

        # bathroom.is_ensuite (Phase 1)
        bath_props = visual_schema["bathroom"]["properties"]
        assert bath_props["is_ensuite"]["type"] == "string"
        assert bath_props["is_ensuite"]["enum"] == expected_enum

        # bedroom.primary_is_double and can_fit_desk (Phase 1)
        bed_props = visual_schema["bedroom"]["properties"]
        assert bed_props["primary_is_double"]["type"] == "string"
        assert bed_props["primary_is_double"]["enum"] == expected_enum
        assert bed_props["can_fit_desk"]["type"] == "string"
        assert bed_props["can_fit_desk"]["enum"] == expected_enum

    def test_keeps_anyof_for_nullable_numeric_fields(self) -> None:
        """Strict mode: only numeric fields retain anyOf for null."""
        visual_schema = VISUAL_ANALYSIS_TOOL["input_schema"]["properties"]
        eval_schema = EVALUATION_TOOL["input_schema"]["properties"]

        # Space living_room_sqm should still use anyOf (numeric) — Phase 1
        living_room_sqm = visual_schema["space"]["properties"]["living_room_sqm"]
        assert "anyOf" in living_room_sqm
        assert {"type": "number"} in living_room_sqm["anyOf"]
        assert {"type": "null"} in living_room_sqm["anyOf"]

        # Space is_spacious_enough should now be plain boolean
        is_spacious = visual_schema["space"]["properties"]["is_spacious_enough"]
        assert is_spacious["type"] == "boolean"

        # listing_extraction.service_charge_pcm should use anyOf — Phase 2
        service_charge = eval_schema["listing_extraction"]["properties"]["service_charge_pcm"]
        assert "anyOf" in service_charge

    def test_strict_mode_on_tools(self) -> None:
        """Phase 1 is non-strict (too large for grammar); Phase 2 is strict."""
        assert "strict" not in VISUAL_ANALYSIS_TOOL
        assert EVALUATION_TOOL.get("strict") is True

    def test_visual_tool_does_not_contain_evaluation_fields(self) -> None:
        """Visual analysis tool should not contain Phase 2 fields."""
        visual_props = VISUAL_ANALYSIS_TOOL["input_schema"]["properties"]
        visual_required = VISUAL_ANALYSIS_TOOL["input_schema"]["required"]

        assert "listing_extraction" not in visual_props
        assert "viewing_notes" not in visual_props
        assert "highlights" not in visual_props
        assert "lowlights" not in visual_props
        assert "one_line" not in visual_props
        assert "value_for_quality" not in visual_props

        assert "listing_extraction" not in visual_required
        assert "value_for_quality" not in visual_required

    def test_evaluation_tool_does_not_contain_visual_fields(self) -> None:
        """Evaluation tool should not contain Phase 1 fields."""
        eval_props = EVALUATION_TOOL["input_schema"]["properties"]

        assert "kitchen" not in eval_props
        assert "condition" not in eval_props
        assert "light_space" not in eval_props
        assert "space" not in eval_props
        assert "bathroom" not in eval_props
        assert "overall_rating" not in eval_props


class TestConditionAnalysis:
    """Tests for ConditionAnalysis model."""

    def test_valid_analysis_with_concerns(self) -> None:
        """Should create analysis with condition concerns."""
        analysis = ConditionAnalysis(
            overall_condition="fair",
            has_visible_damp="yes",
            has_visible_mold="no",
            has_worn_fixtures="yes",
            maintenance_concerns=["Damp near window", "Dated bathroom"],
            confidence="high",
        )
        assert analysis.overall_condition == "fair"
        assert analysis.has_visible_damp == "yes"
        assert len(analysis.maintenance_concerns) == 2

    def test_minimal_analysis(self) -> None:
        """Should create analysis with defaults."""
        analysis = ConditionAnalysis(overall_condition="good")
        assert analysis.has_visible_damp == "unknown"
        assert analysis.maintenance_concerns == []
        assert analysis.confidence == "medium"

    def test_invalid_condition(self) -> None:
        """Should reject invalid condition values."""
        with pytest.raises(ValidationError):
            ConditionAnalysis(overall_condition="amazing")  # type: ignore[arg-type]


class TestLightSpaceAnalysis:
    """Tests for LightSpaceAnalysis model."""

    def test_valid_full_analysis(self) -> None:
        """Should create analysis with all fields."""
        analysis = LightSpaceAnalysis(
            natural_light="excellent",
            window_sizes="large",
            feels_spacious=True,
            ceiling_height="high",
            notes="South-facing with floor-to-ceiling windows",
        )
        assert analysis.natural_light == "excellent"
        assert analysis.feels_spacious is True

    def test_minimal_analysis(self) -> None:
        """Should create analysis with required fields only."""
        analysis = LightSpaceAnalysis(
            natural_light="fair",
            feels_spacious=False,
        )
        assert analysis.window_sizes is None
        assert analysis.ceiling_height is None


class TestSpaceAnalysis:
    """Tests for SpaceAnalysis model."""

    def test_valid_analysis_with_sqm(self) -> None:
        """Should create analysis with square meters."""
        analysis = SpaceAnalysis(
            living_room_sqm=25.5,
            is_spacious_enough=True,
            confidence="high",
        )
        assert analysis.living_room_sqm == 25.5
        assert analysis.is_spacious_enough is True

    def test_analysis_without_sqm(self) -> None:
        """Should create analysis without square meters."""
        analysis = SpaceAnalysis(
            is_spacious_enough=False,
            confidence="low",
        )
        assert analysis.living_room_sqm is None


class TestValueAnalysis:
    """Tests for ValueAnalysis model and assess_value function."""

    def test_value_analysis_model(self) -> None:
        """Should create ValueAnalysis with all fields."""
        value = ValueAnalysis(
            area_average=1900,
            difference=-100,
            rating="good",
            note="£100 below E8 average",
            quality_adjusted_rating="excellent",
            quality_adjusted_note="Great condition justifies price",
        )
        assert value.area_average == 1900
        assert value.quality_adjusted_rating == "excellent"

    def test_excellent_value_below_average(self) -> None:
        """Property well below average should be excellent value."""
        value = assess_value(price_pcm=1600, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "excellent"
        assert value.difference is not None and value.difference < 0
        assert "below" in value.note.lower()

    def test_good_value_at_average(self) -> None:
        """Property at average should be good value."""
        value = assess_value(price_pcm=1900, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "good"

    def test_fair_value_slightly_above(self) -> None:
        """Property slightly above average should be fair value."""
        value = assess_value(price_pcm=2050, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "fair"
        assert "above" in value.note.lower()

    def test_poor_value_well_above(self) -> None:
        """Property well above average should be poor value."""
        value = assess_value(price_pcm=2300, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "poor"

    def test_handles_missing_postcode(self) -> None:
        """Should handle missing postcode gracefully."""
        value = assess_value(price_pcm=1800, postcode=None, bedrooms=1)
        assert value.rating is None
        assert "cannot assess" in value.note.lower()

    def test_uses_default_for_unknown_area(self) -> None:
        """Should use default benchmark for unknown areas."""
        # W1 is not in our benchmarks, should use default
        value = assess_value(price_pcm=1800, postcode="W1A 1AA", bedrooms=1)
        assert value.rating is not None  # Should still produce a rating

    def test_different_bedroom_benchmarks(self) -> None:
        """Different bedroom counts should use different benchmarks."""
        value_1bed = assess_value(price_pcm=2000, postcode="E8 2LX", bedrooms=1)
        value_2bed = assess_value(price_pcm=2000, postcode="E8 2LX", bedrooms=2)

        # 2000 is above E8 1-bed average but below 2-bed average
        assert value_1bed.rating in ["fair", "poor"]
        assert value_2bed.rating in ["excellent", "good"]


class TestPropertyQualityAnalysis:
    """Tests for PropertyQualityAnalysis model."""

    def test_valid_full_analysis(self) -> None:
        """Should create complete quality analysis."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas"),
            condition=ConditionAnalysis(
                overall_condition="good",
                has_visible_damp="no",
                maintenance_concerns=[],
            ),
            light_space=LightSpaceAnalysis(
                natural_light="good",
                feels_spacious=True,
            ),
            space=SpaceAnalysis(
                living_room_sqm=22.0,
                is_spacious_enough=True,
                confidence="high",
            ),
            condition_concerns=False,
            summary="Well-maintained flat with good natural light",
        )
        assert analysis.condition_concerns is False
        assert "Well-maintained" in analysis.summary

    def test_analysis_with_concerns(self) -> None:
        """Should create analysis with condition concerns flagged."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(
                overall_condition="poor",
                has_visible_damp="yes",
                has_visible_mold="yes",
                maintenance_concerns=["Significant damp", "Mold in bathroom"],
            ),
            light_space=LightSpaceAnalysis(
                natural_light="fair",
                feels_spacious=False,
            ),
            space=SpaceAnalysis(is_spacious_enough=False),
            condition_concerns=True,
            concern_severity="serious",
            summary="Property has significant damp and mold issues",
        )
        assert analysis.condition_concerns is True
        assert analysis.concern_severity == "serious"

    def test_model_is_frozen(self) -> None:
        """Should be immutable."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(overall_condition="good"),
            light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
            space=SpaceAnalysis(is_spacious_enough=True),
            summary="Test",
        )
        with pytest.raises(ValidationError):
            analysis.summary = "Changed"


@pytest.fixture
def sample_property() -> Property:
    """Sample property for testing."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="123456789",
        url=HttpUrl("https://www.rightmove.co.uk/properties/123456789"),
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


@pytest.fixture
def one_bed_property() -> Property:
    """Sample 1-bed property for testing."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="999",
        url=HttpUrl("https://www.rightmove.co.uk/properties/999"),
        title="1 bed flat",
        price_pcm=1800,
        bedrooms=1,
        address="456 Test Street, London",
    )


@pytest.fixture
def sample_merged_property(sample_property: Property) -> MergedProperty:
    """Pre-enriched merged property with images and floorplan."""
    return MergedProperty(
        canonical=sample_property,
        sources=(sample_property.source,),
        source_urls={sample_property.source: sample_property.url},
        images=(
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=sample_property.source,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img2.jpg"),
                source=sample_property.source,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img3.jpg"),
                source=sample_property.source,
                image_type="gallery",
            ),
        ),
        floorplan=PropertyImage(
            url=HttpUrl("https://example.com/floor.jpg"),
            source=sample_property.source,
            image_type="floorplan",
        ),
        min_price=sample_property.price_pcm,
        max_price=sample_property.price_pcm,
    )


@pytest.fixture
def one_bed_merged_property(one_bed_property: Property) -> MergedProperty:
    """Pre-enriched 1-bed merged property."""
    return MergedProperty(
        canonical=one_bed_property,
        sources=(one_bed_property.source,),
        source_urls={one_bed_property.source: one_bed_property.url},
        images=(
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=one_bed_property.source,
                image_type="gallery",
            ),
        ),
        floorplan=PropertyImage(
            url=HttpUrl("https://example.com/floor.jpg"),
            source=one_bed_property.source,
            image_type="floorplan",
        ),
        min_price=one_bed_property.price_pcm,
        max_price=one_bed_property.price_pcm,
    )


@pytest.fixture
def sample_visual_response() -> dict[str, Any]:
    """Sample Phase 1 visual analysis response from Claude."""
    return {
        "kitchen": {
            "overall_quality": "modern",
            "hob_type": "gas",
            "has_dishwasher": "yes",
            "has_washing_machine": "yes",
            "notes": "Modern integrated kitchen",
        },
        "condition": {
            "overall_condition": "good",
            "has_visible_damp": "no",
            "has_visible_mold": "no",
            "has_worn_fixtures": "no",
            "maintenance_concerns": [],
            "confidence": "high",
        },
        "light_space": {
            "natural_light": "excellent",
            "window_sizes": "large",
            "feels_spacious": True,
            "ceiling_height": "standard",
            "notes": "South-facing with good light",
        },
        "space": {
            "living_room_sqm": 22,
            "is_spacious_enough": True,
            "confidence": "high",
            "hosting_layout": "good",
        },
        "bathroom": {
            "overall_condition": "modern",
            "has_bathtub": "yes",
            "shower_type": "overhead",
            "is_ensuite": "no",
            "notes": "Clean and modern",
        },
        "bedroom": {
            "primary_is_double": "yes",
            "has_built_in_wardrobe": "yes",
            "can_fit_desk": "yes",
            "office_separation": "dedicated_room",
            "notes": "Good-sized double bedroom",
        },
        "outdoor_space": {
            "has_balcony": False,
            "has_garden": False,
            "has_terrace": False,
            "has_shared_garden": True,
            "notes": "Shared communal garden",
        },
        "storage": {
            "has_built_in_wardrobes": "yes",
            "has_hallway_cupboard": "no",
            "storage_rating": "adequate",
        },
        "flooring_noise": {
            "primary_flooring": "hardwood",
            "has_double_glazing": "yes",
            "noise_indicators": [],
            "hosting_noise_risk": "moderate",
            "notes": "Quiet street",
        },
        "listing_red_flags": {
            "missing_room_photos": [],
            "too_few_photos": False,
            "selective_angles": False,
            "description_concerns": [],
            "red_flag_count": 0,
        },
        "overall_rating": 4,
        "condition_concerns": False,
        "concern_severity": "none",
        "summary": "Well-maintained flat with modern kitchen. Living room suits home office.",
    }


@pytest.fixture
def sample_evaluation_response() -> dict[str, Any]:
    """Sample Phase 2 evaluation response from Claude."""
    return {
        "listing_extraction": {
            "epc_rating": "C",
            "service_charge_pcm": None,
            "deposit_weeks": 5,
            "bills_included": "no",
            "pets_allowed": "unknown",
            "parking": "street",
            "council_tax_band": "C",
            "property_type": "victorian",
            "furnished_status": "furnished",
            "broadband_type": "fttc",
        },
        "value_for_quality": {
            "rating": "good",
            "reasoning": "Well-maintained property at reasonable price",
        },
        "viewing_notes": {
            "check_items": ["Check water pressure", "Inspect windows"],
            "questions_for_agent": ["Any upcoming rent increases?"],
            "deal_breaker_tests": ["Test hot water"],
        },
        "highlights": ["Gas hob", "Modern kitchen", "Good light"],
        "lowlights": ["No balcony"],
        "one_line": "Well-maintained flat with modern kitchen and good natural light",
    }


@pytest.fixture
def sample_visual_response_with_nulls() -> dict[str, Any]:
    """Sample Phase 1 response with nullable fields set to null."""
    return {
        "kitchen": {
            "overall_quality": "unknown",
            "hob_type": None,
            "has_dishwasher": "unknown",
            "has_washing_machine": "unknown",
            "notes": "Kitchen not visible in images",
        },
        "condition": {
            "overall_condition": "unknown",
            "has_visible_damp": "unknown",
            "has_visible_mold": "unknown",
            "has_worn_fixtures": "unknown",
            "maintenance_concerns": [],
            "confidence": "low",
        },
        "light_space": {
            "natural_light": "unknown",
            "window_sizes": None,
            "feels_spacious": None,
            "ceiling_height": None,
            "notes": "Limited photos available",
        },
        "space": {
            "living_room_sqm": None,
            "is_spacious_enough": None,
            "confidence": "low",
        },
        "overall_rating": 3,
        "condition_concerns": False,
        "concern_severity": None,
        "summary": "Limited visibility - cannot fully assess property condition.",
    }


@pytest.fixture
def sample_evaluation_response_with_nulls() -> dict[str, Any]:
    """Sample Phase 2 response with minimal data."""
    return {
        "listing_extraction": None,
        "value_for_quality": {
            "rating": "fair",
            "reasoning": "Cannot assess quality from available images",
        },
        "viewing_notes": None,
        "highlights": [],
        "lowlights": [],
        "one_line": "Property with limited visibility for assessment",
    }


def create_mock_response(
    tool_input: dict[str, Any],
    stop_reason: str = "tool_use",
    tool_name: str = "property_visual_analysis",
) -> MagicMock:
    """Create a mock API response with tool use block."""
    tool_block = ToolUseBlock(
        id="toolu_123",
        type="tool_use",
        name=tool_name,
        input=tool_input,
    )
    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.stop_reason = stop_reason
    mock_response.usage = MagicMock()
    mock_response.usage.cache_read_input_tokens = 0
    mock_response.usage.cache_creation_input_tokens = 0
    return mock_response


def _make_two_phase_mock(
    visual_response: dict[str, Any],
    eval_response: dict[str, Any],
) -> AsyncMock:
    """Create an AsyncMock that returns Phase 1 then Phase 2 responses."""
    mock_visual = create_mock_response(visual_response, tool_name="property_visual_analysis")
    mock_eval = create_mock_response(eval_response, tool_name="property_evaluation")
    return AsyncMock(side_effect=[mock_visual, mock_eval])


class TestPropertyQualityFilter:
    """Tests for PropertyQualityFilter."""

    def test_is_valid_image_url_accepts_images(self) -> None:
        """Should accept valid image URLs."""
        valid_urls = [
            "https://example.com/image.jpg",
            "https://example.com/image.jpeg",
            "https://example.com/image.png",
            "https://example.com/image.gif",
            "https://example.com/image.webp",
            "https://example.com/image.JPG",  # Case insensitive
            "https://example.com/image.jpg?w=800",  # With query params
        ]
        for url in valid_urls:
            assert is_valid_image_url(url), f"Should accept {url}"

    def test_is_valid_image_url_rejects_pdfs(self) -> None:
        """Should reject PDF URLs (not supported by Claude Vision API)."""
        pdf_urls = [
            "https://lc.zoocdn.com/abc123.pdf",
            "https://example.com/floorplan.PDF",
            "https://example.com/doc.pdf?download=true",
        ]
        for url in pdf_urls:
            assert not is_valid_image_url(url), f"Should reject {url}"

    async def test_creates_minimal_analysis_when_no_images(self, sample_property: Property) -> None:
        """Should create minimal analysis when no images available."""
        # Merged property with no images and no floorplan
        merged = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=(),
            floorplan=None,
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        results = await quality_filter.analyze_merged_properties([merged])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary
        assert analysis.space.confidence == "low"

    async def test_analyzes_property_with_images(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should analyze property with gallery images using two-phase structured outputs."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"
        assert analysis.condition.overall_condition == "good"
        assert analysis.light_space.natural_light == "excellent"
        assert analysis.space.living_room_sqm == 22

    async def test_unwraps_one_line_wrapped_in_object(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Pydantic validator unwraps one_line stored as dict (DB backward compat)."""
        # Simulate old DB data where one_line was stored as {"one_line": "text"}
        sample_evaluation_response["one_line"] = {"one_line": "Bright flat with balcony"}

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        _, analysis = results[0]
        assert analysis.one_line == "Bright flat with balcony"

    async def test_overrides_space_for_two_plus_beds(
        self,
        sample_merged_property: MergedProperty,
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should override space assessment for 2+ bedroom properties."""
        # Response says not spacious enough, but 2-bed should override
        visual_response = {
            "kitchen": {"notes": ""},
            "condition": {
                "overall_condition": "good",
                "has_visible_damp": "no",
                "has_visible_mold": "no",
                "has_worn_fixtures": "no",
                "maintenance_concerns": [],
                "confidence": "high",
            },
            "light_space": {"natural_light": "good", "feels_spacious": True, "notes": ""},
            "space": {"living_room_sqm": 15, "is_spacious_enough": False, "confidence": "high"},
            "overall_rating": 3,
            "condition_concerns": False,
            "concern_severity": "none",
            "summary": "Compact living room",
        }

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            visual_response, sample_evaluation_response
        )

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        _, analysis = results[0]
        # Should be overridden because property has 2 bedrooms
        assert analysis.space.is_spacious_enough is True
        assert analysis.space.confidence == "high"

    async def test_does_not_override_space_for_one_bed(
        self,
        one_bed_merged_property: MergedProperty,
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should NOT override space assessment for 1-bed properties."""
        visual_response = {
            "kitchen": {"notes": ""},
            "condition": {
                "overall_condition": "good",
                "has_visible_damp": "no",
                "has_visible_mold": "no",
                "has_worn_fixtures": "no",
                "maintenance_concerns": [],
                "confidence": "high",
            },
            "light_space": {"natural_light": "good", "feels_spacious": True, "notes": ""},
            "space": {"living_room_sqm": 15, "is_spacious_enough": False, "confidence": "high"},
            "overall_rating": 3,
            "condition_concerns": False,
            "concern_severity": "none",
            "summary": "Compact living room",
        }

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            visual_response, sample_evaluation_response
        )

        results = await quality_filter.analyze_merged_properties([one_bed_merged_property])

        _, analysis = results[0]
        # Should keep original assessment
        assert analysis.space.is_spacious_enough is False

    async def test_handles_llm_failure_gracefully(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Should return minimal analysis on Phase 1 LLM failure."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(side_effect=Exception("API error"))

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary

    async def test_handles_unexpected_stop_reason(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Should return minimal analysis on unexpected stop reason (e.g., max_tokens)."""
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.stop_reason = "max_tokens"  # Unexpected stop reason

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary

    async def test_includes_all_images_in_phase1_call(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should include gallery images and floorplan in Phase 1 API call."""
        quality_filter = PropertyQualityFilter(api_key="test-key", max_images=10)
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([sample_merged_property])

        # Check the Phase 1 API call (first call)
        call_args = quality_filter._client.messages.create.call_args_list[0]
        content = call_args.kwargs["messages"][0]["content"]

        # 3 gallery x (label + image) + 1 floorplan x (label + image) + 1 text = 9
        assert len(content) == 9
        assert content[0]["type"] == "text"  # "Gallery image 1:"
        assert content[1]["type"] == "image"
        assert content[6]["type"] == "text"  # "Floorplan:"
        assert content[7]["type"] == "image"  # floorplan
        assert content[8]["type"] == "text"  # user prompt

    async def test_respects_max_images_limit(
        self,
        sample_property: Property,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should respect max_images configuration."""
        # Create merged property with 20 gallery images
        many_images_merged = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=tuple(
                PropertyImage(
                    url=HttpUrl(f"https://example.com/img{i}.jpg"),
                    source=sample_property.source,
                    image_type="gallery",
                )
                for i in range(20)
            ),
            floorplan=PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=sample_property.source,
                image_type="floorplan",
            ),
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )

        quality_filter = PropertyQualityFilter(api_key="test-key", max_images=5)
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([many_images_merged])

        call_args = quality_filter._client.messages.create.call_args_list[0]
        content = call_args.kwargs["messages"][0]["content"]

        # With floorplan present, gallery is capped to max_images-1=4
        # So: 4 gallery + 1 floorplan = 5 total images (stays within max_images)
        image_blocks = [c for c in content if c.get("type") == "image"]
        assert len(image_blocks) == 5  # 4 gallery + 1 floorplan
        label_blocks = [
            c
            for c in content
            if (c.get("type") == "text" and "image" in c.get("text", "").lower())
            or "Floorplan" in c.get("text", "")
        ]
        assert len(label_blocks) >= 4  # at least 4 gallery labels

    async def test_uses_tool_choice_for_structured_output(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Phase 1 uses auto tool_choice with thinking; Phase 2 uses forced tool."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([sample_merged_property])

        calls = quality_filter._client.messages.create.call_args_list
        assert len(calls) == 2

        # Phase 1: auto tool_choice with extended thinking
        phase1_kwargs = calls[0].kwargs
        assert phase1_kwargs["tool_choice"] == {"type": "auto"}
        assert phase1_kwargs["thinking"] == {
            "type": "enabled",
            "budget_tokens": 10000,
        }
        assert len(phase1_kwargs["tools"]) == 1
        assert phase1_kwargs["tools"][0]["name"] == "property_visual_analysis"

        # Phase 2: forced tool choice, no extended thinking
        phase2_kwargs = calls[1].kwargs
        assert phase2_kwargs["tool_choice"] == {
            "type": "tool",
            "name": "property_evaluation",
        }
        assert "thinking" not in phase2_kwargs
        assert len(phase2_kwargs["tools"]) == 1
        assert phase2_kwargs["tools"][0]["name"] == "property_evaluation"

    async def test_uses_cached_system_prompt(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should use system prompt with cache_control for cost savings."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([sample_merged_property])

        calls = quality_filter._client.messages.create.call_args_list

        # Phase 1 system prompt
        system1 = calls[0].kwargs["system"]
        assert len(system1) == 1
        assert system1[0]["type"] == "text"
        assert system1[0]["cache_control"] == {"type": "ephemeral"}
        assert "expert London rental property analyst" in system1[0]["text"]

        # Phase 2 system prompt
        system2 = calls[1].kwargs["system"]
        assert len(system2) == 1
        assert system2[0]["type"] == "text"
        assert system2[0]["cache_control"] == {"type": "ephemeral"}
        assert "expert London rental property evaluator" in system2[0]["text"]

    async def test_extracts_value_for_quality_from_tool_response(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should extract quality-adjusted value rating from Phase 2 response."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        _, analysis = results[0]
        # value.quality_adjusted_rating comes from Phase 2
        assert analysis.value is not None
        assert analysis.value.quality_adjusted_rating == "good"
        assert "Well-maintained" in analysis.value.quality_adjusted_note

    async def test_handles_end_turn_with_tool_use(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should handle end_turn stop_reason when tool_use block is present."""
        mock_visual = create_mock_response(
            sample_visual_response, stop_reason="end_turn", tool_name="property_visual_analysis"
        )
        mock_eval = create_mock_response(
            sample_evaluation_response, tool_name="property_evaluation"
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(side_effect=[mock_visual, mock_eval])

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"

    async def test_includes_description_in_prompt(
        self,
        sample_property: Property,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should include listing description in the user prompt."""
        # Create merged property with description populated
        merged = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=(
                PropertyImage(
                    url=HttpUrl("https://example.com/img1.jpg"),
                    source=sample_property.source,
                    image_type="gallery",
                ),
            ),
            floorplan=PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=sample_property.source,
                image_type="floorplan",
            ),
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
            descriptions={
                PropertySource.RIGHTMOVE: "Spacious flat with modern kitchen and gas hob."
            },
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([merged])

        call_args = quality_filter._client.messages.create.call_args_list[0]
        content = call_args.kwargs["messages"][0]["content"]

        # Find the text block with the user prompt (last text block, after image labels)
        text_blocks = [c for c in content if c.get("type") == "text"]
        prompt_text = text_blocks[-1]["text"]  # User prompt is last

        # Verify description is included (XML format)
        assert "<listing_description>" in prompt_text
        assert "gas hob" in prompt_text

    async def test_handles_nullable_fields_in_response(
        self,
        one_bed_merged_property: MergedProperty,
        sample_visual_response_with_nulls: dict[str, Any],
        sample_evaluation_response_with_nulls: dict[str, Any],
    ) -> None:
        """Should handle null values for optional fields in tool response."""
        # Use 1-bed property to avoid space override for 2+ beds
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response_with_nulls, sample_evaluation_response_with_nulls
        )

        results = await quality_filter.analyze_merged_properties([one_bed_merged_property])

        assert len(results) == 1
        _, analysis = results[0]

        # Verify null values are handled correctly
        assert analysis.kitchen.overall_quality == "unknown"
        assert analysis.kitchen.hob_type is None
        assert analysis.kitchen.has_dishwasher == "unknown"
        assert analysis.kitchen.has_washing_machine == "unknown"

        assert analysis.light_space.window_sizes == "unknown"
        assert analysis.light_space.feels_spacious is None
        assert analysis.light_space.ceiling_height == "unknown"

        assert analysis.space.living_room_sqm is None
        assert analysis.space.is_spacious_enough is None
        assert analysis.space.confidence == "low"

        assert analysis.concern_severity == "none"

    async def test_phase1_output_passed_to_phase2(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Phase 1 JSON output should appear in Phase 2 prompt."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([sample_merged_property])

        calls = quality_filter._client.messages.create.call_args_list
        assert len(calls) == 2

        # Phase 2 prompt should contain Phase 1 output in <visual_analysis> tags
        phase2_content = calls[1].kwargs["messages"][0]["content"]
        assert "<visual_analysis>" in phase2_content
        assert '"overall_quality": "modern"' in phase2_content
        assert "</visual_analysis>" in phase2_content

    async def test_phase2_failure_returns_partial_analysis(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
    ) -> None:
        """Phase 2 failure should return partial analysis with visual data only."""
        mock_visual = create_mock_response(
            sample_visual_response, tool_name="property_visual_analysis"
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        # Phase 1 succeeds, Phase 2 raises
        quality_filter._client.messages.create = AsyncMock(
            side_effect=[mock_visual, Exception("Phase 2 API error")]
        )

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        assert len(results) == 1
        _, analysis = results[0]
        # Visual data should be present
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.condition.overall_condition == "good"
        assert analysis.summary == (
            "Well-maintained flat with modern kitchen. Living room suits home office."
        )
        # Evaluation data should be absent/default
        assert analysis.listing_extraction is None
        assert analysis.viewing_notes is None
        assert analysis.highlights is None
        assert analysis.lowlights is None
        assert analysis.one_line is None

    async def test_phase2_no_images_in_call(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Phase 2 should be text-only (no images)."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([sample_merged_property])

        calls = quality_filter._client.messages.create.call_args_list

        # Phase 2 content is a string (text-only), not a list of content blocks
        phase2_content = calls[1].kwargs["messages"][0]["content"]
        assert isinstance(phase2_content, str)


class TestFloorplanNoteWiring:
    """Tests that has_labeled_floorplan flows from _analyze_property to the prompt."""

    async def test_no_floorplan_includes_note_in_api_call(
        self,
        sample_property: Property,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Property with no floorplan → Phase 1 prompt contains <floorplan_note>."""
        merged = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=(
                PropertyImage(
                    url=HttpUrl("https://example.com/img1.jpg"),
                    source=sample_property.source,
                    image_type="gallery",
                ),
            ),
            floorplan=None,
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([merged])

        # Inspect the Phase 1 API call's user prompt (last text block)
        call_args = quality_filter._client.messages.create.call_args_list[0]
        content = call_args.kwargs["messages"][0]["content"]
        text_blocks = [c for c in content if c.get("type") == "text"]
        prompt_text = text_blocks[-1]["text"]

        assert "<floorplan_note>" in prompt_text
        assert "floorplan_detected_in_gallery" in prompt_text

    async def test_with_floorplan_excludes_note_in_api_call(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Property with floorplan → Phase 1 prompt does NOT contain <floorplan_note>."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_merged_properties([sample_merged_property])

        call_args = quality_filter._client.messages.create.call_args_list[0]
        content = call_args.kwargs["messages"][0]["content"]
        text_blocks = [c for c in content if c.get("type") == "text"]
        prompt_text = text_blocks[-1]["text"]

        assert "<floorplan_note>" not in prompt_text


class TestBuildEvaluationPrompt:
    """Tests for the build_evaluation_prompt function."""

    def test_includes_visual_data(self) -> None:
        """Should include Phase 1 visual data in XML tags."""
        visual_data = {"kitchen": {"overall_quality": "modern"}}
        prompt = build_evaluation_prompt(
            visual_data=visual_data,
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
        )
        assert "<visual_analysis>" in prompt
        assert '"overall_quality": "modern"' in prompt
        assert "</visual_analysis>" in prompt

    def test_includes_property_context(self) -> None:
        """Should include price and bedroom context."""
        prompt = build_evaluation_prompt(
            visual_data={},
            price_pcm=1800,
            bedrooms=2,
            area_average=1900,
        )
        assert "£1,800/month" in prompt
        assert "Bedrooms: 2" in prompt
        assert "£1,900/month" in prompt

    def test_includes_description(self) -> None:
        """Should include listing description."""
        prompt = build_evaluation_prompt(
            visual_data={},
            description="Lovely flat with garden",
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
        )
        assert "<listing_description>" in prompt
        assert "Lovely flat with garden" in prompt

    def test_includes_area_context(self) -> None:
        """Should include area context when outcode provided."""
        prompt = build_evaluation_prompt(
            visual_data={},
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
            area_context="Trendy East London area",
            outcode="E8",
        )
        assert '<area_context outcode="E8">' in prompt
        assert "Trendy East London area" in prompt

    def test_ends_with_tool_instruction(self) -> None:
        """Should end with instruction to use evaluation tool."""
        prompt = build_evaluation_prompt(
            visual_data={},
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
        )
        assert "property_evaluation tool" in prompt


class TestAnalyzeSingleMerged:
    """Tests for analyze_single_merged method."""

    async def test_with_images_returns_analysis(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Should return (merged, analysis) when images are present."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        merged, analysis = await quality_filter.analyze_single_merged(sample_merged_property)

        assert merged is sample_merged_property
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.condition.overall_condition == "good"
        assert analysis.summary == (
            "Well-maintained flat with modern kitchen. Living room suits home office."
        )

    async def test_without_images_returns_minimal(
        self,
        sample_property: Property,
    ) -> None:
        """Should return minimal analysis when no images available."""
        merged = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=(),
            floorplan=None,
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        result_merged, analysis = await quality_filter.analyze_single_merged(merged)

        assert result_merged is merged
        assert "No images available" in analysis.summary
        assert analysis.space.confidence == "low"

    async def test_api_failure_returns_minimal(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Should return minimal analysis on API failure (no raise)."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(side_effect=Exception("API error"))

        merged, analysis = await quality_filter.analyze_single_merged(sample_merged_property)

        assert merged is sample_merged_property
        assert "No images available" in analysis.summary
        assert analysis.space.confidence == "low"


class TestBackwardCompatValidators:
    """Test that bool/None values are coerced to tri-state strings."""

    def test_condition_damp_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for damp/mold."""
        assert ConditionAnalysis(has_visible_damp=True).has_visible_damp == "yes"  # type: ignore[arg-type]
        assert ConditionAnalysis(has_visible_damp=False).has_visible_damp == "no"  # type: ignore[arg-type]
        assert ConditionAnalysis(has_visible_damp=None).has_visible_damp == "unknown"  # type: ignore[arg-type]
        assert ConditionAnalysis(has_visible_mold=True).has_visible_mold == "yes"  # type: ignore[arg-type]
        assert ConditionAnalysis(has_visible_mold=False).has_visible_mold == "no"  # type: ignore[arg-type]
        assert ConditionAnalysis(has_visible_mold=None).has_visible_mold == "unknown"  # type: ignore[arg-type]

    def test_condition_string_passthrough(self) -> None:
        """String values should pass through unchanged."""
        assert ConditionAnalysis(has_visible_damp="yes").has_visible_damp == "yes"
        assert ConditionAnalysis(has_visible_damp="no").has_visible_damp == "no"
        assert ConditionAnalysis(has_visible_damp="unknown").has_visible_damp == "unknown"

    def test_flooring_glazing_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for double glazing."""
        from home_finder.models import FlooringNoiseAnalysis

        assert FlooringNoiseAnalysis(has_double_glazing=True).has_double_glazing == "yes"  # type: ignore[arg-type]
        assert FlooringNoiseAnalysis(has_double_glazing=False).has_double_glazing == "no"  # type: ignore[arg-type]
        assert FlooringNoiseAnalysis(has_double_glazing=None).has_double_glazing == "unknown"  # type: ignore[arg-type]

    def test_listing_extraction_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for bills/pets."""
        from home_finder.models import ListingExtraction

        assert ListingExtraction(bills_included=True).bills_included == "yes"  # type: ignore[arg-type]
        assert ListingExtraction(bills_included=False).bills_included == "no"  # type: ignore[arg-type]
        assert ListingExtraction(bills_included=None).bills_included == "unknown"  # type: ignore[arg-type]
        assert ListingExtraction(pets_allowed=True).pets_allowed == "yes"  # type: ignore[arg-type]
        assert ListingExtraction(pets_allowed=False).pets_allowed == "no"  # type: ignore[arg-type]
        assert ListingExtraction(pets_allowed=None).pets_allowed == "unknown"  # type: ignore[arg-type]

    def test_kitchen_washing_machine_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for washing machine."""
        assert KitchenAnalysis(has_washing_machine=True).has_washing_machine == "yes"  # type: ignore[arg-type]
        assert KitchenAnalysis(has_washing_machine=False).has_washing_machine == "no"  # type: ignore[arg-type]
        assert KitchenAnalysis(has_washing_machine=None).has_washing_machine == "unknown"  # type: ignore[arg-type]

    def test_bathroom_ensuite_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for ensuite."""
        from home_finder.models import BathroomAnalysis

        assert BathroomAnalysis(is_ensuite=True).is_ensuite == "yes"  # type: ignore[arg-type]
        assert BathroomAnalysis(is_ensuite=False).is_ensuite == "no"  # type: ignore[arg-type]
        assert BathroomAnalysis(is_ensuite=None).is_ensuite == "unknown"  # type: ignore[arg-type]

    def test_bedroom_tristate_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for bedroom tri-state fields."""
        from home_finder.models import BedroomAnalysis

        assert BedroomAnalysis(primary_is_double=True).primary_is_double == "yes"  # type: ignore[arg-type]
        assert BedroomAnalysis(primary_is_double=False).primary_is_double == "no"  # type: ignore[arg-type]
        assert BedroomAnalysis(primary_is_double=None).primary_is_double == "unknown"  # type: ignore[arg-type]
        assert BedroomAnalysis(can_fit_desk=True).can_fit_desk == "yes"  # type: ignore[arg-type]
        assert BedroomAnalysis(can_fit_desk=False).can_fit_desk == "no"  # type: ignore[arg-type]
        assert BedroomAnalysis(can_fit_desk=None).can_fit_desk == "unknown"  # type: ignore[arg-type]


class TestNewMarcelFields:
    """Tests for new Marcel-specific fields in schemas and models."""

    def test_new_phase1_fields_in_visual_schema(self) -> None:
        """New Marcel-specific fields appear in Phase 1 tool schema."""
        visual = VISUAL_ANALYSIS_TOOL["input_schema"]["properties"]

        # office_separation in bedroom sub-model
        bedroom_props = visual["bedroom"]["properties"]
        assert "office_separation" in bedroom_props
        assert bedroom_props["office_separation"]["type"] == "string"
        assert "dedicated_room" in bedroom_props["office_separation"]["enum"]
        assert "unknown" in bedroom_props["office_separation"]["enum"]

        # hosting_layout in space sub-model
        space_props = visual["space"]["properties"]
        assert "hosting_layout" in space_props
        assert space_props["hosting_layout"]["type"] == "string"
        assert "excellent" in space_props["hosting_layout"]["enum"]

        # hosting_noise_risk in flooring_noise sub-model
        fn_props = visual["flooring_noise"]["properties"]
        assert "hosting_noise_risk" in fn_props
        assert fn_props["hosting_noise_risk"]["type"] == "string"
        assert "low" in fn_props["hosting_noise_risk"]["enum"]

    def test_broadband_type_in_evaluation_schema(self) -> None:
        """broadband_type appears in Phase 2 listing_extraction."""
        eval_schema = EVALUATION_TOOL["input_schema"]["properties"]
        le_props = eval_schema["listing_extraction"]["properties"]
        assert "broadband_type" in le_props
        assert le_props["broadband_type"]["type"] == "string"
        assert "fttp" in le_props["broadband_type"]["enum"]
        assert "unknown" in le_props["broadband_type"]["enum"]

    def test_new_fields_are_not_tristate(self) -> None:
        """New multi-value fields should NOT be yes/no/unknown tri-states."""
        visual = VISUAL_ANALYSIS_TOOL["input_schema"]["properties"]
        # office_separation is 5-value, not tri-state
        sep_enum = visual["bedroom"]["properties"]["office_separation"]["enum"]
        assert len(sep_enum) == 5
        assert "yes" not in sep_enum

    def test_new_highlights_in_strict_eval_schema(self) -> None:
        """New highlight enum values are present in strict Phase 2 schema."""
        eval_schema = EVALUATION_TOOL["input_schema"]["properties"]
        hl_items = eval_schema["highlights"]["items"]
        enum_values = hl_items.get("enum", [])
        assert "Ultrafast broadband (FTTP)" in enum_values
        assert "Dedicated office room" in enum_values
        assert "Separate work area" in enum_values
        assert "Great hosting layout" in enum_values

    def test_new_lowlights_in_strict_eval_schema(self) -> None:
        """New lowlight enum values are present in strict Phase 2 schema."""
        eval_schema = EVALUATION_TOOL["input_schema"]["properties"]
        ll_items = eval_schema["lowlights"]["items"]
        enum_values = ll_items.get("enum", [])
        assert "Basic broadband only" in enum_values
        assert "No work-life separation" in enum_values
        assert "Poor hosting layout" in enum_values


class TestNewFieldBackwardCompat:
    """New fields gracefully handle missing data from old DB rows."""

    def test_bedroom_without_office_separation(self) -> None:
        """Old DB rows without office_separation default to 'unknown'."""
        old_data = {"primary_is_double": "yes", "can_fit_desk": "yes", "notes": ""}
        bedroom = BedroomAnalysis.model_validate(old_data)
        assert bedroom.office_separation == "unknown"

    def test_space_without_hosting_layout(self) -> None:
        """Old DB rows without hosting_layout default to 'unknown'."""
        old_data = {"living_room_sqm": 20, "is_spacious_enough": True, "confidence": "high"}
        space = SpaceAnalysis.model_validate(old_data)
        assert space.hosting_layout == "unknown"

    def test_flooring_without_hosting_noise_risk(self) -> None:
        """Old DB rows without hosting_noise_risk default to 'unknown'."""
        old_data = {
            "primary_flooring": "hardwood",
            "has_double_glazing": "yes",
            "building_construction": "solid_brick",
            "noise_indicators": [],
            "notes": "",
        }
        flooring = FlooringNoiseAnalysis.model_validate(old_data)
        assert flooring.hosting_noise_risk == "unknown"

    def test_listing_extraction_without_broadband(self) -> None:
        """Old DB rows without broadband_type default to None."""
        old_data = {
            "epc_rating": "C",
            "property_type": "victorian",
            "bills_included": "no",
            "pets_allowed": "unknown",
        }
        le = ListingExtraction.model_validate(old_data)
        assert le.broadband_type is None

    def test_full_analysis_roundtrip_without_new_fields(self) -> None:
        """Full PropertyQualityAnalysis parses old JSON without new fields."""
        old_json: dict[str, Any] = {
            "kitchen": {
                "overall_quality": "decent",
                "hob_type": "gas",
                "has_dishwasher": "yes",
                "has_washing_machine": "yes",
                "notes": "",
            },
            "condition": {
                "overall_condition": "good",
                "has_visible_damp": "no",
                "has_visible_mold": "no",
                "has_worn_fixtures": "no",
                "maintenance_concerns": [],
                "confidence": "high",
            },
            "light_space": {
                "natural_light": "good",
                "feels_spacious": True,
                "notes": "",
            },
            "space": {
                "living_room_sqm": 20,
                "is_spacious_enough": True,
                "confidence": "high",
            },
            "summary": "Nice flat",
        }
        analysis = PropertyQualityAnalysis.model_validate(old_json)
        assert analysis.space.hosting_layout == "unknown"
        assert analysis.summary == "Nice flat"


class TestNewFieldsPipelineFlow:
    """New Marcel fields pass through the two-phase pipeline."""

    async def test_new_fields_flow_through_two_phase_pipeline(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """New Marcel fields pass from API response to PropertyQualityAnalysis."""
        visual = {
            "kitchen": {
                "overall_quality": "modern",
                "hob_type": "gas",
                "has_dishwasher": "yes",
                "has_washing_machine": "yes",
                "notes": "",
            },
            "condition": {
                "overall_condition": "good",
                "has_visible_damp": "no",
                "has_visible_mold": "no",
                "has_worn_fixtures": "no",
                "maintenance_concerns": [],
                "confidence": "high",
            },
            "light_space": {
                "natural_light": "good",
                "feels_spacious": True,
                "notes": "",
            },
            "space": {
                "living_room_sqm": 22,
                "is_spacious_enough": True,
                "confidence": "high",
                "hosting_layout": "excellent",
            },
            "bedroom": {
                "primary_is_double": "yes",
                "has_built_in_wardrobe": "yes",
                "can_fit_desk": "yes",
                "office_separation": "dedicated_room",
                "notes": "",
            },
            "flooring_noise": {
                "primary_flooring": "hardwood",
                "has_double_glazing": "yes",
                "noise_indicators": [],
                "hosting_noise_risk": "low",
                "notes": "",
            },
            "overall_rating": 4,
            "condition_concerns": False,
            "concern_severity": "none",
            "summary": "Great flat",
        }
        eval_resp = {
            "listing_extraction": {
                "epc_rating": "C",
                "service_charge_pcm": None,
                "deposit_weeks": 5,
                "bills_included": "no",
                "pets_allowed": "unknown",
                "parking": "street",
                "council_tax_band": "C",
                "property_type": "victorian",
                "furnished_status": "furnished",
                "broadband_type": "fttp",
            },
            "value_for_quality": {"rating": "good", "reasoning": "Fair price"},
            "viewing_notes": {
                "check_items": [],
                "questions_for_agent": [],
                "deal_breaker_tests": [],
            },
            "highlights": ["Gas hob"],
            "lowlights": [],
            "one_line": "Great flat for WFH",
        }

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(visual, eval_resp)

        results = await quality_filter.analyze_merged_properties([sample_merged_property])
        _, analysis = results[0]

        assert analysis.bedroom is not None
        assert analysis.bedroom.office_separation == "dedicated_room"
        assert analysis.space.hosting_layout == "excellent"
        assert analysis.flooring_noise is not None
        assert analysis.flooring_noise.hosting_noise_risk == "low"
        assert analysis.listing_extraction is not None
        assert analysis.listing_extraction.broadband_type == "fttp"


class TestCircuitBreaker:
    """Tests for API circuit breaker in PropertyQualityFilter."""

    async def test_circuit_opens_after_consecutive_failures(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Circuit should open after _CIRCUIT_BREAKER_THRESHOLD consecutive API failures."""
        from anthropic import APIConnectionError

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )

        assert not quality_filter._circuit_open

        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            with pytest.raises(APIUnavailableError):
                await quality_filter.analyze_single_merged(sample_merged_property)

        assert quality_filter._circuit_open
        assert quality_filter._consecutive_api_failures == _CIRCUIT_BREAKER_THRESHOLD

    async def test_circuit_open_raises_immediately(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """When circuit is open (within cooldown), should raise without calling API."""
        import time

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._circuit_open = True
        quality_filter._circuit_opened_at = time.monotonic()  # just opened
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock()

        with pytest.raises(APIUnavailableError):
            await quality_filter.analyze_single_merged(sample_merged_property)

        # Should NOT have called the API
        quality_filter._client.messages.create.assert_not_called()

    async def test_success_resets_failure_counter(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Successful API call should reset the consecutive failure counter."""
        from anthropic import APIConnectionError

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()

        # First: fail twice (below threshold)
        quality_filter._client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD - 1):
            with pytest.raises(APIUnavailableError):
                await quality_filter.analyze_single_merged(sample_merged_property)

        assert quality_filter._consecutive_api_failures == _CIRCUIT_BREAKER_THRESHOLD - 1
        assert not quality_filter._circuit_open

        # Then: succeed — counter should reset
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )
        await quality_filter.analyze_single_merged(sample_merged_property)

        assert quality_filter._consecutive_api_failures == 0
        assert not quality_filter._circuit_open

    async def test_rate_limit_error_trips_circuit(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """RateLimitError should count toward circuit breaker."""
        from anthropic import RateLimitError

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(
            side_effect=RateLimitError(
                message="rate limited",
                response=mock_response,
                body=None,
            )
        )

        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            with pytest.raises(APIUnavailableError):
                await quality_filter.analyze_single_merged(sample_merged_property)

        assert quality_filter._circuit_open

    async def test_internal_server_error_trips_circuit(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """InternalServerError should count toward circuit breaker."""
        from anthropic import InternalServerError

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {}
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(
            side_effect=InternalServerError(
                message="server error",
                response=mock_response,
                body=None,
            )
        )

        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            with pytest.raises(APIUnavailableError):
                await quality_filter.analyze_single_merged(sample_merged_property)

        assert quality_filter._circuit_open

    async def test_bad_request_does_not_trip_circuit(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """BadRequestError should NOT count toward circuit breaker."""
        from anthropic import BadRequestError

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {}
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(
            side_effect=BadRequestError(
                message="bad request",
                response=mock_response,
                body=None,
            )
        )

        # Exceed threshold — circuit should NOT open (BadRequestError is request-specific)
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD + 1):
            _merged, analysis = await quality_filter.analyze_single_merged(sample_merged_property)
            assert "No images available" in analysis.summary

        assert not quality_filter._circuit_open
        assert quality_filter._consecutive_api_failures == 0

    async def test_generic_exception_does_not_trip_circuit(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Generic exceptions should NOT count toward circuit breaker."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(
            side_effect=ValueError("unexpected error")
        )

        for _ in range(_CIRCUIT_BREAKER_THRESHOLD + 1):
            _merged, analysis = await quality_filter.analyze_single_merged(sample_merged_property)
            assert "No images available" in analysis.summary

        assert not quality_filter._circuit_open

    async def test_circuit_stays_open_during_cooldown(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Circuit should remain open before cooldown expires."""
        import time

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._circuit_open = True
        quality_filter._circuit_opened_at = time.monotonic()  # just opened
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock()

        with pytest.raises(APIUnavailableError):
            await quality_filter.analyze_single_merged(sample_merged_property)
        quality_filter._client.messages.create.assert_not_called()

    async def test_circuit_allows_retry_after_cooldown(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """After cooldown, circuit should allow one retry (half-open)."""
        import time

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._circuit_open = True
        quality_filter._circuit_opened_at = time.monotonic() - (_CIRCUIT_BREAKER_COOLDOWN + 1)
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        # Should NOT raise — half-open allows retry
        await quality_filter.analyze_single_merged(sample_merged_property)
        quality_filter._client.messages.create.assert_called()

    async def test_circuit_closes_on_success_after_halfopen(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Successful retry after half-open should fully close circuit."""
        import time

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._circuit_open = True
        quality_filter._circuit_opened_at = time.monotonic() - (_CIRCUIT_BREAKER_COOLDOWN + 1)
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock(
            sample_visual_response, sample_evaluation_response
        )

        await quality_filter.analyze_single_merged(sample_merged_property)
        assert not quality_filter._circuit_open
        assert quality_filter._circuit_opened_at is None
        assert quality_filter._consecutive_api_failures == 0

    async def test_circuit_reopens_on_failure_after_halfopen(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Failed retry after half-open should re-open with fresh timestamp."""
        import time

        from anthropic import APIConnectionError

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._circuit_open = True
        quality_filter._consecutive_api_failures = _CIRCUIT_BREAKER_THRESHOLD
        old_time = time.monotonic() - (_CIRCUIT_BREAKER_COOLDOWN + 1)
        quality_filter._circuit_opened_at = old_time
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )

        with pytest.raises(APIUnavailableError):
            await quality_filter.analyze_single_merged(sample_merged_property)
        assert quality_filter._circuit_open
        assert quality_filter._circuit_opened_at > old_time  # fresh timestamp


class TestHttpClientReuse:
    """Tests for HTTP client reuse (T2.9)."""

    async def test_curl_session_reused_across_downloads(self) -> None:
        """Single curl session should be reused across multiple image downloads."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(
            return_value=MagicMock(
                status_code=200,
                content=b"fake-image",
                headers={"content-type": "image/jpeg"},
            )
        )

        quality_filter._curl_session = mock_session
        await quality_filter._download_image_as_base64("https://example.com/1.jpg")
        await quality_filter._download_image_as_base64("https://example.com/2.jpg")
        assert mock_session.get.call_count == 2  # same session, two calls

    async def test_close_closes_curl_session(self) -> None:
        """close() should close the curl session."""
        quality_filter = PropertyQualityFilter(api_key="test-key")
        mock_session = AsyncMock()
        quality_filter._curl_session = mock_session
        await quality_filter.close()
        mock_session.close.assert_called_once()
        assert quality_filter._curl_session is None


class TestAcousticContextMapping:
    """Tests for building_construction → acoustic profile mapping."""

    def _build_acoustic_context(self, visual_data: dict[str, Any]) -> str | None:
        """Replicate the mapping logic from _analyze_property."""
        from home_finder.data.area_context import ACOUSTIC_PROFILES

        flooring_raw = visual_data.get("flooring_noise")
        construction: str | None = (
            flooring_raw.get("building_construction") if isinstance(flooring_raw, dict) else None
        )
        if not construction:
            return None
        mapping = {
            "timber_frame": "victorian",
            "concrete": "ex_council",
            "solid_brick": "purpose_built",
        }
        profile_key = mapping.get(construction)
        if not profile_key:
            return None
        profile = ACOUSTIC_PROFILES.get(profile_key)
        if not profile:
            return None
        db_range = profile["airborne_insulation_db"]
        return (
            f"Building construction: {construction}\n"
            f"Typical sound insulation: {db_range} dB airborne\n"
            f"Hosting safety: {profile['hosting_safety']}\n"
            f"{profile['summary']}"
        )

    def test_timber_frame_maps_to_victorian(self) -> None:
        visual = {
            "flooring_noise": {"building_construction": "timber_frame"},
        }
        ctx = self._build_acoustic_context(visual)
        assert ctx is not None
        assert "timber_frame" in ctx
        assert "poor" in ctx  # victorian hosting_safety

    def test_concrete_maps_to_ex_council(self) -> None:
        visual = {
            "flooring_noise": {"building_construction": "concrete"},
        }
        ctx = self._build_acoustic_context(visual)
        assert ctx is not None
        assert "concrete" in ctx
        assert "moderate" in ctx  # ex_council hosting_safety

    def test_solid_brick_maps_to_purpose_built(self) -> None:
        visual = {
            "flooring_noise": {"building_construction": "solid_brick"},
        }
        ctx = self._build_acoustic_context(visual)
        assert ctx is not None
        assert "solid_brick" in ctx
        assert "moderate" in ctx  # purpose_built hosting_safety

    def test_mixed_returns_none(self) -> None:
        visual = {
            "flooring_noise": {"building_construction": "mixed"},
        }
        assert self._build_acoustic_context(visual) is None

    def test_unknown_returns_none(self) -> None:
        visual = {
            "flooring_noise": {"building_construction": "unknown"},
        }
        assert self._build_acoustic_context(visual) is None

    def test_missing_flooring_noise_returns_none(self) -> None:
        assert self._build_acoustic_context({}) is None

    def test_flooring_noise_not_dict_returns_none(self) -> None:
        visual = {"flooring_noise": "some string"}
        assert self._build_acoustic_context(visual) is None

    def test_context_injected_into_evaluation_prompt(self) -> None:
        """Acoustic context flows through to build_evaluation_prompt."""
        visual = {
            "flooring_noise": {"building_construction": "concrete"},
        }
        ctx = self._build_acoustic_context(visual)
        prompt = build_evaluation_prompt(
            visual_data=visual,
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
            acoustic_context=ctx,
        )
        assert "<acoustic_context>" in prompt
        assert "concrete" in prompt


# ── Helpers for model-pair consistency tests ──────────────────────────


def _extract_enum(
    prop: dict[str, Any], defs: dict[str, Any] | None = None
) -> list[str] | None:
    """Extract enum values from a JSON schema property.

    Handles plain ``{"enum": [...]}`` as well as ``anyOf`` wrappers that
    Pydantic generates for ``Literal[...] | None``, top-level ``$ref``
    pointers (e.g. ``PropertyType`` StrEnum), and ``$ref`` inside
    ``anyOf`` variants in the storage models.
    """
    if "enum" in prop:
        return sorted(str(v) for v in prop["enum"] if v is not None)

    # Top-level $ref (e.g. StrEnum field without | None)
    if "$ref" in prop and defs:
        ref_name = prop["$ref"].rsplit("/", 1)[-1]
        ref_schema = defs.get(ref_name, {})
        if "enum" in ref_schema:
            return sorted(str(v) for v in ref_schema["enum"] if v is not None)

    for variant in prop.get("anyOf", []):
        if "$ref" in variant and defs:
            ref_name = variant["$ref"].rsplit("/", 1)[-1]
            ref_schema = defs.get(ref_name, {})
            if "enum" in ref_schema:
                return sorted(str(v) for v in ref_schema["enum"] if v is not None)
        if "enum" in variant:
            vals = [str(v) for v in variant["enum"] if v is not None]
            if vals:
                return sorted(vals)

    return None


_PAIR_IDS = [
    f"{api.__qualname__}-{storage.__name__}" for api, storage in _MODEL_PAIRS
]


class TestModelPairConsistency:
    """Verify API response models and storage models stay in sync.

    The ``_MODEL_PAIRS`` constant in ``quality.py`` maps each API
    sub-model to its storage counterpart.  These tests mechanically
    check that the models have matching fields, compatible enum values,
    and that API output validates against the storage model.
    """

    @pytest.mark.parametrize("api_model,storage_model", _MODEL_PAIRS, ids=_PAIR_IDS)
    def test_field_names_match(
        self,
        api_model: type[_BaseModel],
        storage_model: type[_BaseModel],
    ) -> None:
        """API and storage models must have identical field names."""
        api_fields = set(api_model.model_fields.keys())
        storage_fields = set(storage_model.model_fields.keys())
        assert api_fields == storage_fields, (
            f"{api_model.__qualname__} vs {storage_model.__name__}: "
            f"API-only={api_fields - storage_fields}, "
            f"storage-only={storage_fields - api_fields}"
        )

    @pytest.mark.parametrize("api_model,storage_model", _MODEL_PAIRS, ids=_PAIR_IDS)
    def test_literal_enum_values_match(
        self,
        api_model: type[_BaseModel],
        storage_model: type[_BaseModel],
    ) -> None:
        """Literal/enum values must match for every shared field."""
        api_schema = api_model.model_json_schema()
        storage_schema = storage_model.model_json_schema()
        storage_defs = storage_schema.get("$defs", {})

        for field_name in api_model.model_fields:
            api_prop = api_schema.get("properties", {}).get(field_name, {})
            storage_prop = storage_schema.get("properties", {}).get(field_name, {})

            api_enum = _extract_enum(api_prop)
            storage_enum = _extract_enum(storage_prop, defs=storage_defs)

            if api_enum is None and storage_enum is None:
                continue
            if api_enum is None or storage_enum is None:
                # One has enums, the other doesn't — only flag if API has
                # enums that storage doesn't (the reverse is OK since storage
                # may use plain types with coercion).
                if api_enum is not None and storage_enum is None:
                    pytest.fail(
                        f"{api_model.__qualname__}.{field_name}: "
                        f"API has enum {api_enum} but storage has none"
                    )
                continue
            assert api_enum == storage_enum, (
                f"{api_model.__qualname__}.{field_name}: "
                f"API enum {api_enum} != storage enum {storage_enum}"
            )

    def test_all_api_submodels_are_paired(self) -> None:
        """Every nested BaseModel in the API responses must appear in _MODEL_PAIRS."""
        paired_api_models = {pair[0] for pair in _MODEL_PAIRS}
        unpaired_by_design = {_EvaluationResponse.ValueForQuality}

        missing: list[str] = []
        for parent in (_VisualAnalysisResponse, _EvaluationResponse):
            for attr_name in dir(parent):
                attr = getattr(parent, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, _BaseModel)
                    and attr is not parent
                    and attr not in paired_api_models
                    and attr not in unpaired_by_design
                ):
                    missing.append(f"{parent.__name__}.{attr_name}")

        assert not missing, (
            f"API sub-models not in _MODEL_PAIRS (add them or document as "
            f"intentionally unpaired): {missing}"
        )

    @pytest.mark.parametrize("api_model,storage_model", _MODEL_PAIRS, ids=_PAIR_IDS)
    def test_api_data_validates_in_storage(
        self,
        api_model: type[_BaseModel],
        storage_model: type[_BaseModel],
    ) -> None:
        """A valid API model instance must validate in the storage model."""
        # Build a minimal valid instance with all required fields populated
        schema = api_model.model_json_schema()
        props = schema.get("properties", {})
        data: dict[str, Any] = {}

        for field_name, prop in props.items():
            data[field_name] = _minimal_value(prop)

        api_instance = api_model.model_validate(data)
        dumped = api_instance.model_dump()

        # This must not raise — storage model should accept API output
        storage_model.model_validate(dumped)


def _minimal_value(prop: dict[str, Any]) -> Any:
    """Produce a minimal valid value for a JSON schema property."""
    if "enum" in prop:
        return prop["enum"][0]

    # anyOf: pick first variant that isn't null
    if "anyOf" in prop:
        for variant in prop["anyOf"]:
            if variant.get("type") == "null":
                continue
            return _minimal_value(variant)
        return None

    typ = prop.get("type")
    if typ == "string":
        return ""
    if typ == "integer":
        return 0
    if typ == "number":
        return 0.0
    if typ == "boolean":
        return False
    if typ == "array":
        return []
    if typ == "null":
        return None
    return ""
