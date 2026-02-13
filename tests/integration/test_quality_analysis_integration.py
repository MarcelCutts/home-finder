"""Integration tests for quality analysis with mocked Anthropic API."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic.types import ToolUseBlock
from pydantic import HttpUrl

from home_finder.db import PropertyStorage
from home_finder.filters.quality import (
    PropertyQualityFilter,
    assess_value,
)
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertySource,
)


# ---------------------------------------------------------------------------
# Shared response data and mock helpers
# ---------------------------------------------------------------------------


def _sample_visual_response() -> dict[str, Any]:
    """Realistic Phase 1 visual analysis response from Claude."""
    return {
        "kitchen": {
            "overall_quality": "modern",
            "hob_type": "gas",
            "has_dishwasher": "yes",
            "has_washing_machine": "yes",
            "notes": "Modern integrated kitchen with gas hob",
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
        "summary": "Bright flat with modern kitchen and good natural light.",
    }


def _sample_evaluation_response() -> dict[str, Any]:
    """Realistic Phase 2 evaluation response from Claude."""
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
        },
        "value_for_quality": {
            "rating": "good",
            "reasoning": "Well-maintained at reasonable price for E8",
        },
        "viewing_notes": {
            "check_items": ["Check water pressure", "Inspect windows"],
            "questions_for_agent": ["Any upcoming rent increases?"],
            "deal_breaker_tests": ["Test hot water"],
        },
        "highlights": ["Gas hob", "Modern kitchen", "Good light"],
        "lowlights": ["No balcony"],
        "one_line": "Bright Victorian flat with modern kitchen in E8",
    }


def _create_mock_response(
    tool_input: dict[str, Any],
    stop_reason: str = "tool_use",
    tool_name: str = "property_visual_analysis",
) -> MagicMock:
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


def _make_two_phase_mock() -> AsyncMock:
    """Create mock that returns Phase 1 then Phase 2 responses."""
    mock_visual = _create_mock_response(
        _sample_visual_response(), tool_name="property_visual_analysis"
    )
    mock_eval = _create_mock_response(
        _sample_evaluation_response(), tool_name="property_evaluation"
    )
    return AsyncMock(side_effect=[mock_visual, mock_eval])


def _make_merged_with_images(
    prop: Property,
    *,
    with_images: bool = True,
    with_floorplan: bool = True,
) -> MergedProperty:
    """Wrap a Property into a MergedProperty with optional images/floorplan."""
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


@pytest.mark.integration
class TestQualityAnalysisIntegration:
    """Test quality analysis with mocked Anthropic API and real DB."""

    async def test_analyze_parses_tool_response(
        self, make_property: Callable[..., Property]
    ) -> None:
        """Two-phase tool use response should be parsed into PropertyQualityAnalysis."""
        prop = make_property(source_id="qa-123", price_pcm=1900, postcode="E8 3RH")
        merged = _make_merged_with_images(prop)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock()

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
        # Phase 2 fields
        assert analysis.listing_extraction is not None
        assert analysis.listing_extraction.epc_rating == "C"
        assert analysis.highlights is not None
        assert "Gas hob" in analysis.highlights
        assert analysis.one_line is not None

    async def test_analysis_stored_in_db(
        self,
        in_memory_storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        """Analysis should roundtrip through the database."""
        prop = make_property(source_id="qa-db", price_pcm=1900, postcode="E8 3RH")
        merged = _make_merged_with_images(prop)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock()

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
        self,
        in_memory_storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        """Analysis should be available through get_property_detail."""
        prop = make_property(source_id="qa-detail", price_pcm=1900, postcode="E8 3RH")
        merged = _make_merged_with_images(prop)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock()

        results = await quality_filter.analyze_merged_properties([merged])
        _, analysis = results[0]

        await in_memory_storage.save_merged_property(merged)
        await in_memory_storage.save_quality_analysis(merged.unique_id, analysis)

        detail = await in_memory_storage.get_property_detail(merged.unique_id)
        assert detail is not None
        assert detail["quality_rating"] == 4
        assert detail["quality_analysis"] is not None
        assert detail["quality_analysis"].summary == analysis.summary

    async def test_two_bed_space_override(
        self, make_property: Callable[..., Property]
    ) -> None:
        """2-bed property should override is_spacious_enough to True."""
        prop = make_property(
            source_id="qa-2bed", bedrooms=2, price_pcm=1900, postcode="E8 3RH"
        )
        merged = _make_merged_with_images(prop)

        visual_data = _sample_visual_response()
        visual_data["space"]["is_spacious_enough"] = False  # Claude says not spacious
        visual_data["space"]["living_room_sqm"] = 15

        mock_visual = _create_mock_response(visual_data, tool_name="property_visual_analysis")
        mock_eval = _create_mock_response(
            _sample_evaluation_response(), tool_name="property_evaluation"
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(side_effect=[mock_visual, mock_eval])

        results = await quality_filter.analyze_merged_properties([merged])
        _, analysis = results[0]

        # Override: 2-bed means spare room for office
        assert analysis.space.is_spacious_enough is True
        assert analysis.space.confidence == "high"

    async def test_no_images_skips_api_call(
        self, make_property: Callable[..., Property]
    ) -> None:
        """Property without images should not call Claude API."""
        prop = make_property(source_id="qa-noimg", price_pcm=1900, postcode="E8 3RH")
        merged = _make_merged_with_images(prop, with_images=False, with_floorplan=False)

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock()

        results = await quality_filter.analyze_merged_properties([merged])

        assert len(results) == 1
        _, analysis = results[0]
        assert "No images available" in analysis.summary
        # API should not be called
        quality_filter._client.messages.create.assert_not_called()

    async def test_zoopla_images_as_base64(
        self, make_property: Callable[..., Property]
    ) -> None:
        """Zoopla CDN images should be downloaded and sent as base64."""
        from unittest.mock import patch

        prop = make_property(source_id="qa-zoopla", price_pcm=1900, postcode="E8 3RH")
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

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = _make_two_phase_mock()

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
        quality_filter._client.messages.create.assert_called()
        # Phase 1 call should have base64 image
        call_args = quality_filter._client.messages.create.call_args_list[0]
        content = call_args.kwargs["messages"][0]["content"]
        image_blocks = [c for c in content if c.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["type"] == "base64"

    async def test_phase2_failure_still_returns_analysis(
        self, make_property: Callable[..., Property]
    ) -> None:
        """Phase 2 failure should still return analysis with visual data."""
        prop = make_property(source_id="qa-p2fail", price_pcm=1900, postcode="E8 3RH")
        merged = _make_merged_with_images(prop)

        mock_visual = _create_mock_response(
            _sample_visual_response(), tool_name="property_visual_analysis"
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        quality_filter._client = MagicMock()
        quality_filter._client.messages.create = AsyncMock(
            side_effect=[mock_visual, Exception("Phase 2 timeout")]
        )

        results = await quality_filter.analyze_merged_properties([merged])

        assert len(results) == 1
        _, analysis = results[0]
        # Visual data present
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.overall_rating == 4
        # Evaluation data absent
        assert analysis.listing_extraction is None
        assert analysis.highlights is None

    def test_value_assessment(self) -> None:
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
