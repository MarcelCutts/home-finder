"""Live evaluation tests for quality analysis with real Claude API.

These tests are excluded from the default test run (marked @pytest.mark.slow).
They require a valid ANTHROPIC_API_KEY environment variable and make real API calls.

Run manually with: uv run pytest tests/test_filters/test_quality_eval.py -m slow -v

Uses code-based grading (Anthropic's recommended first evaluation layer):
structural checks on output ranges, required fields, and type constraints.
"""

import os
from datetime import datetime

import pytest
from pydantic import HttpUrl

from home_finder.filters.quality import PropertyQualityFilter
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertySource,
)

# Skip the entire module if no API key is set
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set — skipping live evaluation tests",
    ),
]


# ---------------------------------------------------------------------------
# Golden test properties
# ---------------------------------------------------------------------------

def _golden_2bed_with_images() -> MergedProperty:
    """2-bed property with multiple gallery images and floorplan."""
    prop = Property(
        source=PropertySource.OPENRENT,
        source_id="eval-2bed",
        url=HttpUrl("https://www.openrent.com/property/eval-2bed"),
        title="2 bed flat in Hackney",
        price_pcm=2000,
        bedrooms=2,
        address="42 Mare Street, Hackney, London",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        description=(
            "Spacious two bedroom Victorian conversion flat on a quiet residential "
            "street. Recently refurbished with modern kitchen including gas hob and "
            "integrated dishwasher. Large living room, wooden floors throughout. "
            "Two double bedrooms with built-in wardrobes. Modern bathroom with "
            "overhead shower. Shared garden. EPC rating C. Available now. "
            "Council tax band C. Furnished. 5 weeks deposit."
        ),
        first_seen=datetime(2026, 1, 15, 10, 30),
    )
    return MergedProperty(
        canonical=prop,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop.url},
        images=(
            # Using a known public domain image as a placeholder
            # In real usage, these would be cached locally
            PropertyImage(
                url=HttpUrl("https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/No-Image-Placeholder.svg/330px-No-Image-Placeholder.svg.png"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        ),
        floorplan=None,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
        descriptions={
            PropertySource.OPENRENT: prop.description or "",
        },
    )


def _golden_1bed_minimal() -> MergedProperty:
    """1-bed property with minimal images."""
    prop = Property(
        source=PropertySource.RIGHTMOVE,
        source_id="eval-1bed",
        url=HttpUrl("https://www.rightmove.co.uk/properties/eval-1bed"),
        title="1 bed flat in Stoke Newington",
        price_pcm=1700,
        bedrooms=1,
        address="15 Church Street, Stoke Newington, London",
        postcode="N16 0AP",
        latitude=51.5630,
        longitude=-0.0750,
        description="One bedroom flat close to Clissold Park. New kitchen. Furnished.",
        first_seen=datetime(2026, 1, 20, 14, 0),
    )
    return MergedProperty(
        canonical=prop,
        sources=(PropertySource.RIGHTMOVE,),
        source_urls={PropertySource.RIGHTMOVE: prop.url},
        images=(
            PropertyImage(
                url=HttpUrl("https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/No-Image-Placeholder.svg/330px-No-Image-Placeholder.svg.png"),
                source=PropertySource.RIGHTMOVE,
                image_type="gallery",
            ),
        ),
        floorplan=None,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
        descriptions={
            PropertySource.RIGHTMOVE: prop.description or "",
        },
    )


GOLDEN_PROPERTIES = [
    pytest.param(_golden_2bed_with_images(), id="2bed-with-images"),
    pytest.param(_golden_1bed_minimal(), id="1bed-minimal"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def quality_filter() -> PropertyQualityFilter:
    """Create a quality filter with real API key."""
    api_key = os.environ["ANTHROPIC_API_KEY"]
    return PropertyQualityFilter(
        api_key=api_key,
        max_images=5,
        enable_extended_thinking=True,
        thinking_budget_tokens=5000,
    )


class TestQualityAnalysisStructural:
    """Code-graded structural checks on live Claude API output."""

    @pytest.mark.parametrize("golden_property", GOLDEN_PROPERTIES)
    async def test_analysis_structure_and_ranges(
        self, golden_property: MergedProperty, quality_filter: PropertyQualityFilter
    ) -> None:
        """Verify structure and ranges of live Claude output."""
        try:
            _, analysis = await quality_filter.analyze_single_merged(golden_property)
        finally:
            await quality_filter.close()

        # Phase 1: Visual analysis fields are populated
        assert analysis is not None
        assert 1 <= analysis.overall_rating <= 5
        assert analysis.kitchen is not None
        assert analysis.kitchen.overall_quality in ("modern", "decent", "dated", "unknown")
        assert analysis.condition is not None
        assert analysis.condition.overall_condition in (
            "excellent", "good", "fair", "poor", "unknown"
        )
        assert analysis.light_space is not None
        assert analysis.space is not None
        assert analysis.space.confidence in ("high", "medium", "low")
        assert analysis.summary is not None
        assert len(analysis.summary) > 10

        # Concern severity is valid
        assert analysis.concern_severity in ("minor", "moderate", "serious", "none", None)

    @pytest.mark.parametrize("golden_property", GOLDEN_PROPERTIES)
    async def test_phase2_fields_populated(
        self, golden_property: MergedProperty, quality_filter: PropertyQualityFilter
    ) -> None:
        """Phase 2 evaluation fields should be populated."""
        try:
            _, analysis = await quality_filter.analyze_single_merged(golden_property)
        finally:
            await quality_filter.close()

        # Phase 2: Evaluation fields populated
        assert analysis.listing_extraction is not None
        assert analysis.listing_extraction.epc_rating in (
            "A", "B", "C", "D", "E", "F", "G", "unknown"
        )

        assert analysis.one_line is not None
        assert len(analysis.one_line) > 5

        assert analysis.highlights is not None
        assert len(analysis.highlights) >= 1

        # Value assessment
        assert analysis.value is not None
        assert analysis.value.quality_adjusted_rating in (
            "excellent", "good", "fair", "poor"
        )

    async def test_2bed_space_override(self, quality_filter: PropertyQualityFilter) -> None:
        """2-bed property should have is_spacious_enough=True (office goes in spare room)."""
        golden = _golden_2bed_with_images()
        try:
            _, analysis = await quality_filter.analyze_single_merged(golden)
        finally:
            await quality_filter.close()

        # The 2+ bed override should fire regardless of Claude's assessment
        assert analysis.space.is_spacious_enough is True
