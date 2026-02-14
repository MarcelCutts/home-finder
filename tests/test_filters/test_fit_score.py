"""Tests for Marcel Fit Score and Lifestyle Quick-Glance Icons."""

from __future__ import annotations

from home_finder.filters.fit_score import (
    WEIGHTS,
    _score_vibe,
    compute_fit_breakdown,
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
            "window_sizes": "medium",
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
            "primary_flooring": "hardwood",
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
            kitchen={
                "overall_quality": "modern",
                "hob_type": "gas",
                "has_dishwasher": "yes",
                "has_washing_machine": "yes",
            },
            flooring_noise={
                "building_construction": "solid_brick",
                "has_double_glazing": "yes",
                "noise_indicators": [],
            },
            light_space={"ceiling_height": "high", "feels_spacious": True, "floor_level": "top"},
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
        gas = _full_analysis(
            kitchen={
                "hob_type": "gas",
                "overall_quality": "modern",
                "has_dishwasher": "yes",
                "has_washing_machine": "yes",
            }
        )
        electric = _full_analysis(
            kitchen={
                "hob_type": "electric",
                "overall_quality": "modern",
                "has_dishwasher": "yes",
                "has_washing_machine": "yes",
            }
        )
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

    def test_returns_all_six_keys(self):
        icons = compute_lifestyle_icons(_full_analysis(), 2)
        assert icons is not None
        assert set(icons.keys()) == {"workspace", "hosting", "kitchen", "vibe", "space", "internet"}

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
            outdoor_space={
                "has_balcony": True,
                "has_garden": False,
                "has_terrace": False,
                "has_shared_garden": False,
            },
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
            outdoor_space={
                "has_balcony": False,
                "has_garden": False,
                "has_terrace": False,
                "has_shared_garden": False,
            },
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["space"]["state"] == "good"

    # ── Internet icon ──

    def test_internet_icon_fttp_good(self):
        """FTTP broadband produces good internet icon."""
        analysis = _full_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "fttp"}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["internet"]["state"] == "good"

    def test_internet_icon_standard_concern(self):
        """Standard broadband produces concern internet icon."""
        analysis = _full_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "standard"}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["internet"]["state"] == "concern"

    def test_internet_icon_unknown_neutral(self):
        """Unknown broadband produces neutral internet icon."""
        analysis = _full_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "unknown"}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["internet"]["state"] == "neutral"

    # ── Workspace icon with office_separation ──

    def test_workspace_icon_prefers_office_separation(self):
        """Workspace icon uses office_separation when available."""
        analysis = _full_analysis(
            bedroom={"office_separation": "dedicated_room", "can_fit_desk": "yes"}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["workspace"]["state"] == "good"
        assert "office" in icons["workspace"]["tooltip"].lower()

    def test_workspace_icon_separate_area_good(self):
        """Separate work area produces good workspace icon."""
        analysis = _full_analysis(
            bedroom={"office_separation": "separate_area", "can_fit_desk": "yes"}
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "good"

    def test_workspace_icon_shared_space_concern_1bed(self):
        """Shared space produces concern for 1-bed."""
        analysis = _full_analysis(
            bedroom={"office_separation": "shared_space", "can_fit_desk": "yes"}
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"

    def test_workspace_icon_none_concern(self):
        """No viable workspace produces concern icon."""
        analysis = _full_analysis(bedroom={"office_separation": "none", "can_fit_desk": "no"})
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"


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

    def test_dedicated_office_boosts_workspace(self):
        """dedicated_room office separation scores higher than shared_space."""
        dedicated = _full_analysis(
            bedroom={"office_separation": "dedicated_room", "can_fit_desk": "yes"}
        )
        shared = _full_analysis(
            bedroom={"office_separation": "shared_space", "can_fit_desk": "yes"}
        )
        assert compute_fit_score(dedicated, 2) > compute_fit_score(shared, 2)

    def test_fttp_broadband_boosts_workspace(self):
        """FTTP broadband adds to workspace score."""
        fttp = _full_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "fttp"}
        )
        unknown = _full_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "unknown"}
        )
        assert compute_fit_score(fttp, 2) > compute_fit_score(unknown, 2)

    def test_excellent_hosting_layout_boosts_hosting(self):
        """Excellent hosting layout scores higher than poor."""
        excellent = _full_analysis(
            space={"is_spacious_enough": True, "living_room_sqm": 20, "hosting_layout": "excellent"}
        )
        poor = _full_analysis(
            space={"is_spacious_enough": True, "living_room_sqm": 20, "hosting_layout": "poor"}
        )
        assert compute_fit_score(excellent, 2) > compute_fit_score(poor, 2)

    def test_low_hosting_noise_boosts_sound_and_hosting(self):
        """Low hosting noise risk benefits both sound and hosting dimensions."""
        low = _full_analysis(
            flooring_noise={
                "has_double_glazing": "yes",
                "building_construction": "solid_brick",
                "noise_indicators": [],
                "hosting_noise_risk": "low",
            }
        )
        high = _full_analysis(
            flooring_noise={
                "has_double_glazing": "yes",
                "building_construction": "solid_brick",
                "noise_indicators": [],
                "hosting_noise_risk": "high",
            }
        )
        assert compute_fit_score(low, 2) > compute_fit_score(high, 2)

    def test_unknown_new_fields_still_produce_score(self):
        """Properties with unknown new fields produce a valid score."""
        analysis = _full_analysis()
        # Simulate old analysis without new fields
        analysis["space"].pop("hosting_layout", None)
        analysis["bedroom"].pop("office_separation", None)
        analysis["flooring_noise"].pop("hosting_noise_risk", None)
        analysis["listing_extraction"].pop("broadband_type", None)
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


# ── compute_fit_breakdown ─────────────────────────────────────────────────────


class TestComputeFitBreakdown:
    def test_none_analysis_returns_none(self):
        assert compute_fit_breakdown(None, 2) is None

    def test_empty_analysis_returns_none(self):
        assert compute_fit_breakdown({}, 2) is None

    def test_returns_list_for_valid_analysis(self):
        result = compute_fit_breakdown(_full_analysis(), 2)
        assert isinstance(result, list)

    def test_returns_six_dimensions(self):
        result = compute_fit_breakdown(_full_analysis(), 2)
        assert result is not None
        assert len(result) == 6

    def test_dimensions_match_weights_keys(self):
        result = compute_fit_breakdown(_full_analysis(), 2)
        assert result is not None
        keys = {d["key"] for d in result}
        assert keys == set(WEIGHTS.keys())

    def test_each_dimension_has_required_fields(self):
        result = compute_fit_breakdown(_full_analysis(), 2)
        assert result is not None
        for dim in result:
            assert "key" in dim
            assert "label" in dim
            assert "score" in dim
            assert "weight" in dim
            assert "confidence" in dim
            assert isinstance(dim["score"], int)
            assert 0 <= dim["score"] <= 100
            assert isinstance(dim["weight"], int)
            assert 0.0 <= dim["confidence"] <= 1.0

    def test_weights_match_global_weights(self):
        result = compute_fit_breakdown(_full_analysis(), 2)
        assert result is not None
        for dim in result:
            assert dim["weight"] == int(WEIGHTS[dim["key"]])

    def test_labels_are_human_readable(self):
        result = compute_fit_breakdown(_full_analysis(), 2)
        assert result is not None
        labels = {d["label"] for d in result}
        assert "Workspace" in labels
        assert "Kitchen" in labels
        assert "Sound" in labels

    def test_zero_confidence_analysis_returns_none(self):
        """Analysis with only unknown fields should return None."""
        analysis = {
            "kitchen": {"overall_quality": "unknown", "hob_type": "unknown"},
            "bedroom": {"can_fit_desk": "unknown"},
            "space": {"is_spacious_enough": None},
            "flooring_noise": {"building_construction": "unknown", "has_double_glazing": "unknown"},
            "listing_extraction": {"property_type": "unknown"},
            "light_space": {"ceiling_height": "unknown"},
        }
        assert compute_fit_breakdown(analysis, 1) is None


# ── Vibe scorer (multi-cluster) ──────────────────────────────────────────────


def _vibe_analysis(**overrides: object) -> dict:
    """Build a minimal analysis dict for vibe scorer testing."""
    base: dict = {
        "listing_extraction": {"property_type": "unknown"},
        "light_space": {},
        "flooring_noise": {},
        "space": {},
        "highlights": [],
        "lowlights": [],
    }
    for key, val in overrides.items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            base[key].update(val)
        else:
            base[key] = val
    return base


class TestVibeScorer:
    """Tests for the rewritten multi-cluster _score_vibe()."""

    def test_warehouse_all_positives_scores_high(self):
        """Warehouse with all positive signals should score ~90+."""
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "warehouse"},
            light_space={
                "natural_light": "excellent",
                "window_sizes": "large",
                "ceiling_height": "high",
                "feels_spacious": True,
                "floor_level": "top",
            },
            flooring_noise={
                "primary_flooring": "hardwood",
                "building_construction": "solid_brick",
            },
            space={"hosting_layout": "excellent"},
            highlights=[
                "Period features",
                "Open-plan layout",
                "Floor-to-ceiling windows",
                "Canal views",
            ],
        )
        result = _score_vibe(analysis, 2)
        assert result.score >= 90

    def test_nice_victorian_scores_mid_high(self):
        """Victorian with period features and good light should score ~70s."""
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "victorian"},
            light_space={
                "natural_light": "good",
                "ceiling_height": "high",
                "feels_spacious": True,
                "floor_level": "upper",
            },
            flooring_noise={
                "primary_flooring": "hardwood",
                "building_construction": "solid_brick",
            },
            highlights=["Period features"],
        )
        result = _score_vibe(analysis, 2)
        assert 60 <= result.score <= 85

    def test_decent_new_build_scores_moderate(self):
        """New-build with some positives (light, layout) should score ~30s."""
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "new_build"},
            light_space={
                "natural_light": "excellent",
                "window_sizes": "large",
            },
            space={"hosting_layout": "good"},
        )
        result = _score_vibe(analysis, 2)
        assert 25 <= result.score <= 45

    def test_dark_basement_negatives_clamped_to_zero(self):
        """Dark basement with all negatives should clamp to 0."""
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "purpose_built"},
            light_space={
                "ceiling_height": "low",
                "floor_level": "basement",
            },
            flooring_noise={
                "primary_flooring": "carpet",
                "building_construction": "timber_frame",
            },
            space={"hosting_layout": "poor"},
            lowlights=["Needs updating", "Compact living room"],
        )
        result = _score_vibe(analysis, 2)
        assert result.score == 0

    def test_empty_analysis_zero_with_zero_confidence(self):
        """Empty/unknown analysis should return 0 score and 0 confidence."""
        result = _score_vibe({}, 2)
        assert result.score == 0
        assert result.confidence == 0.0

    def test_unknown_fields_zero_with_zero_confidence(self):
        """Analysis with only unknown values should return 0 confidence."""
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "unknown"},
            light_space={"ceiling_height": "unknown", "natural_light": "unknown"},
            flooring_noise={"primary_flooring": "unknown", "building_construction": "unknown"},
        )
        result = _score_vibe(analysis, 2)
        assert result.score == 0
        assert result.confidence == 0.0

    # ── Cluster independence ──

    def test_cluster1_architecture_only(self):
        """Only architectural character signals should contribute."""
        analysis = _vibe_analysis(listing_extraction={"property_type": "warehouse"})
        result = _score_vibe(analysis, 2)
        assert result.score == 35
        assert result.confidence == 0.25  # 1 cluster

    def test_cluster2_light_only(self):
        """Only light signals should contribute."""
        analysis = _vibe_analysis(
            light_space={
                "natural_light": "excellent",
                "window_sizes": "large",
                "ceiling_height": "high",
            }
        )
        result = _score_vibe(analysis, 2)
        assert result.score == 35  # 15 + 10 + 10
        assert result.confidence == 0.25

    def test_cluster3_material_only(self):
        """Only material signals should contribute."""
        analysis = _vibe_analysis(
            flooring_noise={"primary_flooring": "hardwood", "building_construction": "solid_brick"}
        )
        result = _score_vibe(analysis, 2)
        assert result.score == 20  # 12 + 8
        assert result.confidence == 0.25

    def test_cluster4_position_only(self):
        """Only position signals should contribute."""
        analysis = _vibe_analysis(light_space={"floor_level": "top"})
        result = _score_vibe(analysis, 2)
        assert result.score == 10
        assert result.confidence == 0.25

    def test_cluster4_view_highlights(self):
        """View highlights contribute to position (capped at 8) AND highlight cluster."""
        analysis = _vibe_analysis(highlights=["Canal views", "Park views"])
        result = _score_vibe(analysis, 2)
        # Cluster 4: 6+6 = 12 capped at 8
        # Cluster 6: "Canal views" +6 + "Park views" +4 = 10
        assert result.score == 18

    def test_cluster5_layout_only(self):
        """Only layout signals should contribute."""
        analysis = _vibe_analysis(space={"hosting_layout": "excellent"})
        result = _score_vibe(analysis, 2)
        assert result.score == 12
        assert result.confidence == 0.25

    def test_cluster6_highlights_only(self):
        """Only highlight signals should contribute (capped at 20)."""
        analysis = _vibe_analysis(
            highlights=["Period features", "Open-plan layout", "Floor-to-ceiling windows"]
        )
        result = _score_vibe(analysis, 2)
        # 10 + 6 + 8 = 24, but capped at 20
        assert result.score == 20

    def test_cluster6_lowlights_reduce_score(self):
        """Lowlight signals should reduce the highlight cluster score."""
        analysis = _vibe_analysis(
            highlights=["Period features"],
            lowlights=["Needs updating"],
        )
        result = _score_vibe(analysis, 2)
        # highlight cluster: 10 + (-8) = 2
        assert result.score == 2

    # ── Confidence scaling ──

    def test_confidence_zero_clusters(self):
        result = _score_vibe({}, 2)
        assert result.confidence == 0.0

    def test_confidence_one_cluster(self):
        analysis = _vibe_analysis(listing_extraction={"property_type": "warehouse"})
        result = _score_vibe(analysis, 2)
        assert result.confidence == 0.25

    def test_confidence_two_clusters(self):
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "warehouse"},
            light_space={"natural_light": "excellent"},
        )
        result = _score_vibe(analysis, 2)
        assert result.confidence == 0.5

    def test_confidence_three_clusters(self):
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "warehouse"},
            light_space={"natural_light": "excellent"},
            flooring_noise={"primary_flooring": "hardwood"},
        )
        result = _score_vibe(analysis, 2)
        assert result.confidence == 0.7

    def test_confidence_four_plus_clusters(self):
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "warehouse"},
            light_space={"natural_light": "excellent", "floor_level": "top"},
            flooring_noise={"primary_flooring": "hardwood"},
            space={"hosting_layout": "excellent"},
        )
        result = _score_vibe(analysis, 2)
        assert result.confidence == 1.0

    # ── Edge cases ──

    def test_non_list_highlights_handled(self):
        """Non-list highlights shouldn't crash."""
        analysis = _vibe_analysis(highlights="not a list")
        result = _score_vibe(analysis, 2)
        assert result.score == 0

    def test_non_list_lowlights_handled(self):
        """Non-list lowlights shouldn't crash."""
        analysis = _vibe_analysis(lowlights="not a list")
        result = _score_vibe(analysis, 2)
        assert result.score == 0

    def test_period_conversion_scores_between_warehouse_and_victorian(self):
        """Period conversion should score between warehouse and Victorian."""
        warehouse = _score_vibe(
            _vibe_analysis(listing_extraction={"property_type": "warehouse"}), 2
        )
        period = _score_vibe(
            _vibe_analysis(listing_extraction={"property_type": "period_conversion"}), 2
        )
        victorian = _score_vibe(
            _vibe_analysis(listing_extraction={"property_type": "victorian"}), 2
        )
        assert warehouse.score > period.score > victorian.score

    def test_score_never_exceeds_100(self):
        """Even with all max signals, score shouldn't exceed 100."""
        analysis = _vibe_analysis(
            listing_extraction={"property_type": "warehouse"},
            light_space={
                "natural_light": "excellent",
                "window_sizes": "large",
                "ceiling_height": "high",
                "feels_spacious": True,
                "floor_level": "top",
            },
            flooring_noise={
                "primary_flooring": "hardwood",
                "building_construction": "solid_brick",
            },
            space={"hosting_layout": "excellent"},
            highlights=[
                "Period features",
                "Open-plan layout",
                "Floor-to-ceiling windows",
                "Spacious living room",
                "Canal views",
                "Park views",
                "Roof terrace",
                "Recently refurbished",
            ],
        )
        result = _score_vibe(analysis, 2)
        assert result.score == 100
