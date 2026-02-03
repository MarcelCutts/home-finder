"""Tests for property quality analysis filter."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic.types import ToolUseBlock
from pydantic import HttpUrl, ValidationError

from home_finder.filters.quality import (
    QUALITY_ANALYSIS_TOOL,
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    PropertyQualityAnalysis,
    PropertyQualityFilter,
    SpaceAnalysis,
    ValueAnalysis,
    assess_value,
)
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource


class TestKitchenAnalysis:
    """Tests for KitchenAnalysis model."""

    def test_valid_full_analysis(self) -> None:
        """Should create analysis with all fields."""
        analysis = KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher=True,
            has_washing_machine=True,
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
    """Tests for QUALITY_ANALYSIS_TOOL schema structure."""

    def test_uses_anyof_for_nullable_enum_fields(self) -> None:
        """Should use anyOf pattern for nullable enum fields (strict mode compatibility)."""
        input_schema = QUALITY_ANALYSIS_TOOL["input_schema"]
        schema = input_schema["properties"]

        # Kitchen hob_type should use anyOf
        hob_type = schema["kitchen"]["properties"]["hob_type"]
        assert "anyOf" in hob_type
        assert {"type": "null"} in hob_type["anyOf"]
        enum_option = next(o for o in hob_type["anyOf"] if o.get("type") == "string")
        assert "enum" in enum_option
        assert "gas" in enum_option["enum"]

        # Light space window_sizes should use anyOf
        window_sizes = schema["light_space"]["properties"]["window_sizes"]
        assert "anyOf" in window_sizes
        assert {"type": "null"} in window_sizes["anyOf"]

        # Light space ceiling_height should use anyOf
        ceiling_height = schema["light_space"]["properties"]["ceiling_height"]
        assert "anyOf" in ceiling_height
        assert {"type": "null"} in ceiling_height["anyOf"]

        # Concern severity should use anyOf
        concern_severity = schema["concern_severity"]
        assert "anyOf" in concern_severity
        assert {"type": "null"} in concern_severity["anyOf"]

    def test_uses_anyof_for_nullable_primitive_fields(self) -> None:
        """Should use anyOf pattern for nullable boolean/number fields."""
        input_schema = QUALITY_ANALYSIS_TOOL["input_schema"]
        schema = input_schema["properties"]

        # Kitchen has_dishwasher should use anyOf
        has_dishwasher = schema["kitchen"]["properties"]["has_dishwasher"]
        assert "anyOf" in has_dishwasher
        assert {"type": "boolean"} in has_dishwasher["anyOf"]
        assert {"type": "null"} in has_dishwasher["anyOf"]

        # Space living_room_sqm should use anyOf
        living_room_sqm = schema["space"]["properties"]["living_room_sqm"]
        assert "anyOf" in living_room_sqm
        assert {"type": "number"} in living_room_sqm["anyOf"]
        assert {"type": "null"} in living_room_sqm["anyOf"]

        # Space is_spacious_enough should use anyOf
        is_spacious = schema["space"]["properties"]["is_spacious_enough"]
        assert "anyOf" in is_spacious
        assert {"type": "boolean"} in is_spacious["anyOf"]
        assert {"type": "null"} in is_spacious["anyOf"]

    def test_schema_has_strict_mode_enabled(self) -> None:
        """Should have strict mode enabled for guaranteed schema compliance."""
        assert QUALITY_ANALYSIS_TOOL["strict"] is True


class TestConditionAnalysis:
    """Tests for ConditionAnalysis model."""

    def test_valid_analysis_with_concerns(self) -> None:
        """Should create analysis with condition concerns."""
        analysis = ConditionAnalysis(
            overall_condition="fair",
            has_visible_damp=True,
            has_visible_mold=False,
            has_worn_fixtures=True,
            maintenance_concerns=["Damp near window", "Dated bathroom"],
            confidence="high",
        )
        assert analysis.overall_condition == "fair"
        assert analysis.has_visible_damp is True
        assert len(analysis.maintenance_concerns) == 2

    def test_minimal_analysis(self) -> None:
        """Should create analysis with defaults."""
        analysis = ConditionAnalysis(overall_condition="good")
        assert analysis.has_visible_damp is False
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
                has_visible_damp=False,
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
                has_visible_damp=True,
                has_visible_mold=True,
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
def sample_tool_response_with_nulls() -> dict[str, Any]:
    """Sample tool response with nullable fields set to null."""
    return {
        "kitchen": {
            "overall_quality": "unknown",
            "hob_type": None,
            "has_dishwasher": None,
            "has_washing_machine": None,
            "notes": "Kitchen not visible in images",
        },
        "condition": {
            "overall_condition": "unknown",
            "has_visible_damp": False,
            "has_visible_mold": False,
            "has_worn_fixtures": False,
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
        "value_for_quality": {
            "rating": "fair",
            "reasoning": "Cannot assess quality from available images",
        },
        "condition_concerns": False,
        "concern_severity": None,
        "summary": "Limited visibility - cannot fully assess property condition.",
    }


@pytest.fixture
def sample_tool_response() -> dict[str, Any]:
    """Sample structured tool response from Claude."""
    return {
        "kitchen": {
            "overall_quality": "modern",
            "hob_type": "gas",
            "has_dishwasher": True,
            "has_washing_machine": True,
            "notes": "Modern integrated kitchen",
        },
        "condition": {
            "overall_condition": "good",
            "has_visible_damp": False,
            "has_visible_mold": False,
            "has_worn_fixtures": False,
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
        },
        "value_for_quality": {
            "rating": "good",
            "reasoning": "Well-maintained property at reasonable price",
        },
        "condition_concerns": False,
        "concern_severity": None,
        "summary": "Well-maintained flat with modern kitchen. Living room suits home office.",
    }


def create_mock_response(tool_input: dict[str, Any], stop_reason: str = "tool_use") -> MagicMock:
    """Create a mock API response with tool use block."""
    tool_block = ToolUseBlock(
        id="toolu_123",
        type="tool_use",
        name="property_quality_analysis",
        input=tool_input,
    )
    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.stop_reason = stop_reason
    mock_response.usage = MagicMock()
    mock_response.usage.cache_read_input_tokens = 0
    mock_response.usage.cache_creation_input_tokens = 0
    return mock_response


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
            assert PropertyQualityFilter._is_valid_image_url(url), f"Should accept {url}"

    def test_is_valid_image_url_rejects_pdfs(self) -> None:
        """Should reject PDF URLs (not supported by Claude Vision API)."""
        pdf_urls = [
            "https://lc.zoocdn.com/abc123.pdf",
            "https://example.com/floorplan.PDF",
            "https://example.com/doc.pdf?download=true",
        ]
        for url in pdf_urls:
            assert not PropertyQualityFilter._is_valid_image_url(url), f"Should reject {url}"

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
        sample_tool_response: dict[str, Any],
    ) -> None:
        """Should analyze property with gallery images using structured outputs."""
        mock_response = create_mock_response(sample_tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"
        assert analysis.condition.overall_condition == "good"
        assert analysis.light_space.natural_light == "excellent"
        assert analysis.space.living_room_sqm == 22

    async def test_overrides_space_for_two_plus_beds(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Should override space assessment for 2+ bedroom properties."""
        # Response says not spacious enough, but 2-bed should override
        tool_response = {
            "kitchen": {"notes": ""},
            "condition": {
                "overall_condition": "good",
                "has_visible_damp": False,
                "has_visible_mold": False,
                "has_worn_fixtures": False,
                "maintenance_concerns": [],
                "confidence": "high",
            },
            "light_space": {"natural_light": "good", "feels_spacious": True, "notes": ""},
            "space": {"living_room_sqm": 15, "is_spacious_enough": False, "confidence": "high"},
            "value_for_quality": {"rating": "good", "reasoning": "Fair price"},
            "condition_concerns": False,
            "concern_severity": None,
            "summary": "Compact living room",
        }
        mock_response = create_mock_response(tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        _, analysis = results[0]
        # Should be overridden because property has 2 bedrooms
        assert analysis.space.is_spacious_enough is True
        assert analysis.space.confidence == "high"

    async def test_does_not_override_space_for_one_bed(
        self,
        one_bed_merged_property: MergedProperty,
    ) -> None:
        """Should NOT override space assessment for 1-bed properties."""
        tool_response = {
            "kitchen": {"notes": ""},
            "condition": {
                "overall_condition": "good",
                "has_visible_damp": False,
                "has_visible_mold": False,
                "has_worn_fixtures": False,
                "maintenance_concerns": [],
                "confidence": "high",
            },
            "light_space": {"natural_light": "good", "feels_spacious": True, "notes": ""},
            "space": {"living_room_sqm": 15, "is_spacious_enough": False, "confidence": "high"},
            "value_for_quality": {"rating": "good", "reasoning": "Fair price"},
            "condition_concerns": False,
            "concern_severity": None,
            "summary": "Compact living room",
        }
        mock_response = create_mock_response(tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([one_bed_merged_property])

        _, analysis = results[0]
        # Should keep original assessment
        assert analysis.space.is_spacious_enough is False

    async def test_handles_llm_failure_gracefully(
        self,
        sample_merged_property: MergedProperty,
    ) -> None:
        """Should return minimal analysis on LLM failure."""
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

    async def test_includes_all_images_in_api_call(
        self,
        sample_merged_property: MergedProperty,
        sample_tool_response: dict[str, Any],
    ) -> None:
        """Should include gallery images and floorplan in API call."""
        mock_response = create_mock_response(sample_tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key", max_images=10)
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        await quality_filter.analyze_merged_properties([sample_merged_property])

        # Check the API call
        call_args = quality_filter._client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]

        # With image labels: 3 gallery × (label + image) + 1 floorplan × (label + image) + 1 text = 9
        assert len(content) == 9
        assert content[0]["type"] == "text"  # "Gallery image 1:"
        assert content[1]["type"] == "image"
        assert content[6]["type"] == "text"  # "Floorplan:"
        assert content[7]["type"] == "image"  # floorplan
        assert content[8]["type"] == "text"  # user prompt

    async def test_respects_max_images_limit(
        self,
        sample_property: Property,
        sample_tool_response: dict[str, Any],
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
        mock_response = create_mock_response(sample_tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key", max_images=5)
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        await quality_filter.analyze_merged_properties([many_images_merged])

        call_args = quality_filter._client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]

        # With labels: 5 gallery × (label + image) + 1 floorplan × (label + image) + 1 text = 13
        image_blocks = [c for c in content if c.get("type") == "image"]
        assert len(image_blocks) == 6  # 5 gallery + 1 floorplan
        label_blocks = [
            c
            for c in content
            if c.get("type") == "text"
            and "image" in c.get("text", "").lower()
            or "Floorplan" in c.get("text", "")
        ]
        assert len(label_blocks) >= 5  # at least 5 gallery labels

    async def test_uses_tool_choice_for_structured_output(
        self,
        sample_merged_property: MergedProperty,
        sample_tool_response: dict[str, Any],
    ) -> None:
        """Should use tool_choice to force structured output."""
        mock_response = create_mock_response(sample_tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        await quality_filter.analyze_merged_properties([sample_merged_property])

        call_args = quality_filter._client.messages.create.call_args

        # Verify tool_choice forces the property_quality_analysis tool
        assert call_args.kwargs["tool_choice"] == {
            "type": "tool",
            "name": "property_quality_analysis",
        }

        # Verify tools include our schema with strict mode
        tools = call_args.kwargs["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "property_quality_analysis"
        assert tools[0]["strict"] is True

    async def test_uses_cached_system_prompt(
        self,
        sample_merged_property: MergedProperty,
        sample_tool_response: dict[str, Any],
    ) -> None:
        """Should use system prompt with cache_control for cost savings."""
        mock_response = create_mock_response(sample_tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        await quality_filter.analyze_merged_properties([sample_merged_property])

        call_args = quality_filter._client.messages.create.call_args

        # Verify system prompt uses cache_control
        system = call_args.kwargs["system"]
        assert len(system) == 1
        assert system[0]["type"] == "text"
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert "expert London rental property analyst" in system[0]["text"]

    async def test_extracts_value_for_quality_from_tool_response(
        self,
        sample_merged_property: MergedProperty,
        sample_tool_response: dict[str, Any],
    ) -> None:
        """Should extract quality-adjusted value rating from tool response."""
        mock_response = create_mock_response(sample_tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        _, analysis = results[0]
        # value.quality_adjusted_rating comes from the tool response
        assert analysis.value is not None
        assert analysis.value.quality_adjusted_rating == "good"
        assert "Well-maintained" in analysis.value.quality_adjusted_note

    async def test_handles_end_turn_with_tool_use(
        self,
        sample_merged_property: MergedProperty,
        sample_tool_response: dict[str, Any],
    ) -> None:
        """Should handle end_turn stop_reason when tool_use block is present."""
        # Some responses come with end_turn but still have tool_use
        mock_response = create_mock_response(sample_tool_response, stop_reason="end_turn")

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([sample_merged_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"

    async def test_includes_description_in_prompt(
        self,
        sample_property: Property,
        sample_tool_response: dict[str, Any],
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
        mock_response = create_mock_response(sample_tool_response)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        await quality_filter.analyze_merged_properties([merged])

        call_args = quality_filter._client.messages.create.call_args
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
        sample_tool_response_with_nulls: dict[str, Any],
    ) -> None:
        """Should handle null values for optional fields in tool response."""
        mock_response = create_mock_response(sample_tool_response_with_nulls)

        # Use 1-bed property to avoid space override for 2+ beds
        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([one_bed_merged_property])

        assert len(results) == 1
        _, analysis = results[0]

        # Verify null values are handled correctly
        assert analysis.kitchen.overall_quality == "unknown"
        assert analysis.kitchen.hob_type is None
        assert analysis.kitchen.has_dishwasher is None
        assert analysis.kitchen.has_washing_machine is None

        assert analysis.light_space.window_sizes is None
        assert analysis.light_space.feels_spacious is None
        assert analysis.light_space.ceiling_height is None

        assert analysis.space.living_room_sqm is None
        assert analysis.space.is_spacious_enough is None
        assert analysis.space.confidence == "low"

        assert analysis.concern_severity is None
