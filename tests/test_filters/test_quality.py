"""Tests for property quality analysis filter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import TextBlock
from pydantic import HttpUrl, ValidationError

from home_finder.filters.quality import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    PropertyQualityAnalysis,
    PropertyQualityFilter,
    SpaceAnalysis,
    ValueAnalysis,
    assess_value,
    extract_json_from_response,
)
from home_finder.models import Property, PropertySource
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData


class TestExtractJsonFromResponse:
    """Tests for extract_json_from_response function."""

    def test_parses_raw_json(self):
        """Should parse raw JSON without any wrapping."""
        text = '{"key": "value", "number": 42}'
        result = extract_json_from_response(text)
        assert result == {"key": "value", "number": 42}

    def test_parses_json_with_whitespace(self):
        """Should parse JSON with leading/trailing whitespace."""
        text = '  \n{"key": "value"}\n  '
        result = extract_json_from_response(text)
        assert result == {"key": "value"}

    def test_extracts_from_markdown_json_block(self):
        """Should extract JSON from ```json code block."""
        text = """Here is the analysis:

```json
{"status": "success", "items": [1, 2, 3]}
```

That's the result."""
        result = extract_json_from_response(text)
        assert result == {"status": "success", "items": [1, 2, 3]}

    def test_extracts_from_plain_markdown_block(self):
        """Should extract JSON from ``` code block without language specifier."""
        text = """```
{"data": "test"}
```"""
        result = extract_json_from_response(text)
        assert result == {"data": "test"}

    def test_extracts_json_embedded_in_text(self):
        """Should find JSON braces embedded in surrounding text."""
        text = 'Here is my analysis: {"result": true} End of response.'
        result = extract_json_from_response(text)
        assert result == {"result": True}

    def test_raises_on_no_json(self):
        """Should raise JSONDecodeError when no JSON found."""
        text = "This is just plain text with no JSON."
        with pytest.raises(ValueError):  # json.JSONDecodeError is a ValueError subclass
            extract_json_from_response(text)

    def test_raises_on_invalid_json(self):
        """Should raise JSONDecodeError on malformed JSON."""
        text = '{"key": "value", "missing": }'
        with pytest.raises(ValueError):
            extract_json_from_response(text)

    def test_handles_nested_json(self):
        """Should handle nested JSON structures."""
        text = """```json
{
    "kitchen": {"has_gas_hob": true},
    "condition": {"overall_condition": "good"}
}
```"""
        result = extract_json_from_response(text)
        assert result["kitchen"]["has_gas_hob"] is True
        assert result["condition"]["overall_condition"] == "good"


class TestKitchenAnalysis:
    """Tests for KitchenAnalysis model."""

    def test_valid_full_analysis(self):
        """Should create analysis with all fields."""
        analysis = KitchenAnalysis(
            has_gas_hob=True,
            has_dishwasher=True,
            has_washing_machine=True,
            has_dryer=False,
            appliance_quality="high",
            notes="Modern kitchen with integrated appliances",
        )
        assert analysis.has_gas_hob is True
        assert analysis.appliance_quality == "high"

    def test_minimal_analysis(self):
        """Should create analysis with only defaults."""
        analysis = KitchenAnalysis()
        assert analysis.has_gas_hob is None
        assert analysis.notes == ""

    def test_invalid_appliance_quality(self):
        """Should reject invalid appliance quality."""
        with pytest.raises(ValidationError):
            KitchenAnalysis(appliance_quality="excellent")  # type: ignore[arg-type]


class TestConditionAnalysis:
    """Tests for ConditionAnalysis model."""

    def test_valid_analysis_with_concerns(self):
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

    def test_minimal_analysis(self):
        """Should create analysis with defaults."""
        analysis = ConditionAnalysis(overall_condition="good")
        assert analysis.has_visible_damp is False
        assert analysis.maintenance_concerns == []
        assert analysis.confidence == "medium"

    def test_invalid_condition(self):
        """Should reject invalid condition values."""
        with pytest.raises(ValidationError):
            ConditionAnalysis(overall_condition="amazing")  # type: ignore[arg-type]


class TestLightSpaceAnalysis:
    """Tests for LightSpaceAnalysis model."""

    def test_valid_full_analysis(self):
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

    def test_minimal_analysis(self):
        """Should create analysis with required fields only."""
        analysis = LightSpaceAnalysis(
            natural_light="fair",
            feels_spacious=False,
        )
        assert analysis.window_sizes is None
        assert analysis.ceiling_height is None


class TestSpaceAnalysis:
    """Tests for SpaceAnalysis model."""

    def test_valid_analysis_with_sqm(self):
        """Should create analysis with square meters."""
        analysis = SpaceAnalysis(
            living_room_sqm=25.5,
            is_spacious_enough=True,
            confidence="high",
        )
        assert analysis.living_room_sqm == 25.5
        assert analysis.is_spacious_enough is True

    def test_analysis_without_sqm(self):
        """Should create analysis without square meters."""
        analysis = SpaceAnalysis(
            is_spacious_enough=False,
            confidence="low",
        )
        assert analysis.living_room_sqm is None


class TestValueAnalysis:
    """Tests for ValueAnalysis model and assess_value function."""

    def test_value_analysis_model(self):
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

    def test_excellent_value_below_average(self):
        """Property well below average should be excellent value."""
        value = assess_value(price_pcm=1600, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "excellent"
        assert value.difference is not None and value.difference < 0
        assert "below" in value.note.lower()

    def test_good_value_at_average(self):
        """Property at average should be good value."""
        value = assess_value(price_pcm=1900, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "good"

    def test_fair_value_slightly_above(self):
        """Property slightly above average should be fair value."""
        value = assess_value(price_pcm=2050, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "fair"
        assert "above" in value.note.lower()

    def test_poor_value_well_above(self):
        """Property well above average should be poor value."""
        value = assess_value(price_pcm=2300, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "poor"

    def test_handles_missing_postcode(self):
        """Should handle missing postcode gracefully."""
        value = assess_value(price_pcm=1800, postcode=None, bedrooms=1)
        assert value.rating is None
        assert "cannot assess" in value.note.lower()

    def test_uses_default_for_unknown_area(self):
        """Should use default benchmark for unknown areas."""
        # W1 is not in our benchmarks, should use default
        value = assess_value(price_pcm=1800, postcode="W1A 1AA", bedrooms=1)
        assert value.rating is not None  # Should still produce a rating

    def test_different_bedroom_benchmarks(self):
        """Different bedroom counts should use different benchmarks."""
        value_1bed = assess_value(price_pcm=2000, postcode="E8 2LX", bedrooms=1)
        value_2bed = assess_value(price_pcm=2000, postcode="E8 2LX", bedrooms=2)

        # 2000 is above E8 1-bed average but below 2-bed average
        assert value_1bed.rating in ["fair", "poor"]
        assert value_2bed.rating in ["excellent", "good"]


class TestPropertyQualityAnalysis:
    """Tests for PropertyQualityAnalysis model."""

    def test_valid_full_analysis(self):
        """Should create complete quality analysis."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(has_gas_hob=True, appliance_quality="medium"),
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

    def test_analysis_with_concerns(self):
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

    def test_model_is_frozen(self):
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
def sample_detail_data() -> DetailPageData:
    """Sample detail page data with gallery and floorplan."""
    return DetailPageData(
        floorplan_url="https://example.com/floor.jpg",
        gallery_urls=[
            "https://example.com/img1.jpg",
            "https://example.com/img2.jpg",
            "https://example.com/img3.jpg",
        ],
    )


@pytest.fixture
def sample_llm_response() -> str:
    """Sample JSON response from Claude."""
    return """{
        "kitchen": {
            "has_gas_hob": true,
            "has_dishwasher": true,
            "has_washing_machine": true,
            "has_dryer": false,
            "appliance_quality": "medium",
            "notes": "Modern integrated kitchen"
        },
        "condition": {
            "overall_condition": "good",
            "has_visible_damp": false,
            "has_visible_mold": false,
            "has_worn_fixtures": false,
            "maintenance_concerns": [],
            "confidence": "high"
        },
        "light_space": {
            "natural_light": "excellent",
            "window_sizes": "large",
            "feels_spacious": true,
            "ceiling_height": "standard",
            "notes": "South-facing with good light"
        },
        "space": {
            "living_room_sqm": 22,
            "is_spacious_enough": true,
            "confidence": "high"
        },
        "condition_concerns": false,
        "concern_severity": null,
        "summary": "Well-maintained flat with modern kitchen. Living room suitable for home office."
    }"""


class TestPropertyQualityFilter:
    """Tests for PropertyQualityFilter."""

    async def test_creates_minimal_analysis_when_no_images(self, sample_property: Property):
        """Should create minimal analysis when no images available."""
        with patch.object(DetailFetcher, "fetch_detail_page", return_value=None):
            quality_filter = PropertyQualityFilter(api_key="test-key")
            results = await quality_filter.analyze_properties([sample_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary
        assert analysis.space.confidence == "low"

    async def test_analyzes_property_with_images(
        self,
        sample_property: Property,
        sample_detail_data: DetailPageData,
        sample_llm_response: str,
    ):
        """Should analyze property with gallery images."""
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text=sample_llm_response)]

        with patch.object(DetailFetcher, "fetch_detail_page", return_value=sample_detail_data):
            quality_filter = PropertyQualityFilter(api_key="test-key")
            quality_filter._client = MagicMock()
            quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await quality_filter.analyze_properties([sample_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.kitchen.has_gas_hob is True
        assert analysis.condition.overall_condition == "good"
        assert analysis.light_space.natural_light == "excellent"
        assert analysis.space.living_room_sqm == 22

    async def test_overrides_space_for_two_plus_beds(
        self,
        sample_property: Property,
        sample_detail_data: DetailPageData,
    ):
        """Should override space assessment for 2+ bedroom properties."""
        # Response says not spacious enough, but 2-bed should override
        response_json = """{
            "kitchen": {"notes": ""},
            "condition": {"overall_condition": "good", "maintenance_concerns": []},
            "light_space": {"natural_light": "good", "feels_spacious": true},
            "space": {"living_room_sqm": 15, "is_spacious_enough": false, "confidence": "high"},
            "condition_concerns": false,
            "summary": "Compact living room"
        }"""
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text=response_json)]

        with patch.object(DetailFetcher, "fetch_detail_page", return_value=sample_detail_data):
            quality_filter = PropertyQualityFilter(api_key="test-key")
            quality_filter._client = MagicMock()
            quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await quality_filter.analyze_properties([sample_property])

        _, analysis = results[0]
        # Should be overridden because property has 2 bedrooms
        assert analysis.space.is_spacious_enough is True
        assert analysis.space.confidence == "high"

    async def test_does_not_override_space_for_one_bed(
        self,
        one_bed_property: Property,
        sample_detail_data: DetailPageData,
    ):
        """Should NOT override space assessment for 1-bed properties."""
        response_json = """{
            "kitchen": {"notes": ""},
            "condition": {"overall_condition": "good", "maintenance_concerns": []},
            "light_space": {"natural_light": "good", "feels_spacious": true},
            "space": {"living_room_sqm": 15, "is_spacious_enough": false, "confidence": "high"},
            "condition_concerns": false,
            "summary": "Compact living room"
        }"""
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text=response_json)]

        with patch.object(DetailFetcher, "fetch_detail_page", return_value=sample_detail_data):
            quality_filter = PropertyQualityFilter(api_key="test-key")
            quality_filter._client = MagicMock()
            quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await quality_filter.analyze_properties([one_bed_property])

        _, analysis = results[0]
        # Should keep original assessment
        assert analysis.space.is_spacious_enough is False

    async def test_handles_llm_failure_gracefully(
        self,
        sample_property: Property,
        sample_detail_data: DetailPageData,
    ):
        """Should return minimal analysis on LLM failure."""
        with patch.object(DetailFetcher, "fetch_detail_page", return_value=sample_detail_data):
            quality_filter = PropertyQualityFilter(api_key="test-key")
            quality_filter._client = MagicMock()
            quality_filter._client.messages.create = AsyncMock(side_effect=Exception("API error"))

            results = await quality_filter.analyze_properties([sample_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary

    async def test_handles_invalid_json_response(
        self,
        sample_property: Property,
        sample_detail_data: DetailPageData,
    ):
        """Should return minimal analysis on invalid JSON response."""
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text="This is not JSON")]

        with patch.object(DetailFetcher, "fetch_detail_page", return_value=sample_detail_data):
            quality_filter = PropertyQualityFilter(api_key="test-key")
            quality_filter._client = MagicMock()
            quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await quality_filter.analyze_properties([sample_property])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary

    async def test_includes_all_images_in_api_call(
        self,
        sample_property: Property,
        sample_detail_data: DetailPageData,
        sample_llm_response: str,
    ):
        """Should include gallery images and floorplan in API call."""
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text=sample_llm_response)]

        with patch.object(DetailFetcher, "fetch_detail_page", return_value=sample_detail_data):
            quality_filter = PropertyQualityFilter(api_key="test-key", max_images=10)
            quality_filter._client = MagicMock()
            quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

            await quality_filter.analyze_properties([sample_property])

        # Check the API call
        call_args = quality_filter._client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]

        # Should have 3 gallery images + 1 floorplan + 1 text prompt = 5 content blocks
        assert len(content) == 5
        assert content[0]["type"] == "image"
        assert content[3]["type"] == "image"  # floorplan
        assert content[4]["type"] == "text"

    async def test_respects_max_images_limit(
        self,
        sample_property: Property,
        sample_llm_response: str,
    ):
        """Should respect max_images configuration."""
        # Create detail data with many images
        many_images = DetailPageData(
            floorplan_url="https://example.com/floor.jpg",
            gallery_urls=[f"https://example.com/img{i}.jpg" for i in range(20)],
        )
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text=sample_llm_response)]

        with patch.object(DetailFetcher, "fetch_detail_page", return_value=many_images):
            quality_filter = PropertyQualityFilter(api_key="test-key", max_images=5)
            quality_filter._client = MagicMock()
            quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

            await quality_filter.analyze_properties([sample_property])

        call_args = quality_filter._client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]

        # Should have 5 gallery images + 1 floorplan + 1 text prompt = 7 content blocks
        image_blocks = [c for c in content if c.get("type") == "image"]
        assert len(image_blocks) == 6  # 5 gallery + 1 floorplan
