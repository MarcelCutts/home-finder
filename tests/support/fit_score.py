"""Build complete assessment inputs with visible per-scenario overrides."""

from __future__ import annotations

from typing import Any


def make_fit_analysis(**overrides: object) -> dict[str, Any]:
    """Build a realistic analysis_json dict with sensible defaults."""
    base: dict[str, Any] = {
        "summary": "Fixture assessment",
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
