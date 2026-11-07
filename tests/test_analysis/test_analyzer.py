"""PropertyQualityAnalyzer execution with a mocked SDK client.

Covers request construction, phase-1 to phase-2 chaining, deferral when images are
missing, failure handling and the circuit breaker, and result assembly. Wire-level
request bodies are covered with the real SDK transport in test_provider_transport.py.
"""

import asyncio
import copy
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from anthropic.types import ToolUseBlock, Usage
from pydantic import HttpUrl

from home_finder.analysis.analyzer import (  # type: ignore[attr-defined]
    _CIRCUIT_BREAKER_THRESHOLD,
    DELAY_BETWEEN_CALLS,
    APIUnavailableError,
    PropertyQualityAnalyzer,
    _extract_eval_json,
    _sanitize_eval_enums,
)
from home_finder.data.area_context import ACOUSTIC_PROFILES, build_property_context
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertySource,
)
from home_finder.models.spend import SpendDeferred
from home_finder.utils.image_cache import find_cached_file, get_cache_dir
from tests.support.images import populate_image_cache


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
            floorplan_origin="source_declared",
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
            floorplan_origin="source_declared",
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
            "floor_level": "unknown",
            "notes": "South-facing with good light",
        },
        "space": {
            "living_room_sqm": 22,
            "is_spacious_enough": True,
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
            "availability": "yes",
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
            "building_construction": "unknown",
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
        "highlights": ["Gas hob", "Modern kitchen"],
        "lowlights": [],
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
    """Create a mock API response with tool use block (Phase 1)."""
    tool_block = ToolUseBlock(
        id="toolu_123",
        type="tool_use",
        name=tool_name,
        input=tool_input,
    )
    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.stop_reason = stop_reason
    mock_response.usage = Usage(input_tokens=100, output_tokens=50)
    return mock_response


def create_mock_eval_response(eval_data: dict[str, Any]) -> MagicMock:
    """Create a mock messages.create response for Phase 2 (evaluation).

    Returns a response with a text content block containing the JSON-serialized
    eval data, mimicking the ``output_config`` / ``json_schema`` format.
    """
    import json

    mock_response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps(eval_data)
    mock_response.content = [text_block]
    mock_response.stop_reason = "end_turn"
    mock_response.usage = Usage(input_tokens=100, output_tokens=50)
    return mock_response


def _setup_two_phase_mocks(
    client_mock: MagicMock,
    visual_response: dict[str, Any],
    eval_response: dict[str, Any],
) -> None:
    """Set up Phase 1 and Phase 2 mocks on messages.create (both phases use it)."""
    mock_visual = create_mock_response(visual_response, tool_name="property_visual_analysis")
    mock_eval = create_mock_eval_response(eval_response)
    client_mock.messages.create = AsyncMock(side_effect=[mock_visual, mock_eval])


def _status_error(error_type: type[Exception], status_code: int) -> Exception:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    return error_type(message="fixture", response=response, body=None)  # type: ignore[call-arg]


def _visual_content(analyzer: PropertyQualityAnalyzer) -> list[dict[str, Any]]:
    """Content blocks of the phase-1 (visual) request."""
    return analyzer._client.messages.create.call_args_list[0].kwargs["messages"][0]["content"]


def _evaluation_prompt(analyzer: PropertyQualityAnalyzer) -> str:
    """Text of the phase-2 (evaluation) request."""
    return analyzer._client.messages.create.call_args_list[1].kwargs["messages"][0]["content"]


class TestAnalyzeMergedProperties:
    """The batch entry point analyses each property in turn and paces provider calls."""

    async def test_returns_results_in_input_order_and_pauses_after_each_property(
        self,
        sample_property: Property,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        without_images = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=(),
            floorplan=None,
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        with patch("home_finder.analysis.analyzer.asyncio.sleep", new_callable=AsyncMock) as sleep:
            results = await analyzer.analyze_merged_properties(
                [without_images, sample_merged_property], data_dir=str(tmp_path)
            )

        assert len(results) == 2
        assert results[0][0] is without_images and results[0][1] is None
        merged, analysis = results[1]
        assert merged is sample_merged_property
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"
        assert analysis.condition.overall_condition == "good"
        assert analysis.light_space.natural_light == "excellent"
        assert analysis.space.living_room_sqm == 22
        assert sleep.await_args_list == [call(DELAY_BETWEEN_CALLS)] * 2


class TestVisualRequest:
    """The phase-1 request carries the cached images, their labels and the listing text."""

    async def test_includes_all_images_in_phase1_call(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Should include gallery images and floorplan in Phase 1 API call."""
        data_dir = str(tmp_path)
        populate_image_cache(data_dir, sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key", max_images=10)
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(sample_merged_property, data_dir=data_dir)

        content = _visual_content(analyzer)
        # 3 gallery x (label + image) + 1 floorplan x (label + image) + 1 text = 9
        assert len(content) == 9
        assert content[0] == {"type": "text", "text": "Gallery image 1:"}
        assert content[1]["type"] == "image"
        assert content[6]["type"] == "text"
        assert content[6]["text"].startswith("Source-declared floorplan.")
        assert content[7]["type"] == "image"  # floorplan
        assert content[8]["type"] == "text"  # user prompt

    async def test_respects_max_images_limit(
        self,
        sample_property: Property,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """A cached floorplan takes one of max_images; the gallery fills the rest in order."""
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
                floorplan_origin="source_declared",
            ),
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )

        data_dir = str(tmp_path)
        populate_image_cache(data_dir, many_images_merged)

        analyzer = PropertyQualityAnalyzer(api_key="test-key", max_images=5)
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(many_images_merged, data_dir=data_dir)

        content = _visual_content(analyzer)
        assert sum(block["type"] == "image" for block in content) == 5
        labels = [block["text"] for block in content[:-1] if block["type"] == "text"]
        assert labels[:4] == [f"Gallery image {n}:" for n in range(1, 5)]
        assert len(labels) == 5 and labels[4].startswith("Source-declared floorplan.")

    async def test_pdf_floorplan_is_not_sent_and_leaves_gallery_its_allowance(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """The vision API cannot read a PDF floorplan, so every image slot goes to the gallery."""
        assert sample_merged_property.floorplan is not None
        merged = sample_merged_property.model_copy(
            update={
                "floorplan": sample_merged_property.floorplan.model_copy(
                    update={"url": HttpUrl("https://example.com/FLOORPLAN.PDF")}
                )
            }
        )
        data_dir = str(tmp_path)
        populate_image_cache(data_dir, merged)
        # A decodable file is cached under the PDF URL: only the URL check can reject it.
        assert find_cached_file(data_dir, merged.unique_id, str(merged.floorplan.url), "floorplan")

        analyzer = PropertyQualityAnalyzer(api_key="test-key", max_images=3)
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(merged, data_dir=data_dir)

        content = _visual_content(analyzer)
        assert sum(block["type"] == "image" for block in content) == 3
        labels = [block["text"] for block in content[:-1] if block["type"] == "text"]
        assert labels == ["Gallery image 1:", "Gallery image 2:", "Gallery image 3:"]

    async def test_uses_cached_system_prompt(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Should use system prompt with cache_control for cost savings."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))

        # Phase 1 system prompt (messages.create)
        create_calls = analyzer._client.messages.create.call_args_list
        system1 = create_calls[0].kwargs["system"]
        assert len(system1) == 1
        assert system1[0]["type"] == "text"
        assert system1[0]["cache_control"] == {"type": "ephemeral"}
        assert (
            "London rental property analyst assessing limited marketing evidence"
            in system1[0]["text"]
        )

        # Phase 2 system prompt (messages.create, second call)
        assert len(create_calls) == 2
        system2 = create_calls[1].kwargs["system"]
        assert len(system2) == 1
        assert system2[0]["type"] == "text"
        assert system2[0]["cache_control"] == {"type": "ephemeral"}
        assert "expert London rental property evaluator" in system2[0]["text"]

    async def test_includes_description_in_prompt(
        self,
        sample_property: Property,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
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
                floorplan_origin="source_declared",
            ),
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
            descriptions={
                PropertySource.RIGHTMOVE: "Spacious flat with modern kitchen and gas hob."
            },
        )

        populate_image_cache(str(tmp_path), merged)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))

        # The user prompt is the last text block, after the image labels.
        prompt_text = _visual_content(analyzer)[-1]["text"]

        # Verify description is included (XML format)
        assert "<listing_description>" in prompt_text
        assert "gas hob" in prompt_text

    async def test_outcode_area_context_reaches_both_requests(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """The canonical postcode's area research is offered to both phases."""
        canonical = sample_merged_property.canonical.model_copy(update={"postcode": "E8 2LX"})
        merged = sample_merged_property.model_copy(update={"canonical": canonical})
        overview = build_property_context("E8 2LX", canonical.bedrooms).area_overview
        assert overview
        populate_image_cache(str(tmp_path), merged)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))

        for prompt in (_visual_content(analyzer)[-1]["text"], _evaluation_prompt(analyzer)):
            assert f'<area_context outcode="E8">\n{overview}' in prompt


class TestFloorplanNoteWiring:
    """Tests that has_labeled_floorplan flows from _analyze_property to the prompt."""

    async def test_unknown_legacy_image_is_sent_without_floorplan_authority(
        self, sample_merged_property, sample_visual_response, sample_evaluation_response, tmp_path
    ):
        merged = sample_merged_property.model_copy(
            update={
                "floorplan": sample_merged_property.floorplan.model_copy(
                    update={"floorplan_origin": "unknown"}
                )
            }
        )
        populate_image_cache(str(tmp_path), merged)
        analyzer = PropertyQualityAnalyzer(api_key="test-key", max_images=3)
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)
        await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))
        content = _visual_content(analyzer)
        assert sum(block["type"] == "image" for block in content) == 3
        labels = [block["text"] for block in content[:-1] if block["type"] == "text"]
        assert labels[-1] == "Gallery image 3 (historical image role unverified):"
        assert not any("Source-declared floorplan" in label for label in labels)
        assert "<floorplan_note>" in content[-1]["text"]
        assert "actually readable" in content[-1]["text"]

    async def test_missing_cached_floorplan_uses_gallery_slot_and_includes_note(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        data_dir = str(tmp_path)
        populate_image_cache(data_dir, sample_merged_property)
        floorplan_path = find_cached_file(
            data_dir,
            sample_merged_property.unique_id,
            str(sample_merged_property.floorplan.url),
            "floorplan",
        )
        assert floorplan_path is not None
        floorplan_path.unlink()
        analyzer = PropertyQualityAnalyzer(api_key="test-key", max_images=3)
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        _, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=data_dir
        )

        assert analysis is not None and analysis.overall_rating == 4
        content = _visual_content(analyzer)
        assert sum(block["type"] == "image" for block in content) == 3
        assert "<floorplan_note>" in content[-1]["text"]

    async def test_no_floorplan_includes_note_in_api_call(
        self,
        sample_property: Property,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
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

        populate_image_cache(str(tmp_path), merged)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))

        prompt_text = _visual_content(analyzer)[-1]["text"]
        assert "<floorplan_note>" in prompt_text
        assert "floorplan_detected_in_gallery" in prompt_text

    async def test_with_floorplan_excludes_note_in_api_call(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Property with floorplan → Phase 1 prompt does NOT contain <floorplan_note>."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))

        assert "<floorplan_note>" not in _visual_content(analyzer)[-1]["text"]


class TestPhaseChaining:
    """Phase 2 evaluates phase 1's observations; its own output completes the assessment."""

    async def test_phase1_output_passed_to_phase2(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Phase 1 JSON output should appear in Phase 2 prompt."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))

        assert analyzer._client.messages.create.await_count == 2
        phase2_content = _evaluation_prompt(analyzer)
        assert "<visual_analysis>" in phase2_content
        assert '"overall_quality": "modern"' in phase2_content
        assert "</visual_analysis>" in phase2_content

    @pytest.mark.parametrize(
        ("construction", "profile"),
        [
            ("timber_frame", "victorian"),
            ("concrete", "ex_council"),
            ("solid_brick", "purpose_built"),
        ],
    )
    async def test_building_construction_selects_acoustic_profile_for_evaluation(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
        construction: str,
        profile: str,
    ) -> None:
        """Phase 1's building construction selects the acoustic profile shown to phase 2."""
        visual = copy.deepcopy(sample_visual_response)
        visual["flooring_noise"]["building_construction"] = construction
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, visual, sample_evaluation_response)

        await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))

        prompt = _evaluation_prompt(analyzer)
        block = prompt.split("<acoustic_context>\n", 1)[1].split("\n</acoustic_context>", 1)[0]
        assert block.startswith(f"Building construction: {construction}\n")
        assert (
            f"Typical sound insulation: {ACOUSTIC_PROFILES[profile]['airborne_insulation_db']} "
            "airborne\n"
        ) in block
        assert "dB dB" not in block
        assert f"Hosting safety: {ACOUSTIC_PROFILES[profile]['hosting_safety']}" in block
        assert block.endswith(ACOUSTIC_PROFILES[profile]["summary"])

    @pytest.mark.parametrize("construction", ["mixed", "unknown"])
    async def test_unmapped_building_construction_omits_acoustic_context(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
        construction: str,
    ) -> None:
        visual = copy.deepcopy(sample_visual_response)
        visual["flooring_noise"]["building_construction"] = construction
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, visual, sample_evaluation_response)

        await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))

        assert "<acoustic_context>" not in _evaluation_prompt(analyzer)

    async def test_quality_adjusted_value_comes_from_evaluation(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """The quality-adjusted value rating and note come from the phase-2 output."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        _, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=str(tmp_path)
        )

        assert analysis.value is not None
        assert analysis.value.quality_adjusted_rating == "good"
        assert "Well-maintained" in analysis.value.quality_adjusted_note

    async def test_assembles_work_hosting_and_broadband_evidence(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Work, hosting, and broadband fields pass from API response to PropertyQualityAnalysis."""
        visual = copy.deepcopy(sample_visual_response)
        visual["space"]["hosting_layout"] = "excellent"
        visual["flooring_noise"]["hosting_noise_risk"] = "low"
        eval_resp = copy.deepcopy(sample_evaluation_response)
        eval_resp["listing_extraction"]["broadband_type"] = "fttp"

        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, visual, eval_resp)

        _, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=str(tmp_path)
        )

        assert analysis.bedroom is not None
        assert analysis.bedroom.office_separation == "dedicated_room"
        assert analysis.space.hosting_layout == "excellent"
        assert analysis.flooring_noise is not None
        assert analysis.flooring_noise.hosting_noise_risk == "low"
        assert analysis.listing_extraction is not None
        assert analysis.listing_extraction.broadband_type == "fttp"


class TestAnalyzeSingleMerged:
    """Tests for analyze_single_merged method."""

    async def test_detail_text_survives_save_reload_and_reaches_both_requests(
        self, make_property, sample_visual_response, sample_evaluation_response
    ) -> None:
        from home_finder.db import PropertyStorage
        from home_finder.enrichment.details import enrich_merged_properties
        from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData

        rm = make_property(source=PropertySource.RIGHTMOVE, source_id="rm-text", description=None)
        otm = make_property(
            source=PropertySource.ONTHEMARKET, source_id="otm-text", description=None
        )
        merged = MergedProperty(
            canonical=rm,
            sources=(rm.source, otm.source),
            source_urls={rm.source: rm.url, otm.source: otm.url},
            min_price=rm.price_pcm,
            max_price=rm.price_pcm,
        )
        details = {
            rm.source: DetailPageData(
                description="East-facing café-style kitchen <script>fixture</script>",
                features=["Gas hob", "Private balcony"],
                gallery_urls=["https://example.com/rm-fixture.jpg"],
            ),
            otm.source: DetailPageData(
                description="West-facing kitchen; verify the conflicting orientation",
                features=["Induction hob", "Shared garden"],
                gallery_urls=["https://example.com/otm-fixture.jpg"],
            ),
        }

        async def fetch(prop):
            return details[prop.source]

        async with PropertyStorage(":memory:") as storage:
            await storage.upsert_source_listings([rm, otm])
            async with DetailFetcher() as fetcher:
                with patch.object(fetcher, "fetch_detail_page", side_effect=fetch):
                    result = await enrich_merged_properties([merged], fetcher)
            enriched = result.with_usable_media[0]
            await storage.processing.save_pre_analysis_properties([enriched], {})

            # Both the next sparse search and aggregate rebuild must retain text.
            await storage.upsert_source_listings([rm, otm])
            await storage.update_merged_sources(merged.unique_id, merged)
            reloaded = (await storage.processing.get_pending_analysis_properties())[0]
            assert reloaded.source_text[rm.source].features == ("Gas hob", "Private balcony")
            assert reloaded.source_text[otm.source].description == details[otm.source].description
            async with storage._read_connection() as conn:
                rows = await (
                    await conn.execute("SELECT source, description, text_json FROM source_listings")
                ).fetchall()
            assert {row["description"] for row in rows} == {d.description for d in details.values()}
            assert all(row["text_json"] for row in rows)

            analyzer = PropertyQualityAnalyzer(api_key="fixture-no-live-calls")
            analyzer._client = MagicMock()
            _setup_two_phase_mocks(
                analyzer._client, sample_visual_response, sample_evaluation_response
            )
            with patch.object(
                analyzer,
                "_build_image_block",
                new_callable=AsyncMock,
                return_value={
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "Zml4dHVyZQ==",
                    },
                },
            ):
                _, analysis = await analyzer.analyze_single_merged(reloaded)
            assert analysis is not None and analysis.listing_text_inputs is not None
            requests = analyzer._client.messages.create.call_args_list
            assert len(requests) == 2
            for request in requests:
                content = request.kwargs["messages"][0]["content"]
                prompt = (
                    content
                    if isinstance(content, str)
                    else "\n".join(block["text"] for block in content if block["type"] == "text")
                )
                assert "East-facing café-style" in prompt
                assert "West-facing kitchen" in prompt
                assert "Gas hob" in prompt and "Induction hob" in prompt
                assert "[Rightmove]" in prompt and "[OnTheMarket]" in prompt
                assert "Band C est." not in prompt
                assert "True monthly cost" not in prompt
                assert "<script>fixture</script>" not in prompt
                assert analysis.listing_text_inputs.description in prompt
            # The fixture model returns Band C, but neither listing states a band.
            assert analysis.listing_extraction.council_tax_band == "unknown"
            assert analysis.listing_extraction.epc_rating == "unknown"
            await storage.processing.complete_analysis(reloaded.unique_id, analysis)
            saved = await storage.get_quality_analysis(reloaded.unique_id)
            assert saved.listing_text_inputs == analysis.listing_text_inputs

    async def test_with_images_returns_analysis(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Should return (merged, analysis) when every gallery image is cached."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        merged, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=str(tmp_path)
        )

        assert merged is sample_merged_property
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"
        assert analysis.condition.overall_condition == "good"
        assert analysis.light_space.natural_light == "excellent"
        assert analysis.space.living_room_sqm == 22
        assert analysis.summary == (
            "Well-maintained flat with modern kitchen. Living room suits home office."
        )

    async def test_handles_end_turn_with_tool_use(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Should handle end_turn stop_reason when tool_use block is present."""
        mock_visual = create_mock_response(
            sample_visual_response, stop_reason="end_turn", tool_name="property_visual_analysis"
        )

        mock_eval = create_mock_eval_response(sample_evaluation_response)

        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(side_effect=[mock_visual, mock_eval])

        _, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=str(tmp_path)
        )

        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"

    @pytest.mark.parametrize(
        "merged_fixture",
        ["one_bed_merged_property", "sample_merged_property"],
        ids=["one_bed", "two_bed"],
    )
    async def test_space_judgement_is_not_overridden_by_bedroom_count(
        self,
        request: pytest.FixtureRequest,
        merged_fixture: str,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """The visual phase's space judgement is kept whatever the number of bedrooms."""
        merged = request.getfixturevalue(merged_fixture)
        visual_response = copy.deepcopy(sample_visual_response)
        visual_response["space"].update(living_room_sqm=15, is_spacious_enough=False)
        visual_response.update(overall_rating=3, summary="Compact living room")

        populate_image_cache(str(tmp_path), merged)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, visual_response, sample_evaluation_response)

        _, analysis = await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))

        assert analysis.space.is_spacious_enough is False
        assert analysis.space.living_room_sqm == 15

    async def test_without_images_defers(
        self,
        sample_property: Property,
    ) -> None:
        """Should defer when no images are available."""
        merged = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=(),
            floorplan=None,
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        result_merged, analysis = await analyzer.analyze_single_merged(merged)

        assert result_merged is merged
        assert analysis is None


class TestInsufficientImageSkip:
    """Analysis is deferred, without a provider request, while gallery images are uncached."""

    async def test_skips_analysis_when_images_uncached(
        self,
        sample_merged_property: MergedProperty,
        tmp_path: Any,
    ) -> None:
        """Should return None analysis when no gallery image is cached."""
        data_dir = str(tmp_path)

        # Nothing is cached under data_dir.
        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        merged, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=data_dir
        )

        assert merged is sample_merged_property
        assert analysis is None
        analyzer._client.messages.create.assert_not_called()

    async def test_defers_when_data_dir_not_set(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """An unset cache directory cannot justify a paid text-only visual call."""
        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)

        with pytest.raises(SpendDeferred, match="missing_usable_images"):
            await analyzer.analyze_single_merged(sample_merged_property)
        analyzer._client.messages.create.assert_not_called()

    async def test_skips_when_partial_cache(
        self,
        sample_property: Property,
        tmp_path: Any,
    ) -> None:
        """Should skip when only some images are cached (partial enrichment)."""
        from io import BytesIO

        from PIL import Image

        from home_finder.utils.image_cache import get_cached_image_path, save_image_bytes

        # Create a merged property with 5 gallery images
        images = tuple(
            PropertyImage(
                url=HttpUrl(f"https://example.com/img{i}.jpg"),
                source=sample_property.source,
                image_type="gallery",
            )
            for i in range(5)
        )
        merged = MergedProperty(
            canonical=sample_property,
            sources=(sample_property.source,),
            source_urls={sample_property.source: sample_property.url},
            images=images,
            floorplan=None,
            min_price=sample_property.price_pcm,
            max_price=sample_property.price_pcm,
        )

        data_dir = str(tmp_path)

        # Cache only 3 of the 5 images
        buf = BytesIO()
        Image.new("RGB", (10, 10), color="red").save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()

        for idx in range(3):
            path = get_cached_image_path(
                data_dir, merged.unique_id, str(images[idx].url), "gallery", idx
            )
            save_image_bytes(path, jpeg_bytes)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        merged_out, analysis = await analyzer.analyze_single_merged(merged, data_dir=data_dir)

        assert merged_out is merged
        assert analysis is None
        analyzer._client.messages.create.assert_not_called()


class TestFailureHandling:
    """Non-outage failures become unrated assessments that record the failure."""

    async def test_unexpected_visual_error_returns_unrated_internal_failure(
        self,
        sample_merged_property: MergedProperty,
        tmp_path: Any,
    ) -> None:
        """An unexpected exception in phase 1 is recorded, not raised."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(side_effect=Exception("API error"))

        merged, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=str(tmp_path)
        )

        assert merged is sample_merged_property
        assert "Quality analysis incomplete" in analysis.summary
        assert analysis.overall_rating is None
        assert analysis.analysis_failure.phase == "visual"
        assert analysis.analysis_failure.kind == "internal_error"
        assert analysis.space.is_spacious_enough is None

    async def test_phase2_failure_cannot_complete_analysis(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """A failed required evaluation never becomes a completed visual assessment."""
        mock_visual = create_mock_response(
            sample_visual_response, tool_name="property_visual_analysis"
        )

        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        # Phase 1 succeeds, Phase 2 raises
        analyzer._client.messages.create = AsyncMock(
            side_effect=[mock_visual, Exception("Phase 2 API error")]
        )

        _, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=str(tmp_path)
        )

        assert analysis.overall_rating is None
        assert analysis.analysis_failure.phase == "evaluation"
        assert analysis.analysis_failure.kind == "internal_error"
        assert analysis.kitchen.overall_quality == "unknown"
        assert analysis.condition.overall_condition == "unknown"
        assert "Quality analysis incomplete" in analysis.summary
        # Evaluation data should be absent/default
        assert analysis.listing_extraction is None
        assert analysis.viewing_notes is None
        assert analysis.highlights is None
        assert analysis.lowlights is None
        assert analysis.one_line is None

    async def test_bad_request_media_type_clears_cache(
        self,
        sample_merged_property: MergedProperty,
        tmp_path: Any,
    ) -> None:
        """An image-format rejection deletes the property's cached images for re-enrichment."""
        data_dir = str(tmp_path)
        populate_image_cache(data_dir, sample_merged_property)
        assert get_cache_dir(data_dir, sample_merged_property.unique_id).is_dir()

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(
            side_effect=BadRequestError(
                message="image specified using image/png media_type, but appears to be image/jpeg",
                response=MagicMock(status_code=400, headers={}),
                body=None,
            )
        )

        _, analysis = await analyzer.analyze_single_merged(
            sample_merged_property, data_dir=data_dir
        )

        assert analysis.analysis_failure.kind == "request_rejected"
        assert not get_cache_dir(data_dir, sample_merged_property.unique_id).exists()


class TestCircuitBreaker:
    """Which provider errors count toward the analyzer's breaker, and what an open one stops."""

    @pytest.mark.parametrize(
        "make_error",
        [
            pytest.param(lambda: APIConnectionError(request=MagicMock()), id="connection"),
            pytest.param(lambda: APITimeoutError(request=MagicMock()), id="timeout"),
            pytest.param(lambda: _status_error(RateLimitError, 429), id="rate_limit"),
            pytest.param(lambda: _status_error(InternalServerError, 500), id="server_error"),
            pytest.param(lambda: _status_error(AuthenticationError, 401), id="authentication"),
        ],
    )
    async def test_outage_errors_open_circuit_and_stop_requests(
        self,
        sample_merged_property: MergedProperty,
        tmp_path: Any,
        make_error: Any,
    ) -> None:
        """Each outage error counts; once the circuit opens, the next analysis sends no request."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(side_effect=make_error())

        for attempt in range(1, _CIRCUIT_BREAKER_THRESHOLD + 1):
            assert not analyzer._breaker.is_open()
            with pytest.raises(APIUnavailableError):
                await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))
            assert analyzer._breaker.failure_count == attempt
        assert analyzer._breaker.is_open()

        with pytest.raises(APIUnavailableError, match="Circuit breaker is open"):
            await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))
        assert analyzer._client.messages.create.await_count == _CIRCUIT_BREAKER_THRESHOLD

    async def test_success_resets_failure_counter(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Successful API call should reset the consecutive failure counter."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()

        # First: fail twice (below threshold)
        analyzer._client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD - 1):
            with pytest.raises(APIUnavailableError):
                await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))

        assert analyzer._breaker.failure_count == _CIRCUIT_BREAKER_THRESHOLD - 1
        assert not analyzer._breaker.is_open()

        # Then: succeed — counter should reset
        _setup_two_phase_mocks(analyzer._client, sample_visual_response, sample_evaluation_response)
        await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))

        assert analyzer._breaker.failure_count == 0
        assert not analyzer._breaker.is_open()

    async def test_bad_request_does_not_trip_circuit(
        self,
        sample_merged_property: MergedProperty,
        tmp_path: Any,
    ) -> None:
        """BadRequestError should NOT count toward circuit breaker."""
        data_dir = str(tmp_path)
        populate_image_cache(data_dir, sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(
            side_effect=_status_error(BadRequestError, 400)
        )

        # Exceed threshold — circuit should NOT open (BadRequestError is request-specific)
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD + 1):
            _merged, analysis = await analyzer.analyze_single_merged(
                sample_merged_property, data_dir=data_dir
            )
            assert "Quality analysis incomplete" in analysis.summary
            assert analysis.overall_rating is None
            assert analysis.analysis_failure.kind == "request_rejected"

        assert not analyzer._breaker.is_open()
        assert analyzer._breaker.failure_count == 0
        # A rejection that does not mention the image format keeps the cached images.
        assert get_cache_dir(data_dir, sample_merged_property.unique_id).is_dir()

    async def test_generic_exception_does_not_trip_circuit(
        self,
        sample_merged_property: MergedProperty,
        tmp_path: Any,
    ) -> None:
        """Generic exceptions should NOT count toward circuit breaker."""
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(side_effect=ValueError("unexpected error"))

        for _ in range(_CIRCUIT_BREAKER_THRESHOLD + 1):
            _merged, analysis = await analyzer.analyze_single_merged(
                sample_merged_property, data_dir=str(tmp_path)
            )
            assert "Quality analysis incomplete" in analysis.summary
            assert analysis.overall_rating is None
            assert analysis.analysis_failure.kind == "internal_error"

        assert not analyzer._breaker.is_open()
        assert analyzer._breaker.failure_count == 0

    @pytest.mark.parametrize(
        ("make_error", "message"),
        [
            pytest.param(
                lambda: _status_error(AuthenticationError, 401),
                "Authentication failed",
                id="authentication",
            ),
            pytest.param(
                lambda: APIConnectionError(request=MagicMock()), "Connection error", id="connection"
            ),
        ],
    )
    async def test_phase2_outage_raises_api_unavailable_and_counts_failure(
        self,
        sample_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        tmp_path: Any,
        make_error: Any,
        message: str,
    ) -> None:
        """Phase 2 outages propagate like phase 1's instead of becoming a failed assessment."""
        mock_visual = create_mock_response(
            sample_visual_response, tool_name="property_visual_analysis"
        )
        populate_image_cache(str(tmp_path), sample_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(side_effect=[mock_visual, make_error()])

        with pytest.raises(APIUnavailableError, match=message):
            await analyzer.analyze_single_merged(sample_merged_property, data_dir=str(tmp_path))
        # The visual success reset the counter; the evaluation outage counts once.
        assert analyzer._breaker.failure_count == 1

    async def test_circuit_opened_between_phases_stops_evaluation_request(
        self,
        sample_merged_property: MergedProperty,
        one_bed_merged_property: MergedProperty,
        sample_visual_response: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """Concurrent analyses can open the circuit while one property is between phases.

        Reused visual observations reach phase 2 without a phase-1 success resetting the
        breaker, so the evaluation is checked against the circuit again.
        """
        data_dir = str(tmp_path)
        populate_image_cache(data_dir, sample_merged_property)
        populate_image_cache(data_dir, one_bed_merged_property)

        analyzer = PropertyQualityAnalyzer(api_key="test-key")
        analyzer._client = MagicMock()
        analyzer._client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        reading_checkpoint = asyncio.Event()
        release_checkpoint = asyncio.Event()

        async def visual_checkpoint(property_id: str, input_sha256: str) -> dict[str, Any] | None:
            # Stands in for the ledger read that returns saved visual observations;
            # the other property has none and sends its own visual request.
            if property_id != sample_merged_property.unique_id:
                return None
            reading_checkpoint.set()
            await release_checkpoint.wait()
            return copy.deepcopy(sample_visual_response)

        with patch.object(analyzer._spend_meter, "get_visual_checkpoint", new=visual_checkpoint):
            reused = asyncio.create_task(
                analyzer.analyze_single_merged(sample_merged_property, data_dir=data_dir)
            )
            await reading_checkpoint.wait()
            for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
                with pytest.raises(APIUnavailableError):
                    await analyzer.analyze_single_merged(one_bed_merged_property, data_dir=data_dir)
            assert analyzer._breaker.is_open()
            release_checkpoint.set()

            with pytest.raises(APIUnavailableError, match="opened before evaluation"):
                await reused

        # Only the other property's failed visual requests were sent.
        assert analyzer._client.messages.create.await_count == _CIRCUIT_BREAKER_THRESHOLD


class TestResultAssembly:
    """_merge_analysis_results reads historical output shapes through the storage models.

    Live output is validated against the response models before assembly; these cases
    exercise the tolerant reader that assembly delegates to.
    """

    def test_historical_one_line_wrapped_in_object(
        self,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Historical stored output retains bounded one_line unwrapping."""
        sample_evaluation_response["one_line"] = {"one_line": "Bright flat with balcony"}
        analysis = PropertyQualityAnalyzer._merge_analysis_results(
            sample_visual_response, sample_evaluation_response, 2, "fixture:legacy"
        )
        assert analysis is not None and analysis.one_line == "Bright flat with balcony"

    def test_historical_nullable_fields_remain_readable(
        self,
        sample_visual_response_with_nulls: dict[str, Any],
        sample_evaluation_response_with_nulls: dict[str, Any],
    ) -> None:
        """Nullable legacy output remains readable outside the live API boundary."""
        analysis = PropertyQualityAnalyzer._merge_analysis_results(
            sample_visual_response_with_nulls,
            sample_evaluation_response_with_nulls,
            1,
            "fixture:legacy",
        )
        assert analysis is not None

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

        assert analysis.concern_severity == "none"

    def test_multiple_stringified_nested_objects(
        self,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """Nested objects stored as JSON strings are all parsed."""
        sample_visual_response["bedroom"] = json.dumps(sample_visual_response["bedroom"])
        sample_visual_response["kitchen"] = json.dumps(sample_visual_response["kitchen"])

        analysis = PropertyQualityAnalyzer._merge_analysis_results(
            sample_visual_response,
            sample_evaluation_response,
            bedrooms=2,
            property_id="test-123",
        )

        assert analysis is not None
        assert analysis.bedroom is not None
        assert analysis.bedroom.primary_is_double == "yes"
        assert analysis.bedroom.office_separation == "dedicated_room"
        assert analysis.kitchen is not None
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"

    def test_historical_sub_model_reader_handles_nul_bytes(
        self,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """The stored-row reader repairs control characters in sub-model JSON."""
        bedroom_data = sample_visual_response["bedroom"]
        bedroom_json_with_ctrl = json.dumps(bedroom_data).replace('"yes"', '"yes\x00"', 1)
        sample_visual_response["bedroom"] = bedroom_json_with_ctrl

        analysis = PropertyQualityAnalyzer._merge_analysis_results(
            sample_visual_response,
            sample_evaluation_response,
            bedrooms=2,
            property_id="test-defense",
        )
        assert analysis is not None
        assert analysis.bedroom is not None
        assert analysis.bedroom.primary_is_double == "yes"
        assert analysis.bedroom.office_separation == "dedicated_room"

    def test_out_of_vocabulary_literal_falls_back_during_assembly(
        self,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """An office_separation outside the Literal ('poor') reads as 'unknown'."""
        sample_visual_response["bedroom"]["office_separation"] = "poor"

        analysis = PropertyQualityAnalyzer._merge_analysis_results(
            sample_visual_response,
            sample_evaluation_response,
            bedrooms=2,
            property_id="test-coerce",
        )

        assert analysis is not None
        assert analysis.bedroom is not None
        assert analysis.bedroom.office_separation == "unknown"

    def test_merge_analysis_repairs_set_literal(
        self,
        sample_visual_response: dict[str, Any],
        sample_evaluation_response: dict[str, Any],
    ) -> None:
        """A bedroom object in set-literal syntax is repaired during assembly."""
        sample_visual_response["bedroom"] = (
            '{"primary_is_double", "has_built_in_wardrobe": "no",'
            ' "can_fit_desk": "yes", "office_separation": "shared_space",'
            ' "notes": "Decent room"}'
        )

        analysis = PropertyQualityAnalyzer._merge_analysis_results(
            sample_visual_response,
            sample_evaluation_response,
            bedrooms=2,
            property_id="test-set-literal",
        )

        assert analysis is not None
        assert analysis.bedroom is not None
        assert analysis.bedroom.can_fit_desk == "yes"
        assert analysis.bedroom.office_separation == "shared_space"


class TestEvalHelpers:
    """Tests for _extract_eval_json and _sanitize_eval_enums."""

    def test_extract_eval_json_from_text_block(self) -> None:
        """Should extract JSON dict from a text content block."""
        raw_data = {"one_line": "Nice flat", "highlights": ["Gas hob"]}
        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = json.dumps(raw_data)
        mock_response.content = [text_block]

        result = _extract_eval_json(mock_response)
        assert result == raw_data

    def test_extract_eval_json_returns_none_when_no_content(self) -> None:
        """Should return None when response has no content blocks."""
        mock_response = MagicMock()
        mock_response.content = []
        assert _extract_eval_json(mock_response) is None

    def test_extract_eval_json_returns_none_on_none_response(self) -> None:
        """Should return None when response is None (no content attr)."""
        assert _extract_eval_json(None) is None

    def test_sanitize_eval_enums_filters_invalid_lowlights(self) -> None:
        """Invalid lowlight values should be stripped, valid ones kept."""
        raw = {
            "highlights": ["Gas hob", "Modern kitchen"],
            "lowlights": ["No dishwasher", "Totally invented concern"],
        }
        _sanitize_eval_enums(raw)
        assert "No dishwasher" in raw["lowlights"]
        assert "Totally invented concern" not in raw["lowlights"]
        assert len(raw["lowlights"]) == 1

    def test_sanitize_eval_enums_filters_invalid_highlights(self) -> None:
        """Invalid highlight values should be stripped."""
        raw = {
            "highlights": ["Gas hob", "Amazing imaginary feature"],
            "lowlights": [],
        }
        _sanitize_eval_enums(raw)
        assert "Gas hob" in raw["highlights"]
        assert "Amazing imaginary feature" not in raw["highlights"]

    def test_sanitize_eval_enums_no_keys(self) -> None:
        """Should be a no-op when highlights/lowlights keys are absent."""
        raw: dict[str, Any] = {"one_line": "Nice flat"}
        _sanitize_eval_enums(raw)
        assert raw == {"one_line": "Nice flat"}


class TestClientLifecycle:
    async def test_close_only_closes_anthropic_client(self) -> None:
        """Once the SDK client exists it owns the transport; close() does not close it twice."""
        http_client = AsyncMock()
        analyzer = PropertyQualityAnalyzer(api_key="test-key", http_client=http_client)
        sdk_client = AsyncMock()
        analyzer._client = sdk_client

        await analyzer.close()

        sdk_client.close.assert_awaited_once()
        http_client.aclose.assert_not_awaited()
        assert analyzer._client is None
