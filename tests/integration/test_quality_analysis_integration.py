"""Integration tests for quality analysis with mocked Anthropic API."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import ToolUseBlock
from pydantic import HttpUrl

from home_finder.db import PropertyStorage
from home_finder.filters.quality import (
    PropertyQualityAnalysis,
    PropertyQualityFilter,
    ValueAnalysis,
    assess_value,
)
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource


def _make_property(
    source_id: str = "123",
    price: int = 1900,
    bedrooms: int = 1,
    postcode: str = "E8 3RH",
) -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id=source_id,
        url=HttpUrl(f"https://www.openrent.com/property/{source_id}"),
        title=f"Test flat {source_id}",
        price_pcm=price,
        bedrooms=bedrooms,
        address=f"{source_id} Test Street, London",
        postcode=postcode,
        latitude=51.5465,
        longitude=-0.0553,
    )


def _make_merged(
    prop: Property,
    with_images: bool = True,
    with_floorplan: bool = True,
) -> MergedProperty:
    images = ()
    floorplan = None
    if with_images:
        images = (
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=prop.source,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img2.jpg"),
                source=prop.source,
                image_type="gallery",
            ),
        )
    if with_floorplan:
        floorplan = PropertyImage(
            url=HttpUrl("https://example.com/floor.jpg"),
            source=prop.source,
            image_type="floorplan",
        )
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        images=images,
        floorplan=floorplan,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


def _sample_tool_response() -> dict[str, Any]:
    """Realistic tool response from Claude."""
    return {
        "kitchen": {
            "overall_quality": "modern",
            "hob_type": "gas",
            "has_dishwasher": True,
            "has_washing_machine": True,
            "notes": "Modern integrated kitchen with gas hob",
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
            "natural_light": "good",
            "window_sizes": "medium",
            "feels_spacious": True,
            "ceiling_height": "standard",
            "notes": "South-facing, bright throughout",
        },
        "space": {
            "living_room_sqm": 22,
            "is_spacious_enough": True,
            "confidence": "high",
        },
        "value_for_quality": {
            "rating": "good",
            "reasoning": "Well-maintained at reasonable price for E8",
        },
        "overall_rating": 4,
        "condition_concerns": False,
        "concern_severity": None,
        "summary": "Bright flat with modern kitchen and good natural light.",
    }


def _create_mock_response(
    tool_input: dict[str, Any], stop_reason: str = "tool_use"
) -> MagicMock:
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


@pytest.mark.integration
class TestQualityAnalysisIntegration:
    """Test quality analysis with mocked Anthropic API and real DB."""

    async def test_analyze_parses_tool_response(self):
        """Tool use response should be parsed into PropertyQualityAnalysis."""
        prop = _make_property()
        merged = _make_merged(prop)

        tool_data = _sample_tool_response()
        mock_response = _create_mock_response(tool_data)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([merged])

        assert len(results) == 1
        returned_merged, analysis = results[0]
        assert returned_merged.unique_id == merged.unique_id
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"
        assert analysis.condition.overall_condition == "good"
        assert analysis.light_space.natural_light == "good"
        assert analysis.space.living_room_sqm == 22
        assert analysis.overall_rating == 4
        assert analysis.condition_concerns is False
        assert "Bright flat" in analysis.summary

    async def test_analysis_stored_in_db(self, in_memory_storage: PropertyStorage):
        """Analysis should roundtrip through the database."""
        prop = _make_property()
        merged = _make_merged(prop)

        tool_data = _sample_tool_response()
        mock_response = _create_mock_response(tool_data)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([merged])
        _, analysis = results[0]

        # Save to DB
        await in_memory_storage.save_merged_property(merged)
        await in_memory_storage.save_quality_analysis(merged.unique_id, analysis)

        # Retrieve from DB
        loaded = await in_memory_storage.get_quality_analysis(merged.unique_id)
        assert loaded is not None
        assert loaded.kitchen.overall_quality == analysis.kitchen.overall_quality
        assert loaded.overall_rating == analysis.overall_rating
        assert loaded.summary == analysis.summary

    async def test_analysis_roundtrip_through_detail(
        self, in_memory_storage: PropertyStorage
    ):
        """Analysis should be available through get_property_detail."""
        prop = _make_property()
        merged = _make_merged(prop)

        tool_data = _sample_tool_response()
        mock_response = _create_mock_response(tool_data)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([merged])
        _, analysis = results[0]

        await in_memory_storage.save_merged_property(merged)
        await in_memory_storage.save_quality_analysis(merged.unique_id, analysis)

        detail = await in_memory_storage.get_property_detail(merged.unique_id)
        assert detail is not None
        assert detail["quality_rating"] == 4
        assert detail["quality_analysis"] is not None
        assert detail["quality_analysis"].summary == analysis.summary

    async def test_two_bed_space_override(self):
        """2-bed property should override is_spacious_enough to True."""
        prop = _make_property(bedrooms=2)
        merged = _make_merged(prop)

        tool_data = _sample_tool_response()
        tool_data["space"]["is_spacious_enough"] = False  # Claude says not spacious
        tool_data["space"]["living_room_sqm"] = 15

        mock_response = _create_mock_response(tool_data)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        results = await quality_filter.analyze_merged_properties([merged])
        _, analysis = results[0]

        # Override: 2-bed means spare room for office
        assert analysis.space.is_spacious_enough is True
        assert analysis.space.confidence == "high"

    async def test_no_images_skips_api_call(self):
        """Property without images should not call Claude API."""
        prop = _make_property()
        merged = _make_merged(prop, with_images=False, with_floorplan=False)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock()

        results = await quality_filter.analyze_merged_properties([merged])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary
        # API should not be called
        quality_filter._client.messages.create.assert_not_called()

    async def test_zoopla_images_as_base64(self):
        """Zoopla CDN images should be downloaded and sent as base64."""
        prop = _make_property()
        merged = MergedProperty(
            canonical=prop,
            sources=(prop.source,),
            source_urls={prop.source: prop.url},
            images=(
                PropertyImage(
                    url=HttpUrl("https://lid.zoocdn.com/u/1024/768/abc123.jpg"),
                    source=PropertySource.ZOOPLA,
                    image_type="gallery",
                ),
            ),
            floorplan=None,
            min_price=prop.price_pcm,
            max_price=prop.price_pcm,
        )

        tool_data = _sample_tool_response()
        mock_response = _create_mock_response(tool_data)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(return_value=mock_response)

        # Mock the curl_cffi download
        mock_curl_resp = MagicMock()
        mock_curl_resp.status_code = 200
        mock_curl_resp.content = b"\x89PNG\r\n\x1a\nfake_image_data"
        mock_curl_resp.headers = {"content-type": "image/jpeg"}

        pytest.importorskip("curl_cffi")

        with patch(
            "home_finder.filters.quality.PropertyQualityFilter._download_image_as_base64",
            new_callable=AsyncMock,
            return_value=("dGVzdA==", "image/jpeg"),
        ):
            results = await quality_filter.analyze_merged_properties([merged])

        assert len(results) == 1
        # Verify the API was called (image was processed)
        quality_filter._client.messages.create.assert_called_once()
        call_args = quality_filter._client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]
        # Should have base64 image block
        image_blocks = [c for c in content if c.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["type"] == "base64"

    def test_value_assessment(self):
        """Value assessment should rate correctly against benchmarks."""
        # E8 1-bed average is 1900
        good_value = assess_value(1700, "E8 3RH", 1)
        assert good_value.rating == "excellent"
        assert good_value.difference is not None and good_value.difference < 0
        assert "below" in good_value.note

        poor_value = assess_value(2200, "E8 3RH", 1)
        assert poor_value.rating == "poor"
        assert poor_value.difference is not None and poor_value.difference > 0
        assert "above" in poor_value.note
