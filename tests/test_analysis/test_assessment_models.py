"""Storage assessment models in models/quality.py: defaults, coercion, and historical readers.

Live provider output is validated against the response models in analysis/analyzer.py
before assembly; the lenient readers tested here exist for saved assessments.
"""

from typing import Annotated, Any, Literal

import pytest
from pydantic import ValidationError

from home_finder.models import (
    BathroomAnalysis,
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    ListingRedFlags,
    OutdoorSpaceAnalysis,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    ValueAnalysis,
)


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
        """Invalid kitchen quality should be coerced to default."""
        analysis = KitchenAnalysis(overall_quality="excellent")  # type: ignore[arg-type]
        assert analysis.overall_quality == "unknown"


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
        """Invalid condition should be coerced to default."""
        analysis = ConditionAnalysis(overall_condition="amazing")  # type: ignore[arg-type]
        assert analysis.overall_condition == "unknown"


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
    """Tests for ValueAnalysis model."""

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
        assert FlooringNoiseAnalysis(has_double_glazing=True).has_double_glazing == "yes"  # type: ignore[arg-type]
        assert FlooringNoiseAnalysis(has_double_glazing=False).has_double_glazing == "no"  # type: ignore[arg-type]
        assert FlooringNoiseAnalysis(has_double_glazing=None).has_double_glazing == "unknown"  # type: ignore[arg-type]

    def test_listing_extraction_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for bills/pets."""
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
        assert BathroomAnalysis(is_ensuite=True).is_ensuite == "yes"  # type: ignore[arg-type]
        assert BathroomAnalysis(is_ensuite=False).is_ensuite == "no"  # type: ignore[arg-type]
        assert BathroomAnalysis(is_ensuite=None).is_ensuite == "unknown"  # type: ignore[arg-type]

    def test_bedroom_tristate_bool_coercion(self) -> None:
        """True→'yes', False→'no', None→'unknown' for bedroom tri-state fields."""
        assert BedroomAnalysis(primary_is_double=True).primary_is_double == "yes"  # type: ignore[arg-type]
        assert BedroomAnalysis(primary_is_double=False).primary_is_double == "no"  # type: ignore[arg-type]
        assert BedroomAnalysis(primary_is_double=None).primary_is_double == "unknown"  # type: ignore[arg-type]
        assert BedroomAnalysis(can_fit_desk=True).can_fit_desk == "yes"  # type: ignore[arg-type]
        assert BedroomAnalysis(can_fit_desk=False).can_fit_desk == "no"  # type: ignore[arg-type]
        assert BedroomAnalysis(can_fit_desk=None).can_fit_desk == "unknown"  # type: ignore[arg-type]


class TestAssessmentFieldDefaults:
    """Missing assessment fields retain their documented defaults."""

    def test_bedroom_without_office_separation(self) -> None:
        """Old DB rows without office_separation default to 'unknown'."""
        old_data = {"primary_is_double": "yes", "can_fit_desk": "yes", "notes": ""}
        bedroom = BedroomAnalysis.model_validate(old_data)
        assert bedroom.office_separation == "unknown"

    def test_space_without_hosting_layout(self) -> None:
        """Old DB rows without hosting_layout default to 'unknown'."""
        old_data = {"living_room_sqm": 20, "is_spacious_enough": True}
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

    def test_parses_assessment_without_work_hosting_or_broadband_fields(self) -> None:
        """Older assessment JSON remains readable when these fields are absent."""
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
            },
            "summary": "Nice flat",
        }
        analysis = PropertyQualityAnalysis.model_validate(old_json)
        assert analysis.space.hosting_layout == "unknown"
        assert analysis.summary == "Nice flat"


class TestUnwrapOnLineArbitraryKey:
    """Tests for unwrap_one_line handling of arbitrary single-key dicts."""

    def test_character_key_dict_unwrapped(self) -> None:
        """one_line as {"character": "text"} should extract the string value."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            one_line={"character": "Victorian flat with garden but dated throughout"},  # type: ignore[arg-type]
        )
        assert analysis.one_line == "Victorian flat with garden but dated throughout"

    def test_tagline_key_dict_unwrapped(self) -> None:
        """one_line as {"tagline": "text"} should extract the string value."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            one_line={"tagline": "Bright corner flat in quiet street"},  # type: ignore[arg-type]
        )
        assert analysis.one_line == "Bright corner flat in quiet street"

    def test_one_line_key_still_works(self) -> None:
        """Original {"one_line": "text"} pattern still works."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            one_line={"one_line": "Good flat"},  # type: ignore[arg-type]
        )
        assert analysis.one_line == "Good flat"

    def test_multi_key_dict_with_one_line_extracts(self) -> None:
        """Multi-key dict with 'one_line' key should still extract it."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            one_line={"one_line": "Good flat", "extra": "junk"},  # type: ignore[arg-type]
        )
        assert analysis.one_line == "Good flat"

    def test_json_string_with_arbitrary_key_unwrapped(self) -> None:
        """JSON string '{"character": "text"}' should be parsed and unwrapped."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            one_line='{"character": "Victorian flat"}',
        )
        assert analysis.one_line == "Victorian flat"

    def test_plain_string_passes_through(self) -> None:
        """Plain string should be unmodified."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            one_line="Just a plain string",
        )
        assert analysis.one_line == "Just a plain string"

    def test_empty_dict_raises(self) -> None:
        """Empty dict passes through unwrap_one_line, Pydantic rejects the type."""
        with pytest.raises(ValidationError):
            PropertyQualityAnalysis(
                kitchen=KitchenAnalysis(),
                condition=ConditionAnalysis(),
                light_space=LightSpaceAnalysis(),
                space=SpaceAnalysis(),
                summary="Test",
                one_line={},  # type: ignore[arg-type]
            )

    def test_multi_key_dict_without_one_line_raises(self) -> None:
        """Multi-key dict without 'one_line' key passes through, Pydantic rejects."""
        with pytest.raises(ValidationError):
            PropertyQualityAnalysis(
                kitchen=KitchenAnalysis(),
                condition=ConditionAnalysis(),
                light_space=LightSpaceAnalysis(),
                space=SpaceAnalysis(),
                summary="Test",
                one_line={"character": "x", "extra": "y"},  # type: ignore[arg-type]
            )

    def test_none_passes_through(self) -> None:
        """None value for one_line should remain None."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            one_line=None,
        )
        assert analysis.one_line is None


# Minimal valid JSON strings per sub-model field for parametrized coercion tests.
# Tuple: (json_string, attribute_to_check, expected_value)
_SUB_MODEL_JSON_SAMPLES: dict[str, tuple[str, str, object]] = {
    "kitchen": ('{"overall_quality": "modern", "hob_type": "gas"}', "overall_quality", "modern"),
    "condition": (
        '{"overall_condition": "good", "confidence": "high"}',
        "overall_condition",
        "good",
    ),
    "light_space": ('{"natural_light": "excellent"}', "natural_light", "excellent"),
    "space": ('{"living_room_sqm": 18}', "living_room_sqm", 18),
    "bathroom": ('{"overall_condition": "modern", "has_bathtub": "yes"}', "has_bathtub", "yes"),
    "bedroom": (
        '{"primary_is_double": "yes", "office_separation": "dedicated_room"}',
        "primary_is_double",
        "yes",
    ),
    "outdoor_space": ('{"has_balcony": true, "notes": "Nice"}', "has_balcony", True),
    "storage": ('{"storage_rating": "good"}', "storage_rating", "good"),
    "flooring_noise": ('{"primary_flooring": "hardwood"}', "primary_flooring", "hardwood"),
    "listing_red_flags": ('{"red_flag_count": 2}', "red_flag_count", 2),
    "listing_extraction": ('{"epc_rating": "B", "deposit_weeks": 5}', "epc_rating", "B"),
    "viewing_notes": (
        '{"check_items": ["Check boiler"], "questions_for_agent": []}',
        "check_items",
        ["Check boiler"],
    ),
    "value": ('{"area_average": 1800, "note": "Reasonable"}', "area_average", 1800),
}


class TestCoerceJsonStringsModelValidator:
    """The coerce_json_strings reader parses historical stringified sub-model objects."""

    def test_bedroom_json_string_with_trailing_comma(self) -> None:
        """Bedroom JSON string with trailing comma should still be parsed."""
        bedroom_json = (
            '{"primary_is_double": "yes", "has_built_in_wardrobe": "no",'
            ' "can_fit_desk": "yes", "notes": "OK",}'
        )
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            bedroom=bedroom_json,  # type: ignore[arg-type]
        )
        assert analysis.bedroom is not None
        assert analysis.bedroom.primary_is_double == "yes"

    @pytest.mark.parametrize(
        "value",
        ["{not valid json at all}", "just a string"],
        ids=["unrepairable_json", "plain_prose"],
    )
    def test_unparseable_sub_model_string_fails_validation(self, value: str) -> None:
        """A string the reader cannot turn into an object fails validation, not JSON decoding."""
        with pytest.raises(ValidationError):
            PropertyQualityAnalysis(
                kitchen=KitchenAnalysis(),
                condition=ConditionAnalysis(),
                light_space=LightSpaceAnalysis(),
                space=SpaceAnalysis(),
                summary="Test",
                bedroom=value,  # type: ignore[arg-type]
            )

    def test_dict_value_not_double_parsed(self) -> None:
        """Dict values should pass through without modification."""
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            bedroom={"primary_is_double": "yes", "notes": "Fine"},
        )
        assert analysis.bedroom is not None
        assert analysis.bedroom.primary_is_double == "yes"

    def test_control_characters_are_removed_from_repaired_values(self) -> None:
        """A NUL byte inside a value is stripped before the object is parsed."""
        bedroom_json = (
            '{"primary_is_double": "yes", "has_built_in_wardrobe": "no",'
            ' "can_fit_desk": "yes", "office_separation": "shared_space",'
            ' "notes": "Good\x00 room"}'
        )
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            bedroom=bedroom_json,  # type: ignore[arg-type]
        )
        assert analysis.bedroom is not None
        assert analysis.bedroom.primary_is_double == "yes"
        # The json_repair fallback alone would keep the NUL inside the note.
        assert analysis.bedroom.notes == "Good room"

    @pytest.mark.parametrize("field_name", sorted(_SUB_MODEL_JSON_SAMPLES))
    def test_json_string_parsed_for_all_sub_model_fields(self, field_name: str) -> None:
        """Every sub-model field should be parseable from a JSON string."""
        json_string, attr, expected = _SUB_MODEL_JSON_SAMPLES[field_name]

        # Required fields need model instances when they aren't the test target
        required_defaults: dict[str, Any] = {
            "kitchen": KitchenAnalysis(),
            "condition": ConditionAnalysis(),
            "light_space": LightSpaceAnalysis(),
            "space": SpaceAnalysis(),
        }
        kwargs: dict[str, Any] = {**required_defaults, "summary": "Test"}
        kwargs[field_name] = json_string  # override with JSON string

        analysis = PropertyQualityAnalysis(**kwargs)  # type: ignore[arg-type]
        field_value = getattr(analysis, field_name)
        assert field_value is not None, f"{field_name} should not be None after parsing"
        assert getattr(field_value, attr) == expected

    def test_tab_and_newline_survive_control_character_repair(self) -> None:
        """Tab, LF and CR are exempt from the strip that removes the NUL byte."""
        bedroom_json = '{"primary_is_double": "yes", "notes": "Desk\tnook\nby window\x00"}'
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            bedroom=bedroom_json,  # type: ignore[arg-type]
        )
        assert analysis.bedroom is not None
        assert analysis.bedroom.notes == "Desk\tnook\nby window"

    def test_control_characters_between_tokens_do_not_lose_the_sub_model(self) -> None:
        """SOH, VT and US between tokens of pretty-printed JSON still yield every field."""
        bedroom_json = (
            '{\n\t"primary_is_double":\x01 "yes",\n'
            '\t"has_built_in_wardrobe":\x0b "no",\n'
            '\t"can_fit_desk": "yes",\x1f\n'
            '\t"notes": "Fine"\n}'
        )
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            bedroom=bedroom_json,  # type: ignore[arg-type]
        )
        assert analysis.bedroom is not None
        assert analysis.bedroom.primary_is_double == "yes"
        assert analysis.bedroom.has_built_in_wardrobe == "no"
        assert analysis.bedroom.can_fit_desk == "yes"


class TestQualitySubModelFieldsSync:
    """Verify _QUALITY_SUB_MODEL_FIELDS stays in sync with PropertyQualityAnalysis."""

    def test_matches_actual_sub_model_fields(self) -> None:
        """_QUALITY_SUB_MODEL_FIELDS must match the BaseModel-typed fields on the class."""
        import typing

        from pydantic import BaseModel

        from home_finder.models.quality import _QUALITY_SUB_MODEL_FIELDS

        expected: set[str] = set()
        for name, field_info in PropertyQualityAnalysis.model_fields.items():
            annotation = field_info.annotation
            # Unwrap Optional[X] / X | None via typing.get_args
            args = typing.get_args(annotation)
            types_to_check = args if args else (annotation,)
            for t in types_to_check:
                if isinstance(t, type) and issubclass(t, BaseModel):
                    expected.add(name)
                    break

        assert expected == _QUALITY_SUB_MODEL_FIELDS, (
            f"_QUALITY_SUB_MODEL_FIELDS drift detected.\n"
            f"  Missing from frozenset: {expected - _QUALITY_SUB_MODEL_FIELDS}\n"
            f"  Extra in frozenset: {_QUALITY_SUB_MODEL_FIELDS - expected}"
        )


class TestLiteralCoercion:
    """Tests for _LenientLiteralModel auto-coercion of invalid Literal values."""

    def test_out_of_vocabulary_office_separation_falls_back_to_unknown(self) -> None:
        """A saved 'poor' office_separation (outside the Literal) reads as the default."""
        analysis = BedroomAnalysis(office_separation="poor")  # type: ignore[arg-type]
        assert analysis.office_separation == "unknown"

    def test_non_unknown_default(self) -> None:
        """Field with non-'unknown' default should coerce to that default."""
        # ConditionAnalysis.confidence defaults to "medium"
        analysis = ConditionAnalysis(confidence="mega")  # type: ignore[arg-type]
        assert analysis.confidence == "medium"

    def test_optional_literal_fallback_to_none(self) -> None:
        """Optional Literal field should coerce to None (field default)."""
        analysis = ValueAnalysis(rating="superb")  # type: ignore[arg-type]
        assert analysis.rating is None

    def test_none_passthrough(self) -> None:
        """None on an Optional Literal field should stay None."""
        analysis = KitchenAnalysis(hob_type=None)
        assert analysis.hob_type is None

    def test_tristatebool_invalid(self) -> None:
        """TriStateBool field with invalid string should coerce to default."""
        analysis = KitchenAnalysis(has_dishwasher="maybe")  # type: ignore[arg-type]
        assert analysis.has_dishwasher == "unknown"


class TestLiteralFieldIntrospection:
    """Tests for _extract_literal_values and _literal_field_info helpers."""

    def test_plain_literal(self) -> None:
        """Should extract values from a plain Literal type."""
        from home_finder.models.quality import _extract_literal_values

        result = _extract_literal_values(Literal["a", "b", "c"])
        assert result == ("a", "b", "c")

    def test_optional_literal(self) -> None:
        """Should extract values from Literal[...] | None."""
        from home_finder.models.quality import _extract_literal_values

        result = _extract_literal_values(Literal["x", "y"] | None)
        assert result is not None
        assert set(result) == {"x", "y"}

    def test_annotated_literal(self) -> None:
        """Should extract values from Annotated[Literal[...], ...]."""
        from home_finder.models.quality import _extract_literal_values

        result = _extract_literal_values(Annotated[Literal["a", "b"], "metadata"])
        assert result == ("a", "b")

    def test_annotated_optional_literal(self) -> None:
        """Should extract values from Annotated[Literal[...] | None, ...]."""
        from home_finder.models.quality import _extract_literal_values

        result = _extract_literal_values(Annotated[Literal["a", "b"] | None, "meta"])
        assert result is not None
        assert set(result) == {"a", "b"}

    def test_non_literal_types_return_none(self) -> None:
        """Non-Literal types should return None."""
        from home_finder.models.quality import _extract_literal_values

        assert _extract_literal_values(bool) is None
        assert _extract_literal_values(str) is None
        assert _extract_literal_values(list[str]) is None

    def test_literal_field_info_bedroom(self) -> None:
        """_literal_field_info should include office_separation for BedroomAnalysis."""
        from home_finder.models.quality import _literal_field_info

        info = _literal_field_info(BedroomAnalysis)
        assert "office_separation" in info
        allowed, default = info["office_separation"]
        assert "dedicated_room" in allowed
        assert "poor" not in allowed
        assert default == "unknown"


class TestCoercedBoolStringInputs:
    """CoercedBool reads string booleans in saved assessments.

    Live visual output with a string boolean is rejected before assembly instead.
    """

    def test_unknown_coerces_to_false(self) -> None:
        """'unknown' should coerce to False — safe default for uncertain booleans."""
        analysis = OutdoorSpaceAnalysis(has_shared_garden="unknown")  # type: ignore[arg-type]
        assert analysis.has_shared_garden is False

    def test_yes_coerces_to_true(self) -> None:
        analysis = OutdoorSpaceAnalysis(has_balcony="yes")  # type: ignore[arg-type]
        assert analysis.has_balcony is True

    def test_no_coerces_to_false(self) -> None:
        analysis = OutdoorSpaceAnalysis(has_garden="no")  # type: ignore[arg-type]
        assert analysis.has_garden is False

    def test_true_string_coerces_to_true(self) -> None:
        analysis = OutdoorSpaceAnalysis(has_terrace="true")  # type: ignore[arg-type]
        assert analysis.has_terrace is True

    def test_false_string_coerces_to_false(self) -> None:
        analysis = OutdoorSpaceAnalysis(has_balcony="false")  # type: ignore[arg-type]
        assert analysis.has_balcony is False

    def test_na_coerces_to_false(self) -> None:
        analysis = OutdoorSpaceAnalysis(has_shared_garden="n/a")  # type: ignore[arg-type]
        assert analysis.has_shared_garden is False

    def test_empty_string_coerces_to_false(self) -> None:
        analysis = OutdoorSpaceAnalysis(has_garden="")  # type: ignore[arg-type]
        assert analysis.has_garden is False

    def test_case_insensitive(self) -> None:
        """Coercion should be case-insensitive."""
        analysis = OutdoorSpaceAnalysis(
            has_balcony="YES",  # type: ignore[arg-type]
            has_garden="Unknown",  # type: ignore[arg-type]
            has_terrace="TRUE",  # type: ignore[arg-type]
        )
        assert analysis.has_balcony is True
        assert analysis.has_garden is False
        assert analysis.has_terrace is True

    def test_listing_red_flags_too_few_photos(self) -> None:
        """CoercedBool on ListingRedFlags should also handle string inputs."""
        flags = ListingRedFlags(too_few_photos="unknown", selective_angles="yes")  # type: ignore[arg-type]
        assert flags.too_few_photos is False
        assert flags.selective_angles is True

    def test_none_still_coerces_to_false(self) -> None:
        """Original None → False behavior must be preserved."""
        analysis = OutdoorSpaceAnalysis(has_balcony=None)  # type: ignore[arg-type]
        assert analysis.has_balcony is False

    def test_bool_values_pass_through(self) -> None:
        """Actual bool values should not be affected."""
        analysis = OutdoorSpaceAnalysis(has_balcony=True, has_garden=False)
        assert analysis.has_balcony is True
        assert analysis.has_garden is False


class TestHistoricalJsonRepair:
    """The json_repair fallback reads saved sub-model JSON the stricter parsers reject."""

    def test_model_validator_repairs_missing_colon(self) -> None:
        """coerce_json_strings model validator should repair missing colons."""
        bedroom_json = (
            '{"primary_is_double" "yes", "has_built_in_wardrobe" "no",'
            ' "can_fit_desk" "yes", "office_separation" "shared_space",'
            ' "notes" "Decent room"}'
        )
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            bedroom=bedroom_json,  # type: ignore[arg-type]
        )
        assert analysis.bedroom is not None
        assert analysis.bedroom.primary_is_double == "yes"
        assert analysis.bedroom.office_separation == "shared_space"

    def test_model_validator_repairs_set_literal_syntax(self) -> None:
        """A key without a value (Python set-literal syntax) does not lose the other fields."""
        bedroom_json = (
            '{"primary_is_double", "has_built_in_wardrobe": "no",'
            ' "can_fit_desk": "yes", "office_separation": "shared_space",'
            ' "notes": "Decent room"}'
        )
        analysis = PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(),
            condition=ConditionAnalysis(),
            light_space=LightSpaceAnalysis(),
            space=SpaceAnalysis(),
            summary="Test",
            bedroom=bedroom_json,  # type: ignore[arg-type]
        )
        assert analysis.bedroom is not None
        assert analysis.bedroom.can_fit_desk == "yes"
        assert analysis.bedroom.office_separation == "shared_space"
