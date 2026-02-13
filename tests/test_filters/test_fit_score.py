"""Tests for Marcel Fit Score and Lifestyle Quick-Glance Icons."""

from __future__ import annotations

from home_finder.filters.fit_score import (
    WEIGHTS,
    compute_fit_score,
    compute_lifestyle_icons,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _full_analysis(**overrides: object) -> dict:
    """Build a realistic analysis_json dict with sensible defaults."""
    base: dict = {
        "kitchen": {
            "overall_quality": "decent",
            "hob_type": "gas",
            "has_dishwasher": "yes",
            "has_washing_machine": "yes",
        },
        "condition": {
            "overall_condition": "good",
            "has_visible_damp": "no",
            "has_visible_mold": "no",
            "has_worn_fixtures": "no",
        },
        "light_space": {
            "natural_light": "good",
            "feels_spacious": True,
            "ceiling_height": "high",
            "floor_level": "upper",
        },
        "space": {
            "living_room_sqm": 20,
            "is_spacious_enough": True,
            "confidence": "high",
        },
        "bedroom": {
            "primary_is_double": "yes",
            "can_fit_desk": "yes",
        },
        "outdoor_space": {
            "has_balcony": True,
            "has_garden": False,
            "has_terrace": False,
            "has_shared_garden": False,
        },
        "flooring_noise": {
            "has_double_glazing": "yes",
            "building_construction": "solid_brick",
            "noise_indicators": [],
        },
        "listing_extraction": {
            "property_type": "victorian",
        },
        "highlights": ["Period features", "High ceilings"],
        "overall_rating": 4,
        "condition_concerns": False,
        "value": {"quality_adjusted_rating": "good"},
    }
    for key, val in overrides.items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            base[key].update(val)
        else:
            base[key] = val
    return base


# ── compute_fit_score ──────────────────────────────────────────────────────────


class TestComputeFitScore:
    def test_none_analysis_returns_none(self):
        assert compute_fit_score(None, 2) is None

    def test_empty_analysis_returns_none(self):
        assert compute_fit_score({}, 2) is None

    def test_returns_int_for_valid_analysis(self):
        result = compute_fit_score(_full_analysis(), 2)
        assert isinstance(result, int)

    def test_score_in_valid_range(self):
        result = compute_fit_score(_full_analysis(), 2)
        assert result is not None
        assert 0 <= result <= 100

    def test_ideal_2bed_scores_high(self):
        """A 2-bed with gas hob, solid brick, high ceilings, spacious should score high."""
        analysis = _full_analysis(
            kitchen={"overall_quality": "modern", "hob_type": "gas",
                     "has_dishwasher": "yes", "has_washing_machine": "yes"},
            flooring_noise={"building_construction": "solid_brick",
                            "has_double_glazing": "yes", "noise_indicators": []},
            light_space={"ceiling_height": "high", "feels_spacious": True,
                         "floor_level": "top"},
            listing_extraction={"property_type": "warehouse"},
            highlights=["Period features", "High ceilings", "Original character"],
            overall_rating=5,
        )
        score = compute_fit_score(analysis, 2)
        assert score is not None
        assert score >= 75

    def test_studio_scores_lower(self):
        """Studio with basic amenities should score lower due to workspace penalty."""
        analysis = _full_analysis(
            bedroom={"can_fit_desk": "no"},
            space={"is_spacious_enough": False, "living_room_sqm": 12},
        )
        studio_score = compute_fit_score(analysis, 0)
        two_bed_score = compute_fit_score(analysis, 2)
        assert studio_score is not None
        assert two_bed_score is not None
        assert studio_score < two_bed_score

    def test_electric_hob_lowers_kitchen_score(self):
        """Electric hob should produce a lower score than gas."""
        gas = _full_analysis(kitchen={"hob_type": "gas", "overall_quality": "modern",
                                       "has_dishwasher": "yes", "has_washing_machine": "yes"})
        electric = _full_analysis(kitchen={"hob_type": "electric", "overall_quality": "modern",
                                            "has_dishwasher": "yes", "has_washing_machine": "yes"})
        gas_score = compute_fit_score(gas, 2)
        elec_score = compute_fit_score(electric, 2)
        assert gas_score is not None and elec_score is not None
        assert gas_score > elec_score

    def test_warehouse_gets_vibe_bonus(self):
        """Warehouse property type should boost vibe and sound scores."""
        warehouse = _full_analysis(listing_extraction={"property_type": "warehouse"})
        new_build = _full_analysis(listing_extraction={"property_type": "new_build"})
        w_score = compute_fit_score(warehouse, 2)
        n_score = compute_fit_score(new_build, 2)
        assert w_score is not None and n_score is not None
        assert w_score > n_score

    def test_weights_sum_to_100(self):
        assert sum(WEIGHTS.values()) == 100

    def test_condition_concerns_reduce_score(self):
        no_concerns = _full_analysis(condition_concerns=False)
        serious = _full_analysis(condition_concerns=True, concern_severity="serious")
        s1 = compute_fit_score(no_concerns, 2)
        s2 = compute_fit_score(serious, 2)
        assert s1 is not None and s2 is not None
        assert s1 > s2

    def test_missing_sections_still_produce_score(self):
        """Partial analysis with only kitchen data should still return a score."""
        partial = {
            "kitchen": {"overall_quality": "modern", "hob_type": "gas"},
        }
        score = compute_fit_score(partial, 1)
        assert score is not None
        assert 0 <= score <= 100

    def test_spacious_1bed_gets_workspace_credit(self):
        """Spacious 1-bed should get workspace credit vs non-spacious 1-bed."""
        spacious = _full_analysis(space={"is_spacious_enough": True, "living_room_sqm": 25})
        compact = _full_analysis(space={"is_spacious_enough": False, "living_room_sqm": 10})
        s1 = compute_fit_score(spacious, 1)
        s2 = compute_fit_score(compact, 1)
        assert s1 is not None and s2 is not None
        assert s1 > s2


# ── compute_lifestyle_icons ────────────────────────────────────────────────────


class TestComputeLifestyleIcons:
    def test_none_analysis_returns_none(self):
        assert compute_lifestyle_icons(None, 2) is None

    def test_returns_all_five_keys(self):
        icons = compute_lifestyle_icons(_full_analysis(), 2)
        assert icons is not None
        assert set(icons.keys()) == {"workspace", "hosting", "kitchen", "vibe", "space"}

    def test_each_icon_has_state_and_tooltip(self):
        icons = compute_lifestyle_icons(_full_analysis(), 2)
        assert icons is not None
        for key, icon in icons.items():
            assert "state" in icon, f"{key} missing state"
            assert "tooltip" in icon, f"{key} missing tooltip"
            assert icon["state"] in ("good", "neutral", "concern"), f"{key} invalid state"
            assert isinstance(icon["tooltip"], str) and len(icon["tooltip"]) > 0

    # ── Workspace icon ──

    def test_workspace_good_for_2bed(self):
        icons = compute_lifestyle_icons(_full_analysis(), 2)
        assert icons is not None
        assert icons["workspace"]["state"] == "good"

    def test_workspace_concern_for_studio(self):
        analysis = _full_analysis(bedroom={"can_fit_desk": "no"})
        icons = compute_lifestyle_icons(analysis, 0)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"

    def test_workspace_good_for_spacious_1bed(self):
        analysis = _full_analysis(space={"is_spacious_enough": True})
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "good"

    def test_workspace_concern_for_compact_1bed(self):
        analysis = _full_analysis(
            space={"is_spacious_enough": False},
            bedroom={"can_fit_desk": "no"},
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"

    # ── Hosting icon ──

    def test_hosting_good_for_spacious_solid(self):
        analysis = _full_analysis(
            space={"is_spacious_enough": True},
            flooring_noise={"building_construction": "solid_brick", "has_double_glazing": "yes"},
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["hosting"]["state"] == "good"

    def test_hosting_concern_for_compact(self):
        analysis = _full_analysis(
            space={"is_spacious_enough": False},
            flooring_noise={"noise_indicators": ["road noise"]},
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["hosting"]["state"] == "concern"

    # ── Kitchen icon ──

    def test_kitchen_good_for_gas(self):
        analysis = _full_analysis(kitchen={"hob_type": "gas", "overall_quality": "modern"})
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["kitchen"]["state"] == "good"

    def test_kitchen_concern_for_electric(self):
        analysis = _full_analysis(kitchen={"hob_type": "electric", "overall_quality": "dated"})
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["kitchen"]["state"] == "concern"

    # ── Vibe icon ──

    def test_vibe_good_for_warehouse(self):
        analysis = _full_analysis(listing_extraction={"property_type": "warehouse"})
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["vibe"]["state"] == "good"

    def test_vibe_neutral_for_new_build(self):
        analysis = _full_analysis(
            listing_extraction={"property_type": "new_build"},
            highlights=[],
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["vibe"]["state"] == "neutral"

    def test_vibe_good_for_character_highlights(self):
        analysis = _full_analysis(
            listing_extraction={"property_type": "unknown"},
            highlights=["Original period features throughout"],
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["vibe"]["state"] == "good"

    # ── Space icon ──

    def test_space_good_for_spacious_with_outdoor(self):
        analysis = _full_analysis(
            space={"is_spacious_enough": True},
            outdoor_space={"has_balcony": True, "has_garden": False,
                           "has_terrace": False, "has_shared_garden": False},
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["space"]["state"] == "good"

    def test_space_concern_for_not_spacious(self):
        analysis = _full_analysis(space={"is_spacious_enough": False})
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["space"]["state"] == "concern"

    def test_space_good_for_spacious_no_outdoor(self):
        analysis = _full_analysis(
            space={"is_spacious_enough": True},
            outdoor_space={"has_balcony": False, "has_garden": False,
                           "has_terrace": False, "has_shared_garden": False},
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["space"]["state"] == "good"


# ── Edge cases ─────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_all_unknown_fields(self):
        """Analysis with all unknown/null fields should degrade gracefully."""
        analysis = {
            "kitchen": {"overall_quality": "unknown", "hob_type": "unknown"},
            "bedroom": {"can_fit_desk": "unknown"},
            "space": {"is_spacious_enough": None},
            "flooring_noise": {"building_construction": "unknown", "has_double_glazing": "unknown"},
            "listing_extraction": {"property_type": "unknown"},
            "light_space": {"ceiling_height": "unknown"},
        }
        score = compute_fit_score(analysis, 1)
        # Should return None since no dimension has real signal
        assert score is None

        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        # All should be neutral
        for icon in icons.values():
            assert icon["state"] == "neutral"

    def test_negative_living_room_sqm_ignored(self):
        """Negative sqm value shouldn't contribute to score."""
        analysis = _full_analysis(space={"living_room_sqm": -5, "is_spacious_enough": True})
        score = compute_fit_score(analysis, 2)
        assert score is not None
        assert 0 <= score <= 100

    def test_non_list_highlights_handled(self):
        """Non-list highlights shouldn't crash."""
        analysis = _full_analysis(highlights="not a list")
        score = compute_fit_score(analysis, 2)
        assert score is not None

    def test_none_outdoor_space(self):
        """None outdoor_space section should be handled."""
        analysis = _full_analysis(outdoor_space=None)
        score = compute_fit_score(analysis, 2)
        assert score is not None
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
