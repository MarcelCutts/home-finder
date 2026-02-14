"""Tests for area context data loading and accessor functions."""

import pytest

from home_finder.data.area_context import (
    AREA_CONTEXT,
    get_area_overview,
    get_micro_areas,
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
        """9 outcodes should have micro_areas, 2 should not."""
        with_micro = {k for k, v in AREA_CONTEXT.items() if "micro_areas" in v}
        without_micro = set(AREA_CONTEXT.keys()) - with_micro
        assert with_micro == {"E3", "E5", "E8", "E9", "E10", "E17", "N15", "N16", "N17"}
        assert without_micro == {"E2", "E15"}


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

    def test_get_micro_areas_no_micro_areas(self) -> None:
        """E2 and E15 have no micro_areas."""
        assert get_micro_areas("E2") is None
        assert get_micro_areas("E15") is None

    def test_get_micro_areas_unknown_outcode(self) -> None:
        assert get_micro_areas("ZZ99") is None
