"""Tests for area context data loading and accessor functions."""

import pytest

from home_finder.data.area_context import (
    ACOUSTIC_PROFILES,
    AREA_CONTEXT,
    BROADBAND_COSTS_MONTHLY,
    CREATIVE_SCENE,
    ENERGY_COSTS_MONTHLY,
    HOSTING_TOLERANCE,
    NOISE_ENFORCEMENT,
    SERVICE_CHARGE_RANGES,
    WARD_TO_MICRO_AREA,
    WATER_COSTS_MONTHLY,
    get_area_overview,
    get_micro_area_for_ward,
    get_micro_areas,
    match_micro_area,
)

VALID_HOSTING_VALUES = {"high", "moderate", "low"}
VALID_WFH_VALUES = {"good", "moderate", "poor"}


class TestDataLoading:
    def test_area_context_loads(self) -> None:
        assert isinstance(AREA_CONTEXT, dict)
        assert len(AREA_CONTEXT) > 0

    def test_all_entries_are_dicts(self) -> None:
        """Every area_context entry should be a dict (no legacy strings)."""
        for outcode, entry in AREA_CONTEXT.items():
            assert isinstance(entry, dict), f"{outcode} is not a dict: {type(entry)}"

    def test_all_entries_have_overview(self) -> None:
        for outcode, entry in AREA_CONTEXT.items():
            assert "overview" in entry, f"{outcode} missing 'overview' key"
            assert isinstance(entry["overview"], str)
            assert len(entry["overview"]) > 50, f"{outcode} overview too short"

    def test_outcodes_with_micro_areas(self) -> None:
        """All outcodes should have micro_areas."""
        with_micro = {k for k, v in AREA_CONTEXT.items() if "micro_areas" in v}
        without_micro = set(AREA_CONTEXT.keys()) - with_micro
        assert with_micro == {
            "E2",
            "E3",
            "E5",
            "E8",
            "E9",
            "E10",
            "E15",
            "E17",
            "N15",
            "N16",
            "N17",
        }
        assert without_micro == set()


class TestMicroAreaValidation:
    @pytest.fixture
    def all_micro_areas(self) -> list[tuple[str, str, dict]]:
        """Collect all micro-areas as (outcode, name, data) tuples."""
        result = []
        for outcode, entry in AREA_CONTEXT.items():
            if not isinstance(entry, dict):
                continue
            for name, ma in entry.get("micro_areas", {}).items():
                result.append((outcode, name, ma))
        return result

    def test_hosting_tolerance_values(self, all_micro_areas: list) -> None:
        for outcode, name, ma in all_micro_areas:
            if "hosting_tolerance" in ma:
                assert ma["hosting_tolerance"] in VALID_HOSTING_VALUES, (
                    f"{outcode}/{name}: invalid hosting_tolerance '{ma['hosting_tolerance']}'"
                )

    def test_wfh_suitability_values(self, all_micro_areas: list) -> None:
        for outcode, name, ma in all_micro_areas:
            if "wfh_suitability" in ma:
                assert ma["wfh_suitability"] in VALID_WFH_VALUES, (
                    f"{outcode}/{name}: invalid wfh_suitability '{ma['wfh_suitability']}'"
                )

    def test_micro_areas_have_required_fields(self, all_micro_areas: list) -> None:
        required = {"character", "transport", "hosting_tolerance", "wfh_suitability"}
        for outcode, name, ma in all_micro_areas:
            missing = required - set(ma.keys())
            assert not missing, f"{outcode}/{name} missing fields: {missing}"


class TestAccessors:
    def test_get_area_overview_known(self) -> None:
        result = get_area_overview("E8")
        assert result is not None
        assert "Hackney" in result

    def test_get_area_overview_unknown(self) -> None:
        assert get_area_overview("ZZ99") is None

    def test_get_micro_areas_known(self) -> None:
        result = get_micro_areas("E8")
        assert result is not None
        assert "Dalston core" in result
        assert "Haggerston" in result

    def test_get_micro_areas_e2(self) -> None:
        """E2 has micro_areas with expected neighborhoods."""
        result = get_micro_areas("E2")
        assert result is not None
        assert "Bethnal Green / Cambridge Heath" in result
        assert "Haggerston / Queensbridge" in result

    def test_get_micro_areas_e15(self) -> None:
        """E15 has micro_areas with expected neighborhoods."""
        result = get_micro_areas("E15")
        assert result is not None
        assert "Stratford Village / The Grove" in result
        assert "Maryland / Forest Gate Border" in result

    def test_get_micro_areas_unknown_outcode(self) -> None:
        assert get_micro_areas("ZZ99") is None


class TestAcousticProfiles:
    def test_acoustic_profiles_loaded(self) -> None:
        assert isinstance(ACOUSTIC_PROFILES, dict)
        expected_keys = {
            "victorian",
            "edwardian",
            "georgian",
            "new_build",
            "purpose_built",
            "warehouse",
            "ex_council",
            "period_conversion",
        }
        assert set(ACOUSTIC_PROFILES.keys()) == expected_keys

    def test_acoustic_profiles_have_valid_hosting_safety(self) -> None:
        valid_values = {"good", "moderate", "poor"}
        for key, profile in ACOUSTIC_PROFILES.items():
            assert profile["hosting_safety"] in valid_values, (
                f"{key}: invalid hosting_safety '{profile['hosting_safety']}'"
            )

    def test_acoustic_profiles_have_required_fields(self) -> None:
        required = {
            "label",
            "airborne_insulation_db",
            "hosting_safety",
            "summary",
            "viewing_checks",
        }
        for key, profile in ACOUSTIC_PROFILES.items():
            missing = required - set(profile.keys())
            assert not missing, f"{key} missing fields: {missing}"

    def test_acoustic_profile_viewing_checks_are_lists(self) -> None:
        for key, profile in ACOUSTIC_PROFILES.items():
            assert isinstance(profile["viewing_checks"], list), f"{key}: viewing_checks not a list"
            assert len(profile["viewing_checks"]) > 0, f"{key}: empty viewing_checks"


class TestNoiseEnforcement:
    def test_noise_enforcement_loaded(self) -> None:
        assert isinstance(NOISE_ENFORCEMENT, dict)
        expected_boroughs = {"Hackney", "Haringey", "Tower Hamlets", "Waltham Forest", "Newham"}
        assert set(NOISE_ENFORCEMENT.keys()) == expected_boroughs

    def test_hackney_has_process_field(self) -> None:
        hackney = NOISE_ENFORCEMENT["Hackney"]
        assert "process" in hackney
        assert "NoiseWorks" in hackney["process"]

    def test_noise_enforcement_have_required_fields(self) -> None:
        required = {"process", "threshold_info", "response_time"}
        for borough, data in NOISE_ENFORCEMENT.items():
            missing = required - set(data.keys())
            assert not missing, f"{borough} missing fields: {missing}"


class TestWardMapping:
    def test_all_mapped_micro_areas_exist(self) -> None:
        """Every micro-area name in WARD_TO_MICRO_AREA must exist in area_context.json."""
        for (outcode, ward), micro_area_name in WARD_TO_MICRO_AREA.items():
            micro_areas = get_micro_areas(outcode)
            assert micro_areas is not None, f"No micro-areas for {outcode} (ward={ward})"
            assert micro_area_name in micro_areas, (
                f"Ward ({outcode}, {ward}) maps to '{micro_area_name}' "
                f"but that doesn't exist in {outcode}'s micro-areas: {list(micro_areas.keys())}"
            )

    def test_all_outcodes_with_micro_areas_have_ward_mappings(self) -> None:
        """Every outcode that has micro-areas should have at least one ward mapping."""
        outcodes_with_micro = {
            k for k, v in AREA_CONTEXT.items() if isinstance(v, dict) and "micro_areas" in v
        }
        outcodes_with_wards = {outcode for outcode, _ in WARD_TO_MICRO_AREA.keys()}
        missing = outcodes_with_micro - outcodes_with_wards
        assert not missing, f"Outcodes with micro-areas but no ward mappings: {missing}"

    def test_get_micro_area_for_ward_known(self) -> None:
        assert get_micro_area_for_ward("London Fields", "E8") == "London Fields / Broadway Market"
        assert get_micro_area_for_ward("Dalston", "E8") == "Dalston core"
        assert get_micro_area_for_ward("Hackney Wick", "E9") == "Hackney Wick core"

    def test_get_micro_area_for_ward_unknown(self) -> None:
        assert get_micro_area_for_ward("Nonexistent Ward", "E8") is None
        assert get_micro_area_for_ward("Dalston", "ZZ99") is None

    def test_lea_bridge_disambiguation(self) -> None:
        """Lea Bridge exists in both Hackney (E5) and Waltham Forest (E10)."""
        assert get_micro_area_for_ward("Lea Bridge", "E5") == "Lea Bridge fringe"
        assert get_micro_area_for_ward("Lea Bridge", "E10") == "Lea Bridge Road"

    def test_e2_cross_borough(self) -> None:
        """E2 straddles Hackney and Tower Hamlets."""
        assert get_micro_area_for_ward("Haggerston", "E2") == "Haggerston / Queensbridge"
        assert get_micro_area_for_ward("Weavers", "E2") == "Weavers / Brick Lane Fringe"
        assert (
            get_micro_area_for_ward("Bethnal Green West", "E2") == "Bethnal Green / Cambridge Heath"
        )


class TestTextMicroAreaMatching:
    def test_match_by_neighbourhood_name(self) -> None:
        """Address containing a neighbourhood name should match."""
        result = match_micro_area("Flat 2, Roman Road, London E3", "E3")
        assert result == "Roman Road / Old Ford"

    def test_match_broadway_market(self) -> None:
        result = match_micro_area("125 Broadway Market, London E8", "E8")
        assert result == "London Fields / Broadway Market"

    def test_match_by_street_in_prose(self) -> None:
        """Street names mentioned in character/value fields should match."""
        result = match_micro_area("15 Mare Street, Hackney E8", "E8")
        assert result is not None

    def test_no_match_generic_address(self) -> None:
        """Generic addresses with no neighbourhood signals may return None."""
        result = match_micro_area("Flat 3, Tower House, E8", "E8")
        # May or may not match — just shouldn't crash
        assert result is None or result in (get_micro_areas("E8") or {})

    def test_no_match_empty_address(self) -> None:
        assert match_micro_area("", "E8") is None

    def test_no_match_unknown_outcode(self) -> None:
        assert match_micro_area("123 Some Street", "ZZ99") is None


class TestCostData:
    def test_energy_costs_loaded(self) -> None:
        """Energy costs should have A-G ratings with 1_bed/2_bed keys."""
        assert isinstance(ENERGY_COSTS_MONTHLY, dict)
        expected_ratings = {"A", "B", "C", "D", "E", "F", "G"}
        assert set(ENERGY_COSTS_MONTHLY.keys()) == expected_ratings
        for rating, costs in ENERGY_COSTS_MONTHLY.items():
            assert "1_bed" in costs, f"{rating} missing 1_bed"
            assert "2_bed" in costs, f"{rating} missing 2_bed"
            assert costs["1_bed"] > 0
            assert costs["2_bed"] > costs["1_bed"]

    def test_water_costs_loaded(self) -> None:
        """Water costs should have 1_bed/2_bed keys."""
        assert isinstance(WATER_COSTS_MONTHLY, dict)
        assert "1_bed" in WATER_COSTS_MONTHLY
        assert "2_bed" in WATER_COSTS_MONTHLY
        assert WATER_COSTS_MONTHLY["1_bed"] > 0
        assert WATER_COSTS_MONTHLY["2_bed"] >= WATER_COSTS_MONTHLY["1_bed"]

    def test_broadband_costs_loaded(self) -> None:
        """Broadband costs should have expected type keys."""
        assert isinstance(BROADBAND_COSTS_MONTHLY, dict)
        assert "fttp" in BROADBAND_COSTS_MONTHLY
        assert BROADBAND_COSTS_MONTHLY["fttp"] > 0

    def test_service_charge_ranges_loaded(self) -> None:
        """Service charge ranges should have property type keys with valid ranges."""
        assert isinstance(SERVICE_CHARGE_RANGES, dict)
        assert "new_build" in SERVICE_CHARGE_RANGES
        for prop_type, sc_range in SERVICE_CHARGE_RANGES.items():
            assert "typical_low" in sc_range, f"{prop_type} missing typical_low"
            assert "typical_high" in sc_range, f"{prop_type} missing typical_high"
            assert sc_range["typical_low"] < sc_range["typical_high"], (
                f"{prop_type}: typical_low >= typical_high"
            )


class TestHostingTolerance:
    def test_hosting_tolerance_loaded(self) -> None:
        assert isinstance(HOSTING_TOLERANCE, dict)
        assert len(HOSTING_TOLERANCE) > 0

    def test_expected_outcodes_present(self) -> None:
        expected = {"E2", "E3", "E5", "E8", "E9", "E10", "E15", "E17", "N15", "N16", "N17"}
        assert set(HOSTING_TOLERANCE.keys()) == expected

    def test_ratings_are_valid(self) -> None:
        valid_ratings = {"high", "moderate", "low"}
        for outcode, data in HOSTING_TOLERANCE.items():
            assert data["rating"] in valid_ratings, f"{outcode}: invalid rating '{data['rating']}'"

    def test_required_fields_present(self) -> None:
        for outcode, data in HOSTING_TOLERANCE.items():
            assert "rating" in data, f"{outcode} missing 'rating'"
            assert "notes" in data, f"{outcode} missing 'notes'"
            assert isinstance(data["notes"], str)
            assert len(data["notes"]) > 10, f"{outcode} notes too short"

    def test_n15_rated_high(self) -> None:
        """N15 (Tottenham Hale creative corridor) should be rated high."""
        assert HOSTING_TOLERANCE["N15"]["rating"] == "high"

    def test_n16_rated_low(self) -> None:
        """N16 (Stoke Newington families) should be rated low."""
        assert HOSTING_TOLERANCE["N16"]["rating"] == "low"

    def test_friendly_areas_are_lists(self) -> None:
        for outcode, data in HOSTING_TOLERANCE.items():
            if "known_friendly_areas" in data:
                assert isinstance(data["known_friendly_areas"], list), (
                    f"{outcode}: known_friendly_areas not a list"
                )

    def test_sensitive_areas_are_lists(self) -> None:
        for outcode, data in HOSTING_TOLERANCE.items():
            if "known_sensitive_areas" in data:
                assert isinstance(data["known_sensitive_areas"], list), (
                    f"{outcode}: known_sensitive_areas not a list"
                )

    def test_all_outcodes_exist_in_area_context(self) -> None:
        """Every outcode in HOSTING_TOLERANCE must also exist in AREA_CONTEXT."""
        missing = set(HOSTING_TOLERANCE.keys()) - set(AREA_CONTEXT.keys())
        assert not missing, f"HOSTING_TOLERANCE outcodes not in AREA_CONTEXT: {missing}"


class TestCreativeScene:
    def test_creative_scene_loaded(self) -> None:
        assert isinstance(CREATIVE_SCENE, dict)
        assert len(CREATIVE_SCENE) > 0

    def test_expected_outcodes_present(self) -> None:
        expected = {"E2", "E3", "E5", "E8", "E9", "E10", "E15", "E17", "N15", "N16", "N17"}
        assert set(CREATIVE_SCENE.keys()) == expected

    def test_structure_valid(self) -> None:
        for outcode, data in CREATIVE_SCENE.items():
            assert "summary" in data, f"{outcode} missing 'summary'"
            assert isinstance(data["summary"], str)
            assert len(data["summary"]) > 10, f"{outcode} summary too short"

    def test_list_fields_are_lists(self) -> None:
        list_fields = ["rehearsal_spaces", "venues", "creative_hubs"]
        for outcode, data in CREATIVE_SCENE.items():
            for field in list_fields:
                if field in data:
                    assert isinstance(data[field], list), f"{outcode}: {field} not a list"

    def test_e8_has_venues(self) -> None:
        """E8 (Dalston/Hackney) should have multiple venues."""
        assert len(CREATIVE_SCENE["E8"]["venues"]) >= 3

    def test_e2_has_rehearsal_spaces(self) -> None:
        """E2 should have rehearsal spaces from research."""
        assert len(CREATIVE_SCENE["E2"]["rehearsal_spaces"]) >= 3

    def test_all_outcodes_exist_in_area_context(self) -> None:
        """Every outcode in CREATIVE_SCENE must also exist in AREA_CONTEXT."""
        missing = set(CREATIVE_SCENE.keys()) - set(AREA_CONTEXT.keys())
        assert not missing, f"CREATIVE_SCENE outcodes not in AREA_CONTEXT: {missing}"

    def test_creative_scene_matches_hosting_tolerance_outcodes(self) -> None:
        """CREATIVE_SCENE and HOSTING_TOLERANCE should cover the same outcodes."""
        assert set(CREATIVE_SCENE.keys()) == set(HOSTING_TOLERANCE.keys()), (
            f"Mismatch: creative_scene={set(CREATIVE_SCENE.keys())}, "
            f"hosting_tolerance={set(HOSTING_TOLERANCE.keys())}"
        )
