"""Tests for AI-drafted viewing message generation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_finder.models import (
    BedroomAnalysis,
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    PropertyQualityAnalysis,
    SpaceAnalysis,
)
from home_finder.utils.viewing_message import (
    SYSTEM_PROMPT,
    _build_user_prompt,
    generate_viewing_message,
)


@pytest.fixture
def base_analysis() -> PropertyQualityAnalysis:
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher="yes"),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
        space=SpaceAnalysis(living_room_sqm=18.0, is_spacious_enough=True, confidence="high"),
        bedroom=BedroomAnalysis(office_separation="dedicated_room"),
        overall_rating=4,
        summary="Nice place.",
        highlights=["Gas hob", "Dedicated office"],
        lowlights=["No garden"],
        one_line="Solid 2-bed with great kitchen and office setup.",
    )


class TestBuildUserPrompt:
    def test_includes_property_details(self) -> None:
        prompt = _build_user_prompt(
            title="2 bed in Hackney",
            postcode="E8 3RH",
            source_name="OpenRent",
            quality_analysis=None,
            profile="Test profile.",
        )
        assert "<property_title>2 bed in Hackney</property_title>" in prompt
        assert "<postcode>E8 3RH</postcode>" in prompt
        assert "<source>OpenRent</source>" in prompt

    def test_includes_highlights(self, base_analysis: PropertyQualityAnalysis) -> None:
        prompt = _build_user_prompt(
            title="Flat",
            postcode="N16 0AP",
            source_name="Zoopla",
            quality_analysis=base_analysis,
            profile="Profile.",
        )
        assert "<highlights>" in prompt
        assert "Gas hob" in prompt
        assert "Dedicated office" in prompt

    def test_includes_profile(self) -> None:
        prompt = _build_user_prompt(
            title="Flat",
            postcode=None,
            source_name="Rightmove",
            quality_analysis=None,
            profile="I am a software consultant.",
        )
        assert "<tenant_profile>I am a software consultant.</tenant_profile>" in prompt

    def test_includes_hob_type(self, base_analysis: PropertyQualityAnalysis) -> None:
        prompt = _build_user_prompt(
            title="Flat",
            postcode="E8 3RH",
            source_name="OpenRent",
            quality_analysis=base_analysis,
            profile="Profile.",
        )
        assert "<hob_type>gas</hob_type>" in prompt

    def test_includes_office_separation(self, base_analysis: PropertyQualityAnalysis) -> None:
        prompt = _build_user_prompt(
            title="Flat",
            postcode="E8 3RH",
            source_name="OpenRent",
            quality_analysis=base_analysis,
            profile="Profile.",
        )
        assert "<office_separation>dedicated_room</office_separation>" in prompt

    def test_skips_unknown_hob_type(self) -> None:
        qa = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(overall_quality="basic", hob_type="unknown"),
            condition=ConditionAnalysis(overall_condition="fair", confidence="medium"),
            light_space=LightSpaceAnalysis(natural_light="average"),
            space=SpaceAnalysis(living_room_sqm=12.0, is_spacious_enough=False, confidence="low"),
            overall_rating=3,
            summary="OK.",
        )
        prompt = _build_user_prompt(
            title="Flat",
            postcode="E8 3RH",
            source_name="OpenRent",
            quality_analysis=qa,
            profile="Profile.",
        )
        assert "hob_type" not in prompt

    def test_no_postcode(self) -> None:
        prompt = _build_user_prompt(
            title="Flat",
            postcode=None,
            source_name="OpenRent",
            quality_analysis=None,
            profile="Profile.",
        )
        assert "postcode" not in prompt

    def test_includes_one_liner(self, base_analysis: PropertyQualityAnalysis) -> None:
        prompt = _build_user_prompt(
            title="Flat",
            postcode="E8 3RH",
            source_name="OpenRent",
            quality_analysis=base_analysis,
            profile="Profile.",
        )
        assert "<one_liner>" in prompt
        assert "Solid 2-bed" in prompt


class TestGenerateViewingMessage:
    @pytest.mark.asyncio
    async def test_returns_text(self) -> None:
        mock_text_block = MagicMock()
        mock_text_block.text = "Hi, the gas hob caught my eye. Happy to view this week."

        mock_response = MagicMock()
        mock_response.content = [mock_text_block]

        mock_messages = MagicMock()
        mock_messages.create = AsyncMock(return_value=mock_response)

        mock_client = AsyncMock()
        mock_client.messages = mock_messages
        mock_client.close = AsyncMock()

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await generate_viewing_message(
                api_key="test-key",
                title="2 bed in E8",
                postcode="E8 3RH",
                source_name="OpenRent",
                quality_analysis=None,
                profile="Test profile.",
            )

        assert result == "Hi, the gas hob caught my eye. Happy to view this week."
        mock_messages.create.assert_called_once()
        call_kwargs = mock_messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-6"
        assert SYSTEM_PROMPT in call_kwargs["system"][0]["text"]
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_error_propagates(self) -> None:
        mock_messages = MagicMock()
        mock_messages.create = AsyncMock(side_effect=RuntimeError("API down"))

        mock_client = AsyncMock()
        mock_client.messages = mock_messages
        mock_client.close = AsyncMock()

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            pytest.raises(RuntimeError, match="API down"),
        ):
            await generate_viewing_message(
                api_key="test-key",
                title="Flat",
                postcode=None,
                source_name="Rightmove",
                quality_analysis=None,
                profile="Profile.",
            )

        # Client should still be closed even on error
        mock_client.close.assert_called_once()
