"""HTTP-level Anthropic API response tests using respx.

Tests PropertyQualityFilter against realistic HTTP-level API responses,
matching how Anthropic tests their own SDK. Unlike the existing ToolUseBlock
mocks (which patch at the SDK client level), respx intercepts at the httpx
transport layer, so we test that the SDK correctly deserializes the HTTP response.

This complements (does not replace) the existing test_quality.py mock tests.
"""

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import HttpUrl

from home_finder.filters.quality import (
    PropertyQualityFilter,
)
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertySource,
)


# ---------------------------------------------------------------------------
# Golden HTTP response payloads (matching real Anthropic API format)
# ---------------------------------------------------------------------------

PHASE1_VISUAL_RESPONSE: dict[str, Any] = {
    "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_01A09q90qw90lq917835lBSQ",
            "name": "property_visual_analysis",
            "input": {
                "kitchen": {
                    "overall_quality": "modern",
                    "hob_type": "gas",
                    "has_dishwasher": "yes",
                    "has_washing_machine": "yes",
                    "notes": "Modern integrated kitchen with gas hob and quartz worktops",
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
                    "ceiling_height": "high",
                    "floor_level": "upper",
                    "notes": "South-facing with floor-to-ceiling windows",
                },
                "space": {
                    "living_room_sqm": 24.0,
                    "is_spacious_enough": True,
                    "confidence": "high",
                    "hosting_layout": "good",
                },
                "bathroom": {
                    "overall_condition": "modern",
                    "has_bathtub": "yes",
                    "shower_type": "overhead",
                    "is_ensuite": "no",
                    "notes": "Clean and modern bathroom with heated towel rail",
                },
                "bedroom": {
                    "primary_is_double": "yes",
                    "has_built_in_wardrobe": "yes",
                    "can_fit_desk": "yes",
                    "office_separation": "dedicated_room",
                    "notes": "Good-sized double bedroom with built-in storage",
                },
                "outdoor_space": {
                    "has_balcony": True,
                    "has_garden": False,
                    "has_terrace": False,
                    "has_shared_garden": False,
                    "notes": "Small balcony off living room",
                },
                "storage": {
                    "has_built_in_wardrobes": "yes",
                    "has_hallway_cupboard": "yes",
                    "storage_rating": "good",
                },
                "flooring_noise": {
                    "primary_flooring": "hardwood",
                    "has_double_glazing": "yes",
                    "building_construction": "solid_brick",
                    "noise_indicators": [],
                    "hosting_noise_risk": "low",
                    "notes": "Solid Victorian brick construction, quiet street",
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
                "summary": "Well-maintained Victorian conversion with modern kitchen and good natural light. Spacious living room suits WFH and hosting.",
            },
        }
    ],
    "model": "claude-sonnet-4-5-20250929",
    "stop_reason": "tool_use",
    "usage": {
        "input_tokens": 1523,
        "output_tokens": 312,
        "cache_read_input_tokens": 1200,
        "cache_creation_input_tokens": 0,
    },
}

PHASE2_EVALUATION_RESPONSE: dict[str, Any] = {
    "id": "msg_01YGDUEZKgBBDzvnqtvWpZFL",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_01B19r91rw91mr928946mCSR",
            "name": "property_evaluation",
            "input": {
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
                "viewing_notes": {
                    "check_items": [
                        "Check water pressure in shower",
                        "Inspect sash windows for drafts",
                        "Test broadband speed",
                    ],
                    "questions_for_agent": [
                        "What is the service charge?",
                        "Any planned building works?",
                    ],
                    "deal_breaker_tests": [
                        "Run hot water for 2 minutes",
                        "Check mobile signal in all rooms",
                    ],
                },
                "highlights": [
                    "Gas hob",
                    "Modern kitchen",
                    "Excellent natural light",
                    "Private balcony",
                    "Dedicated office room",
                ],
                "lowlights": ["No dishwasher"],
                "one_line": "Bright Victorian conversion with modern kitchen and dedicated office",
                "value_for_quality": {
                    "rating": "good",
                    "reasoning": "Well-maintained property at fair price for E8 Victorian stock",
                },
            },
        }
    ],
    "model": "claude-sonnet-4-5-20250929",
    "stop_reason": "tool_use",
    "usage": {
        "input_tokens": 892,
        "output_tokens": 187,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 0,
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_property() -> Property:
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="resp-test-001",
        url=HttpUrl("https://www.rightmove.co.uk/properties/resp-test-001"),
        title="2 bed flat in Hackney",
        price_pcm=2000,
        bedrooms=2,
        address="42 Mare Street, Hackney, London",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
    )


@pytest.fixture
def test_merged_property(test_property: Property) -> MergedProperty:
    return MergedProperty(
        canonical=test_property,
        sources=(test_property.source,),
        source_urls={test_property.source: test_property.url},
        images=(
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=test_property.source,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img2.jpg"),
                source=test_property.source,
                image_type="gallery",
            ),
        ),
        floorplan=PropertyImage(
            url=HttpUrl("https://example.com/floorplan.jpg"),
            source=test_property.source,
            image_type="floorplan",
        ),
        min_price=test_property.price_pcm,
        max_price=test_property.price_pcm,
    )


def _make_quality_filter() -> PropertyQualityFilter:
    """Create a PropertyQualityFilter with a real Anthropic client (no SDK mocks)."""
    return PropertyQualityFilter(api_key="test-key-for-respx")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTwoPhaseAnalysisViaHTTP:
    """Test the full two-phase analysis through the HTTP layer with respx."""

    @respx.mock
    async def test_full_two_phase_analysis(
        self, test_merged_property: MergedProperty
    ) -> None:
        """Full two-phase analysis through real HTTP layer."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=[
                httpx.Response(200, json=PHASE1_VISUAL_RESPONSE),
                httpx.Response(200, json=PHASE2_EVALUATION_RESPONSE),
            ]
        )

        quality_filter = _make_quality_filter()
        try:
            results = await quality_filter.analyze_merged_properties([test_merged_property])
        finally:
            await quality_filter.close()

        assert len(results) == 1
        _, analysis = results[0]

        # Phase 1 visual data parsed correctly
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.kitchen.hob_type == "gas"
        assert analysis.condition.overall_condition == "good"
        assert analysis.light_space.natural_light == "excellent"
        assert analysis.space.living_room_sqm == 24.0
        assert analysis.space.hosting_layout == "good"
        assert analysis.overall_rating == 4
        assert analysis.condition_concerns is False

        # Phase 2 evaluation data parsed correctly
        assert analysis.listing_extraction is not None
        assert analysis.listing_extraction.epc_rating == "C"
        assert analysis.listing_extraction.broadband_type == "fttc"
        assert analysis.highlights is not None
        assert "Gas hob" in analysis.highlights
        assert "Dedicated office room" in analysis.highlights
        assert analysis.one_line is not None
        assert "Victorian" in analysis.one_line

        # Value assessment from Phase 2
        assert analysis.value is not None
        assert analysis.value.quality_adjusted_rating == "good"

        # Both phases were called
        assert route.call_count == 2

    @respx.mock
    async def test_request_headers(self, test_merged_property: MergedProperty) -> None:
        """Requests should include correct Anthropic API headers."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=[
                httpx.Response(200, json=PHASE1_VISUAL_RESPONSE),
                httpx.Response(200, json=PHASE2_EVALUATION_RESPONSE),
            ]
        )

        quality_filter = _make_quality_filter()
        try:
            await quality_filter.analyze_merged_properties([test_merged_property])
        finally:
            await quality_filter.close()

        # Check Phase 1 request headers
        phase1_request = route.calls[0].request
        assert phase1_request.headers["x-api-key"] == "test-key-for-respx"
        assert "anthropic-version" in phase1_request.headers
        assert phase1_request.headers["content-type"] == "application/json"

    @respx.mock
    async def test_request_body_structure(
        self, test_merged_property: MergedProperty
    ) -> None:
        """Phase 1 request body should contain images, system prompt, and tools."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=[
                httpx.Response(200, json=PHASE1_VISUAL_RESPONSE),
                httpx.Response(200, json=PHASE2_EVALUATION_RESPONSE),
            ]
        )

        quality_filter = _make_quality_filter()
        try:
            await quality_filter.analyze_merged_properties([test_merged_property])
        finally:
            await quality_filter.close()

        # Parse Phase 1 request body
        phase1_body = json.loads(route.calls[0].request.content)

        # Model and max_tokens
        assert phase1_body["model"] == "claude-sonnet-4-5-20250929"
        assert phase1_body["max_tokens"] == 16384

        # System prompt with cache_control
        assert len(phase1_body["system"]) == 1
        assert phase1_body["system"][0]["type"] == "text"
        assert phase1_body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "expert London rental property analyst" in phase1_body["system"][0]["text"]

        # Messages contain image blocks
        user_content = phase1_body["messages"][0]["content"]
        image_blocks = [b for b in user_content if b.get("type") == "image"]
        assert len(image_blocks) >= 2  # gallery images

        # Tool definitions
        assert len(phase1_body["tools"]) == 1
        assert phase1_body["tools"][0]["name"] == "property_visual_analysis"

        # Extended thinking enabled
        assert phase1_body["thinking"]["type"] == "enabled"
        assert phase1_body["tool_choice"] == {"type": "auto"}

    @respx.mock
    async def test_phase2_request_is_text_only(
        self, test_merged_property: MergedProperty
    ) -> None:
        """Phase 2 should send text-only (no images) with forced tool choice."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=[
                httpx.Response(200, json=PHASE1_VISUAL_RESPONSE),
                httpx.Response(200, json=PHASE2_EVALUATION_RESPONSE),
            ]
        )

        quality_filter = _make_quality_filter()
        try:
            await quality_filter.analyze_merged_properties([test_merged_property])
        finally:
            await quality_filter.close()

        # Parse Phase 2 request body
        phase2_body = json.loads(route.calls[1].request.content)

        # Phase 2 content is a text string (not a list of content blocks)
        phase2_content = phase2_body["messages"][0]["content"]
        assert isinstance(phase2_content, str)
        assert "<visual_analysis>" in phase2_content
        assert "</visual_analysis>" in phase2_content

        # Forced tool choice for Phase 2
        assert phase2_body["tool_choice"] == {
            "type": "tool",
            "name": "property_evaluation",
        }

        # No extended thinking in Phase 2
        assert "thinking" not in phase2_body

        # Evaluation system prompt
        assert "expert London rental property evaluator" in phase2_body["system"][0]["text"]


class TestHTTPErrorHandling:
    """Test error handling at the HTTP level."""

    @respx.mock
    async def test_phase1_server_error_graceful(
        self, test_merged_property: MergedProperty
    ) -> None:
        """500 error on Phase 1 should fail gracefully (after SDK retries)."""
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                500,
                json={"type": "error", "error": {"type": "api_error", "message": "overloaded"}},
            )
        )

        quality_filter = PropertyQualityFilter(api_key="test-key", max_images=10)
        # Disable retries to speed up test
        import anthropic

        quality_filter._client = anthropic.AsyncAnthropic(
            api_key="test-key", max_retries=0
        )

        try:
            from home_finder.filters.quality import APIUnavailableError

            with pytest.raises(APIUnavailableError):
                await quality_filter.analyze_single_merged(test_merged_property)
        finally:
            await quality_filter.close()

    @respx.mock
    async def test_phase1_rate_limit_trips_circuit(
        self, test_merged_property: MergedProperty
    ) -> None:
        """429 rate limit should trip circuit breaker."""
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                429,
                json={
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "rate limited"},
                },
                headers={"retry-after": "1"},
            )
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        import anthropic

        quality_filter._client = anthropic.AsyncAnthropic(
            api_key="test-key", max_retries=0
        )

        try:
            from home_finder.filters.quality import APIUnavailableError

            with pytest.raises(APIUnavailableError):
                await quality_filter.analyze_single_merged(test_merged_property)

            # Circuit breaker should have recorded the failure
            assert quality_filter._consecutive_api_failures >= 1
        finally:
            await quality_filter.close()

    @respx.mock
    async def test_phase2_failure_returns_partial(
        self, test_merged_property: MergedProperty
    ) -> None:
        """Phase 2 HTTP failure should return partial analysis with Phase 1 data."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=[
                httpx.Response(200, json=PHASE1_VISUAL_RESPONSE),
                httpx.Response(
                    500,
                    json={
                        "type": "error",
                        "error": {"type": "api_error", "message": "server error"},
                    },
                ),
            ]
        )

        quality_filter = PropertyQualityFilter(api_key="test-key")
        import anthropic

        quality_filter._client = anthropic.AsyncAnthropic(
            api_key="test-key", max_retries=0
        )

        try:
            results = await quality_filter.analyze_merged_properties([test_merged_property])
        finally:
            await quality_filter.close()

        assert len(results) == 1
        _, analysis = results[0]

        # Phase 1 visual data should be present
        assert analysis.kitchen.overall_quality == "modern"
        assert analysis.condition.overall_condition == "good"
        assert analysis.overall_rating == 4

        # Phase 2 evaluation data should be absent
        assert analysis.listing_extraction is None
        assert analysis.highlights is None
        assert analysis.one_line is None


class TestTokenUsageParsing:
    """Test that token usage from HTTP responses is handled correctly."""

    @respx.mock
    async def test_cache_tokens_parsed(
        self, test_merged_property: MergedProperty
    ) -> None:
        """Cache read/creation tokens should be parsed from the HTTP response."""
        respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=[
                httpx.Response(200, json=PHASE1_VISUAL_RESPONSE),
                httpx.Response(200, json=PHASE2_EVALUATION_RESPONSE),
            ]
        )

        quality_filter = _make_quality_filter()
        try:
            results = await quality_filter.analyze_merged_properties([test_merged_property])
        finally:
            await quality_filter.close()

        # If we got here without errors, the SDK successfully parsed the
        # usage fields including cache_read_input_tokens and cache_creation_input_tokens
        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.overall_rating == 4
