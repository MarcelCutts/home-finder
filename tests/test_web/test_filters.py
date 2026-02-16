"""Tests for PropertyFilter model and convenience methods."""

from home_finder.web.filters import PropertyFilter


class TestPropertyFilterValidation:
    """Validator tests: coercion, clamping, enum validation, graceful None."""

    def test_default_all_none(self) -> None:
        f = PropertyFilter()
        assert f.min_price is None
        assert f.max_price is None
        assert f.bedrooms is None
        assert f.min_rating is None
        assert f.area is None
        assert f.tags == []

    def test_str_to_int_coercion_prices(self) -> None:
        f = PropertyFilter(min_price="1500", max_price="2500")
        assert f.min_price == 1500
        assert f.max_price == 2500

    def test_invalid_price_becomes_none(self) -> None:
        f = PropertyFilter(min_price="abc", max_price="")
        assert f.min_price is None
        assert f.max_price is None

    def test_int_prices_passed_through(self) -> None:
        f = PropertyFilter(min_price=1000, max_price=3000)
        assert f.min_price == 1000
        assert f.max_price == 3000

    def test_bedrooms_clamped(self) -> None:
        assert PropertyFilter(bedrooms="0").bedrooms == 0
        assert PropertyFilter(bedrooms="-1").bedrooms == 0
        assert PropertyFilter(bedrooms="10").bedrooms == 10
        assert PropertyFilter(bedrooms="99").bedrooms == 10

    def test_bedrooms_invalid_becomes_none(self) -> None:
        assert PropertyFilter(bedrooms="abc").bedrooms is None

    def test_min_rating_clamped(self) -> None:
        assert PropertyFilter(min_rating="1").min_rating == 1
        assert PropertyFilter(min_rating="0").min_rating == 1
        assert PropertyFilter(min_rating="5").min_rating == 5
        assert PropertyFilter(min_rating="10").min_rating == 5

    def test_min_rating_invalid_becomes_none(self) -> None:
        assert PropertyFilter(min_rating="abc").min_rating is None

    def test_area_stripped(self) -> None:
        assert PropertyFilter(area="  E8  ").area == "E8"

    def test_area_empty_becomes_none(self) -> None:
        assert PropertyFilter(area="").area is None
        assert PropertyFilter(area="   ").area is None

    def test_valid_property_type(self) -> None:
        assert PropertyFilter(property_type="victorian").property_type == "victorian"
        assert PropertyFilter(property_type="WAREHOUSE").property_type == "warehouse"

    def test_invalid_property_type_becomes_none(self) -> None:
        assert PropertyFilter(property_type="mansion").property_type is None

    def test_outdoor_space_valid(self) -> None:
        assert PropertyFilter(outdoor_space="yes").outdoor_space == "yes"
        assert PropertyFilter(outdoor_space="NO").outdoor_space == "no"

    def test_outdoor_space_invalid_becomes_none(self) -> None:
        assert PropertyFilter(outdoor_space="maybe").outdoor_space is None

    def test_natural_light_valid(self) -> None:
        assert PropertyFilter(natural_light="excellent").natural_light == "excellent"

    def test_natural_light_invalid_becomes_none(self) -> None:
        assert PropertyFilter(natural_light="bright").natural_light is None

    def test_pets_only_yes(self) -> None:
        assert PropertyFilter(pets="yes").pets == "yes"
        assert PropertyFilter(pets="YES").pets == "yes"
        assert PropertyFilter(pets="no").pets is None
        assert PropertyFilter(pets="maybe").pets is None

    def test_value_rating_valid(self) -> None:
        assert PropertyFilter(value_rating="good").value_rating == "good"
        assert PropertyFilter(value_rating="EXCELLENT").value_rating == "excellent"

    def test_value_rating_invalid_becomes_none(self) -> None:
        assert PropertyFilter(value_rating="amazing").value_rating is None

    def test_hob_type_valid(self) -> None:
        assert PropertyFilter(hob_type="gas").hob_type == "gas"
        assert PropertyFilter(hob_type="INDUCTION").hob_type == "induction"

    def test_hob_type_invalid_becomes_none(self) -> None:
        assert PropertyFilter(hob_type="wood").hob_type is None

    def test_floor_level_valid(self) -> None:
        assert PropertyFilter(floor_level="ground").floor_level == "ground"

    def test_building_construction_valid(self) -> None:
        f = PropertyFilter(building_construction="solid_brick")
        assert f.building_construction == "solid_brick"

    def test_office_separation_valid(self) -> None:
        f = PropertyFilter(office_separation="dedicated_room")
        assert f.office_separation == "dedicated_room"

    def test_hosting_layout_valid(self) -> None:
        assert PropertyFilter(hosting_layout="excellent").hosting_layout == "excellent"

    def test_hosting_noise_risk_valid(self) -> None:
        assert PropertyFilter(hosting_noise_risk="low").hosting_noise_risk == "low"

    def test_broadband_type_valid(self) -> None:
        assert PropertyFilter(broadband_type="fttp").broadband_type == "fttp"

    def test_tags_filtered_against_valid_set(self) -> None:
        f = PropertyFilter(tags=["Gas hob", "bogus_tag", "Pets allowed"])
        assert f.tags == ["Gas hob", "Pets allowed"]

    def test_tags_empty_list(self) -> None:
        assert PropertyFilter(tags=[]).tags == []

    def test_tags_none_becomes_empty(self) -> None:
        assert PropertyFilter(tags=None).tags == []


class TestActiveFilterChips:
    def test_no_filters_no_chips(self) -> None:
        assert PropertyFilter().active_filter_chips() == []

    def test_bedrooms_chip(self) -> None:
        chips = PropertyFilter(bedrooms=0).active_filter_chips()
        assert len(chips) == 1
        assert chips[0] == {"key": "bedrooms", "label": "Studio"}

        chips = PropertyFilter(bedrooms=2).active_filter_chips()
        assert chips[0] == {"key": "bedrooms", "label": "2 bed"}

    def test_price_chips(self) -> None:
        chips = PropertyFilter(min_price=1500, max_price=2500).active_filter_chips()
        labels = [c["label"] for c in chips]
        assert "Min \u00a31,500" in labels
        assert "Max \u00a32,500" in labels

    def test_rating_chip(self) -> None:
        chips = PropertyFilter(min_rating=4).active_filter_chips()
        assert chips[0] == {"key": "min_rating", "label": "4+ stars"}

    def test_area_chip(self) -> None:
        chips = PropertyFilter(area="E8").active_filter_chips()
        assert chips[0] == {"key": "area", "label": "E8"}

    def test_property_type_chip_formatted(self) -> None:
        chips = PropertyFilter(property_type="purpose_built").active_filter_chips()
        assert chips[0]["label"] == "Purpose Built"

    def test_pets_chip(self) -> None:
        chips = PropertyFilter(pets="yes").active_filter_chips()
        assert chips[0] == {"key": "pets", "label": "Pets allowed"}

    def test_tag_chips_include_value(self) -> None:
        chips = PropertyFilter(tags=["Gas hob"]).active_filter_chips()
        assert len(chips) == 1
        assert chips[0]["key"] == "tag"
        assert chips[0]["label"] == "Gas hob"
        assert chips[0]["value"] == "Gas hob"

    def test_multiple_filters_combined(self) -> None:
        f = PropertyFilter(bedrooms=1, min_price=1500, hob_type="gas", tags=["Pets allowed"])
        chips = f.active_filter_chips()
        keys = [c["key"] for c in chips]
        assert "bedrooms" in keys
        assert "min_price" in keys
        assert "hob_type" in keys
        assert "tag" in keys


class TestQualityFieldsActive:
    def test_false_when_no_quality_filters(self) -> None:
        assert not PropertyFilter().quality_fields_active
        assert not PropertyFilter(min_price=1500, bedrooms=2).quality_fields_active

    def test_true_with_property_type(self) -> None:
        assert PropertyFilter(property_type="victorian").quality_fields_active

    def test_true_with_tags(self) -> None:
        assert PropertyFilter(tags=["Gas hob"]).quality_fields_active

    def test_true_with_broadband(self) -> None:
        assert PropertyFilter(broadband_type="fttp").quality_fields_active

    def test_true_with_pets(self) -> None:
        assert PropertyFilter(pets="yes").quality_fields_active


class TestSecondaryFilterCount:
    def test_zero_when_no_filters(self) -> None:
        assert PropertyFilter().secondary_filter_count == 0

    def test_counts_enum_filters(self) -> None:
        f = PropertyFilter(property_type="victorian", hob_type="gas", pets="yes")
        assert f.secondary_filter_count == 3

    def test_counts_tags(self) -> None:
        f = PropertyFilter(tags=["Gas hob", "Pets allowed"])
        assert f.secondary_filter_count == 2

    def test_combined_enum_and_tags(self) -> None:
        f = PropertyFilter(property_type="victorian", tags=["Gas hob"])
        assert f.secondary_filter_count == 2

    def test_does_not_count_primary_filters(self) -> None:
        """min_price, max_price, bedrooms, min_rating, area are not secondary."""
        f = PropertyFilter(min_price=1500, max_price=2500, bedrooms=2, min_rating=3, area="E8")
        assert f.secondary_filter_count == 0
