"""Tests for Marcel Fit Score and Lifestyle Quick-Glance Icons."""

from __future__ import annotations

from typing import Any

import pytest

from home_finder.filters.fit_score import (
    WEIGHTS,
    _score_hosting,
    _score_kitchen,
    _score_vibe,
    _score_workspace,
    compute_fit_breakdown,
    compute_fit_score,
    compute_fit_score_and_breakdown,
    compute_lifestyle_icons,
    fit_tier_label,
)
from tests.support.fit_score import make_fit_analysis

# ── Helpers ────────────────────────────────────────────────────────────────────


def _dimension(analysis: dict[str, Any], bedrooms: int, key: str) -> dict[str, Any]:
    """One dimension of the public breakdown, so a test can isolate the signal it names."""
    breakdown = compute_fit_breakdown(analysis, bedrooms)
    assert breakdown is not None
    return next(dim for dim in breakdown if dim["key"] == key)


# ── Eligibility ────────────────────────────────────────────────────────────────


class TestEligibility:
    """Only a rated assessment that decodes can produce a score, breakdown or icons."""

    @pytest.mark.parametrize(
        "entry_point",
        [compute_fit_score, compute_fit_breakdown, compute_lifestyle_icons],
        ids=["score", "breakdown", "icons"],
    )
    @pytest.mark.parametrize(
        "analysis",
        [None, {}, {"kitchen": {"hob_type": "gas"}}],
        ids=["missing", "empty", "unrated-with-evidence"],
    )
    def test_unrated_assessment_has_no_score_breakdown_or_icons(self, entry_point, analysis):
        assert entry_point(analysis, 2) is None

    def test_assessment_the_decoder_rejects_gets_no_score(self):
        """A shape the historical decoder rejects cannot become a recommendation."""
        analysis = make_fit_analysis(highlights="not a list")
        score = compute_fit_score(analysis, 2)
        assert score is None


# ── compute_fit_score ──────────────────────────────────────────────────────────


class TestComputeFitScore:
    def test_ideal_2bed_scores_high(self):
        """A 2-bed with gas hob, solid brick, high ceilings, spacious should score high."""
        analysis = make_fit_analysis(
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
        analysis = make_fit_analysis(
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
        gas = make_fit_analysis(
            kitchen={
                "hob_type": "gas",
                "overall_quality": "modern",
                "has_dishwasher": "yes",
                "has_washing_machine": "yes",
            }
        )
        electric = make_fit_analysis(
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
        warehouse = make_fit_analysis(listing_extraction={"property_type": "warehouse"})
        new_build = make_fit_analysis(listing_extraction={"property_type": "new_build"})
        w_score = compute_fit_score(warehouse, 2)
        n_score = compute_fit_score(new_build, 2)
        assert w_score is not None and n_score is not None
        assert w_score > n_score

    def test_weights_sum_to_100(self):
        assert sum(WEIGHTS.values()) == 100

    def test_condition_concerns_reduce_score(self):
        no_concerns = make_fit_analysis(condition_concerns=False)
        serious = make_fit_analysis(condition_concerns=True, concern_severity="serious")
        s1 = compute_fit_score(no_concerns, 2)
        s2 = compute_fit_score(serious, 2)
        assert s1 is not None and s2 is not None
        assert s1 > s2

    def test_missing_sections_still_produce_score(self):
        """A rated assessment can still score when an optional section is absent."""
        partial = make_fit_analysis()
        partial.pop("bedroom")
        partial.pop("outdoor_space")
        score = compute_fit_score(partial, 1)
        assert score is not None
        assert 0 <= score <= 100

    def test_spaciousness_earns_workspace_credit_only_without_a_spare_bedroom(self):
        spacious = make_fit_analysis(space={"is_spacious_enough": True})
        compact = make_fit_analysis(space={"is_spacious_enough": False})
        assert (
            _dimension(spacious, 1, "workspace")["score"]
            > _dimension(compact, 1, "workspace")["score"]
        )
        assert (
            _dimension(spacious, 2, "workspace")["score"]
            == _dimension(compact, 2, "workspace")["score"]
        )


# ── compute_lifestyle_icons ────────────────────────────────────────────────────


class TestComputeLifestyleIcons:
    def test_returns_all_six_keys(self):
        icons = compute_lifestyle_icons(make_fit_analysis(), 2)
        assert icons is not None
        assert set(icons.keys()) == {"workspace", "hosting", "kitchen", "vibe", "space", "internet"}

    def test_each_icon_has_state_and_tooltip(self):
        icons = compute_lifestyle_icons(make_fit_analysis(), 2)
        assert icons is not None
        for key, icon in icons.items():
            assert "state" in icon, f"{key} missing state"
            assert "tooltip" in icon, f"{key} missing tooltip"
            assert icon["state"] in ("good", "neutral", "concern"), f"{key} invalid state"
            assert isinstance(icon["tooltip"], str) and len(icon["tooltip"]) > 0

    # ── Workspace icon ──

    def test_workspace_good_for_2bed(self):
        """The spare bedroom alone is enough, without desk space or a spacious layout."""
        analysis = make_fit_analysis(
            bedroom={"can_fit_desk": "no"}, space={"is_spacious_enough": False}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["workspace"]["state"] == "good"

    def test_workspace_concern_for_studio(self):
        analysis = make_fit_analysis(bedroom={"can_fit_desk": "no"})
        icons = compute_lifestyle_icons(analysis, 0)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"

    def test_workspace_good_for_spacious_1bed(self):
        """With desk space unknown, the spacious layout is what earns the good state."""
        analysis = make_fit_analysis(
            bedroom={"can_fit_desk": "unknown"}, space={"is_spacious_enough": True}
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "good"

    def test_workspace_concern_for_compact_1bed(self):
        analysis = make_fit_analysis(
            space={"is_spacious_enough": False},
            bedroom={"can_fit_desk": "no"},
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"

    # ── Hosting icon ──

    def test_hosting_good_for_spacious_solid(self):
        analysis = make_fit_analysis(
            space={"is_spacious_enough": True},
            flooring_noise={"building_construction": "solid_brick", "has_double_glazing": "yes"},
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["hosting"]["state"] == "good"

    def test_hosting_concern_for_compact(self):
        analysis = make_fit_analysis(space={"is_spacious_enough": False})
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["hosting"]["state"] == "concern"

    # ── Kitchen icon ──

    def test_kitchen_good_for_gas(self):
        analysis = make_fit_analysis(kitchen={"hob_type": "gas", "overall_quality": "modern"})
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["kitchen"]["state"] == "good"

    def test_kitchen_concern_for_electric(self):
        analysis = make_fit_analysis(kitchen={"hob_type": "electric", "overall_quality": "dated"})
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["kitchen"]["state"] == "concern"

    # ── Vibe icon ──

    def test_vibe_good_for_warehouse(self):
        analysis = make_fit_analysis(listing_extraction={"property_type": "warehouse"})
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["vibe"]["state"] == "good"

    def test_vibe_neutral_for_new_build(self):
        analysis = make_fit_analysis(
            listing_extraction={"property_type": "new_build"},
            highlights=[],
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["vibe"]["state"] == "neutral"

    def test_vibe_good_for_character_highlights(self):
        analysis = make_fit_analysis(
            listing_extraction={"property_type": "unknown"},
            highlights=["Original period features throughout"],
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["vibe"]["state"] == "good"

    # ── Space icon ──

    def test_space_good_for_spacious_with_outdoor(self):
        analysis = make_fit_analysis(
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
        analysis = make_fit_analysis(space={"is_spacious_enough": False})
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["space"]["state"] == "concern"

    def test_space_good_for_spacious_no_outdoor(self):
        analysis = make_fit_analysis(
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
        analysis = make_fit_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "fttp"}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["internet"]["state"] == "good"

    def test_internet_icon_standard_concern(self):
        """Standard broadband produces concern internet icon."""
        analysis = make_fit_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "standard"}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["internet"]["state"] == "concern"

    def test_internet_icon_unknown_neutral(self):
        """Unknown broadband produces neutral internet icon."""
        analysis = make_fit_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "unknown"}
        )
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None
        assert icons["internet"]["state"] == "neutral"

    # ── Workspace icon with office_separation ──

    def test_workspace_icon_prefers_office_separation(self):
        """A compact 1-bed without desk space is a concern unless office_separation says not."""
        analysis = make_fit_analysis(
            bedroom={"office_separation": "dedicated_room", "can_fit_desk": "no"},
            space={"is_spacious_enough": False},
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"] == {"state": "good", "tooltip": "Dedicated office room"}

    def test_workspace_icon_separate_area_good(self):
        """Separate work area produces good workspace icon."""
        analysis = make_fit_analysis(
            bedroom={"office_separation": "separate_area", "can_fit_desk": "no"},
            space={"is_spacious_enough": False},
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "good"

    def test_workspace_icon_shared_space_concern_1bed(self):
        """Shared space produces concern for 1-bed."""
        analysis = make_fit_analysis(
            bedroom={"office_separation": "shared_space", "can_fit_desk": "yes"}
        )
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"

    def test_workspace_icon_none_concern(self):
        """No viable workspace produces concern icon."""
        analysis = make_fit_analysis(bedroom={"office_separation": "none", "can_fit_desk": "no"})
        icons = compute_lifestyle_icons(analysis, 1)
        assert icons is not None
        assert icons["workspace"]["state"] == "concern"


# ── Effects of individual signals ──────────────────────────────────────────────


class TestSignalEffects:
    def test_negative_living_room_sqm_is_treated_as_absent(self):
        """A negative area adds no hosting points, signal or factor."""
        negative = make_fit_analysis(space={"living_room_sqm": -5, "is_spacious_enough": True})
        absent = make_fit_analysis(space={"is_spacious_enough": True})
        del absent["space"]["living_room_sqm"]
        assert _dimension(negative, 2, "hosting") == _dimension(absent, 2, "hosting")

    def test_dedicated_office_boosts_workspace(self):
        """dedicated_room office separation scores higher than shared_space."""
        dedicated = make_fit_analysis(
            bedroom={"office_separation": "dedicated_room", "can_fit_desk": "yes"}
        )
        shared = make_fit_analysis(
            bedroom={"office_separation": "shared_space", "can_fit_desk": "yes"}
        )
        dedicated_score = compute_fit_score(dedicated, 2)
        shared_score = compute_fit_score(shared, 2)
        assert dedicated_score is not None
        assert shared_score is not None
        assert dedicated_score > shared_score

    def test_fttp_broadband_boosts_workspace(self):
        """FTTP broadband adds to workspace score."""
        fttp = make_fit_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "fttp"}
        )
        unknown = make_fit_analysis(
            listing_extraction={"property_type": "victorian", "broadband_type": "unknown"}
        )
        fttp_score = compute_fit_score(fttp, 2)
        unknown_score = compute_fit_score(unknown, 2)
        assert fttp_score is not None
        assert unknown_score is not None
        assert fttp_score > unknown_score

    def test_excellent_hosting_layout_boosts_hosting(self):
        """Excellent hosting layout scores higher than poor in the hosting dimension itself."""
        excellent = make_fit_analysis(
            space={"is_spacious_enough": True, "living_room_sqm": 20, "hosting_layout": "excellent"}
        )
        poor = make_fit_analysis(
            space={"is_spacious_enough": True, "living_room_sqm": 20, "hosting_layout": "poor"}
        )
        assert (
            _dimension(excellent, 2, "hosting")["score"] > _dimension(poor, 2, "hosting")["score"]
        )

    @pytest.mark.parametrize("postcode", ["N15 3RH", "N16 3RH", "E8 3RH"])
    def test_the_postcode_no_longer_moves_the_score(self, postcode):
        """Areas the hand-written guide called tolerant or noise-sensitive score alike."""
        analysis = make_fit_analysis()
        located = compute_fit_score_and_breakdown(analysis, 2, postcode=postcode)
        assert located == compute_fit_score_and_breakdown(analysis, 2)
        factors = [f["label"] for dim in located[1] or [] for f in dim["factors"]]
        assert not [label for label in factors if "Area" in label]

    def test_low_hosting_noise_boosts_sound_and_hosting(self):
        """Low hosting noise risk benefits both sound and hosting dimensions."""
        low = make_fit_analysis(
            flooring_noise={
                "has_double_glazing": "yes",
                "building_construction": "solid_brick",
                "noise_indicators": [],
                "hosting_noise_risk": "low",
            }
        )
        high = make_fit_analysis(
            flooring_noise={
                "has_double_glazing": "yes",
                "building_construction": "solid_brick",
                "noise_indicators": [],
                "hosting_noise_risk": "high",
            }
        )
        for key in ("sound", "hosting"):
            assert _dimension(low, 2, key)["score"] > _dimension(high, 2, key)["score"], key

    def test_null_outdoor_space_still_gets_score_and_icons(self):
        """A null outdoor section is optional evidence, not a decoding failure."""
        analysis = make_fit_analysis(outdoor_space=None)
        score = compute_fit_score(analysis, 2)
        assert score is not None
        icons = compute_lifestyle_icons(analysis, 2)
        assert icons is not None


# ── compute_fit_breakdown ─────────────────────────────────────────────────────


class TestComputeFitBreakdown:
    def test_lists_every_weighted_dimension_in_order_with_label_and_weight(self):
        result = compute_fit_breakdown(make_fit_analysis(), 2)
        assert result is not None
        assert [(d["key"], d["label"], d["weight"]) for d in result] == [
            ("workspace", "Workspace", 25),
            ("hosting", "Hosting", 20),
            ("sound", "Sound", 15),
            ("kitchen", "Kitchen", 15),
            ("vibe", "Vibe", 15),
            ("condition", "Condition", 10),
        ]

    def test_each_dimension_has_required_fields(self):
        result = compute_fit_breakdown(make_fit_analysis(), 2)
        assert result is not None
        for dim in result:
            assert "key" in dim
            assert "label" in dim
            assert "score" in dim
            assert "weight" in dim
            assert "confidence" in dim
            assert "factors" in dim
            assert isinstance(dim["score"], int)
            assert 0 <= dim["score"] <= 100
            assert isinstance(dim["weight"], int)
            assert 0.0 <= dim["confidence"] <= 1.0
            assert isinstance(dim["factors"], list)


# ── Factor tags ──────────────────────────────────────────────────────────────


class TestFactorTags:
    """Tests that each dimension returns meaningful factor breakdowns."""

    def test_all_dimensions_have_factors(self):
        """Every dimension with confidence > 0 should have at least one factor."""
        result = compute_fit_breakdown(make_fit_analysis(), 2)
        assert result is not None
        for dim in result:
            if dim["confidence"] > 0:
                assert len(dim["factors"]) > 0, f"{dim['key']} has no factors"

    def test_factor_states_are_valid(self):
        """All factor states must be 'earned', 'missed', or 'unknown'."""
        result = compute_fit_breakdown(make_fit_analysis(), 2)
        assert result is not None
        for dim in result:
            for f in dim["factors"]:
                assert f["state"] in ("earned", "missed", "unknown"), (
                    f"{dim['key']} factor '{f['label']}' has invalid state '{f['state']}'"
                )
                assert isinstance(f["label"], str) and len(f["label"]) > 0

    def test_kitchen_factors_gas_modern(self):
        """Kitchen with gas hob, modern quality, dishwasher, washing machine."""
        analysis = {
            "kitchen": {
                "hob_type": "gas",
                "overall_quality": "modern",
                "has_dishwasher": "yes",
                "has_washing_machine": "yes",
            }
        }
        result = _score_kitchen(analysis, 2)
        assert result.factors == [
            {"label": "Gas hob", "state": "earned"},
            {"label": "Modern kitchen", "state": "earned"},
            {"label": "Dishwasher", "state": "earned"},
            {"label": "Washing machine", "state": "earned"},
        ]

    def test_kitchen_factors_electric_dated_without_appliances(self):
        """An electric hob, a dated kitchen and absent appliances are all missed."""
        analysis = {
            "kitchen": {
                "hob_type": "electric",
                "overall_quality": "dated",
                "has_dishwasher": "no",
                "has_washing_machine": "no",
            }
        }
        result = _score_kitchen(analysis, 2)
        assert result.factors == [
            {"label": "Electric hob (not gas/induction)", "state": "missed"},
            {"label": "Dated kitchen", "state": "missed"},
            {"label": "No dishwasher", "state": "missed"},
            {"label": "No washing machine", "state": "missed"},
        ]

    def test_kitchen_factors_without_kitchen_data_are_unknown(self):
        analysis: dict[str, Any] = {"kitchen": {}}
        result = _score_kitchen(analysis, 2)
        assert result.factors == [
            {"label": "Gas/induction hob", "state": "unknown"},
            {"label": "Kitchen quality", "state": "unknown"},
            {"label": "Dishwasher", "state": "unknown"},
            {"label": "Washing machine", "state": "unknown"},
        ]

    def test_workspace_factors_2bed_dedicated_fttp(self):
        """2-bed with dedicated office and FTTP shows all earned."""
        analysis = {
            "bedroom": {"office_separation": "dedicated_room", "can_fit_desk": "yes"},
            "listing_extraction": {"broadband_type": "fttp"},
        }
        result = _score_workspace(analysis, 2)
        labels = {f["label"] for f in result.factors}
        states = {f["label"]: f["state"] for f in result.factors}
        assert "2+ bedrooms" in labels
        assert states["2+ bedrooms"] == "earned"
        assert "Dedicated office room" in labels
        assert states["Dedicated office room"] == "earned"
        assert "Full fibre (FTTP)" in labels
        assert states["Full fibre (FTTP)"] == "earned"

    def test_workspace_factors_studio(self):
        """Studio shows missed bedroom factor."""
        analysis = {"bedroom": {"can_fit_desk": "no"}}
        result = _score_workspace(analysis, 0)
        states = {f["label"]: f["state"] for f in result.factors}
        assert "Studio (no separation)" in states
        assert states["Studio (no separation)"] == "missed"


# ── Hosting scorer (direct unit tests) ────────────────────────────────────────


def _hosting_analysis(**overrides: object) -> dict[str, Any]:
    """Build a minimal analysis dict for hosting scorer testing."""
    base: dict[str, Any] = {
        "space": {},
        "light_space": {},
        "outdoor_space": {},
        "flooring_noise": {},
    }
    for key, val in overrides.items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            base[key].update(val)
        else:
            base[key] = val
    return base


class TestHostingScorer:
    """Direct tests for _score_hosting() — exact point values and signal counts."""

    def test_empty_analysis_zero_confidence(self):
        result = _score_hosting({}, 2)
        assert result.score == 0
        assert result.confidence == 0.0

    def test_spacious_only(self):
        analysis = _hosting_analysis(space={"is_spacious_enough": True})
        result = _score_hosting(analysis, 2)
        assert result.score == 25
        assert result.confidence == 0.4  # 1 signal

    def test_not_spacious_2bed_gets_partial_bonus(self):
        """2-bed with is_spacious_enough=False gets partial +15 (preference rule)."""
        analysis = _hosting_analysis(space={"is_spacious_enough": False})
        result = _score_hosting(analysis, 2)
        assert result.score == 15
        assert result.confidence == 0.4  # 1 signal

    def test_not_spacious_1bed_no_score(self):
        """1-bed with is_spacious_enough=False gets 0 points."""
        analysis = _hosting_analysis(space={"is_spacious_enough": False})
        result = _score_hosting(analysis, 1)
        assert result.score == 0
        assert result.confidence == 0.4  # 1 signal (counts but no points)

    def test_living_sqm_graduated(self):
        """Living room sqm gives 0-30 points graduated between 10-25sqm."""
        # At 10sqm: 0 points
        r10 = _score_hosting(_hosting_analysis(space={"living_room_sqm": 10}), 2)
        assert r10.score == 0
        # At 25sqm: 30 points
        r25 = _score_hosting(_hosting_analysis(space={"living_room_sqm": 25}), 2)
        assert r25.score == 30
        # At 17.5sqm: 15 points (midpoint)
        r17 = _score_hosting(_hosting_analysis(space={"living_room_sqm": 17.5}), 2)
        assert r17.score == 15

    def test_hosting_layout_excellent(self):
        analysis = _hosting_analysis(space={"hosting_layout": "excellent"})
        result = _score_hosting(analysis, 2)
        assert result.score == 25

    def test_hosting_layout_good(self):
        analysis = _hosting_analysis(space={"hosting_layout": "good"})
        result = _score_hosting(analysis, 2)
        assert result.score == 15

    def test_hosting_layout_awkward_zero_score(self):
        analysis = _hosting_analysis(space={"hosting_layout": "awkward"})
        result = _score_hosting(analysis, 2)
        assert result.score == 0
        assert result.confidence == 0.4  # signal counted

    def test_hosting_noise_risk_low(self):
        analysis = _hosting_analysis(flooring_noise={"hosting_noise_risk": "low"})
        result = _score_hosting(analysis, 2)
        assert result.score == 10

    def test_area_context_in_the_assessment_is_not_scored(self):
        """The hand-written area hosting guide no longer counts, even if supplied inline."""
        for rating in ("high", "moderate", "low"):
            result = _score_hosting(_hosting_analysis(_area_hosting_tolerance=rating), 2)
            assert (result.score, result.confidence, result.factors) == (0, 0.0, [])

    def test_all_positive_signals(self):
        """All positive signals sum correctly."""
        analysis = _hosting_analysis(
            space={
                "is_spacious_enough": True,  # +25
                "living_room_sqm": 25,  # +30
                "hosting_layout": "excellent",  # +25
            },
            light_space={"feels_spacious": True},  # +10
            outdoor_space={"has_balcony": True},  # +15
            flooring_noise={"hosting_noise_risk": "low"},  # +10
        )
        result = _score_hosting(analysis, 2)
        # Total: 25+30+10+15+25+10 = 115, clamped to 100
        assert result.score == 100
        assert result.confidence == 1.0  # 6 signals, capped at 1.0

    def test_confidence_scales_with_signals(self):
        """Confidence = min(1.0, signals * 0.4)."""
        # 1 signal: 0.4
        r1 = _score_hosting(_hosting_analysis(space={"is_spacious_enough": True}), 2)
        assert r1.confidence == 0.4
        # 2 signals: 0.8
        r2 = _score_hosting(
            _hosting_analysis(
                space={"is_spacious_enough": True},
                light_space={"feels_spacious": True},
            ),
            2,
        )
        assert r2.confidence == 0.8
        # 3+ signals: 1.0
        r3 = _score_hosting(
            _hosting_analysis(
                space={"is_spacious_enough": True, "hosting_layout": "good"},
                light_space={"feels_spacious": True},
            ),
            2,
        )
        assert r3.confidence == 1.0


# ── Vibe scorer (multi-cluster) ──────────────────────────────────────────────


def _vibe_analysis(**overrides: object) -> dict[str, Any]:
    """Build a minimal analysis dict for vibe scorer testing."""
    base: dict[str, Any] = {
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
    """Tests for the multi-cluster _score_vibe()."""

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

    # ── Confidence scaling (zero and one cluster are covered above) ──

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

    def test_highlights_given_as_a_string_are_ignored(self):
        """Read as one item or by substring, this string would earn view and highlight points."""
        analysis = _vibe_analysis(highlights="Canal views")
        result = _score_vibe(analysis, 2)
        assert result.score == 0

    def test_lowlights_given_as_a_string_are_ignored(self):
        """Read as one item, this string would cost the period-features highlight 8 points."""
        analysis = _vibe_analysis(highlights=["Period features"], lowlights="Needs updating")
        result = _score_vibe(analysis, 2)
        assert result.score == 10

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

    def test_warehouse_with_every_positive_signal_is_capped_at_100(self):
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


class TestFitTierLabel:
    """Tests for fit_tier_label."""

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (90, "Great Fit"),
            (80, "Great Fit"),
            (70, "Good Fit"),
            (60, "Good Fit"),
            (50, "Okay"),
            (40, "Okay"),
            (30, "Poor Fit"),
            (10, "Poor Fit"),
        ],
    )
    def test_fit_tier_label(self, score: int, expected: str) -> None:
        assert fit_tier_label(score) == expected
