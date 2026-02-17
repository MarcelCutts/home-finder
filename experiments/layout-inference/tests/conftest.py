"""Synthetic fixtures for layout inference experiment tests.

These fixtures provide deterministic test data so tests can run
without a real database or API calls.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def sample_ground_truth() -> list[dict]:
    """Synthetic ground truth entries."""
    return [
        {
            "unique_id": "rightmove:1001",
            "source": "rightmove",
            "bedrooms": 2,
            "price_pcm": 1800,
            "postcode": "E8 3RH",
            "title": "2 bed flat in Dalston",
            "gallery_count": 12,
            "floorplan_count": 1,
            "cache_dir": "/tmp/fake_cache/rightmove_1001",
            "ground_truth": {
                "living_room_sqm": 18.0,
                "is_spacious_enough": True,
                "hosting_layout": "good",
                "confidence": "high",
                "office_separation": "dedicated_room",
            },
        },
        {
            "unique_id": "openrent:2002",
            "source": "openrent",
            "bedrooms": 1,
            "price_pcm": 1500,
            "postcode": "N16 8QA",
            "title": "1 bed flat in Stoke Newington",
            "gallery_count": 8,
            "floorplan_count": 1,
            "cache_dir": "/tmp/fake_cache/openrent_2002",
            "ground_truth": {
                "living_room_sqm": 22.0,
                "is_spacious_enough": True,
                "hosting_layout": "excellent",
                "confidence": "high",
                "office_separation": "separate_area",
            },
        },
        {
            "unique_id": "zoopla:3003",
            "source": "zoopla",
            "bedrooms": 2,
            "price_pcm": 2200,
            "postcode": "E5 0NP",
            "title": "2 bed warehouse conversion",
            "gallery_count": 15,
            "floorplan_count": 1,
            "cache_dir": "/tmp/fake_cache/zoopla_3003",
            "ground_truth": {
                "living_room_sqm": 30.0,
                "is_spacious_enough": True,
                "hosting_layout": "excellent",
                "confidence": "high",
                "office_separation": "dedicated_room",
            },
        },
        {
            "unique_id": "rightmove:4004",
            "source": "rightmove",
            "bedrooms": 1,
            "price_pcm": 1400,
            "postcode": "N4 2HA",
            "title": "1 bed flat in Finsbury Park",
            "gallery_count": 6,
            "floorplan_count": 1,
            "cache_dir": "/tmp/fake_cache/rightmove_4004",
            "ground_truth": {
                "living_room_sqm": 14.0,
                "is_spacious_enough": False,
                "hosting_layout": "awkward",
                "confidence": "medium",
                "office_separation": "shared_space",
            },
        },
        {
            "unique_id": "onthemarket:5005",
            "source": "onthemarket",
            "bedrooms": 2,
            "price_pcm": 1950,
            "postcode": "E8 1HN",
            "title": "2 bed Victorian flat",
            "gallery_count": 10,
            "floorplan_count": 1,
            "cache_dir": "/tmp/fake_cache/onthemarket_5005",
            "ground_truth": {
                "living_room_sqm": 25.0,
                "is_spacious_enough": True,
                "hosting_layout": "good",
                "confidence": "high",
                "office_separation": "dedicated_room",
            },
        },
    ]


def _make_inference(gt: dict, sqm_offset: float = 0, same_spacious: bool = True, same_hosting: bool = True, same_office: bool = True) -> dict:
    """Create an inference result entry from a ground truth entry with configurable accuracy."""
    inf_sqm = gt["ground_truth"]["living_room_sqm"] + sqm_offset if gt["ground_truth"]["living_room_sqm"] is not None else None
    return {
        "unique_id": gt["unique_id"],
        "gallery_count_used": gt["gallery_count"],
        "max_gallery_cap": None,
        "prompt_variant": None,
        "inference": {
            "living_room_sqm": inf_sqm,
            "is_spacious_enough": gt["ground_truth"]["is_spacious_enough"] if same_spacious else not gt["ground_truth"]["is_spacious_enough"],
            "hosting_layout": gt["ground_truth"]["hosting_layout"] if same_hosting else "poor",
            "confidence": "medium",  # Photo-only should typically be lower confidence
            "office_separation": gt["ground_truth"]["office_separation"] if same_office else "unknown",
        },
        "ground_truth": gt["ground_truth"],
    }


@pytest.fixture()
def perfect_inference(sample_ground_truth: list[dict]) -> list[dict]:
    """Inference results that perfectly match ground truth (0 error)."""
    return [_make_inference(gt, sqm_offset=0) for gt in sample_ground_truth]


@pytest.fixture()
def good_inference(sample_ground_truth: list[dict]) -> list[dict]:
    """Inference results with small errors (within full-go threshold)."""
    offsets = [2.0, -3.0, 4.0, -1.0, 3.0]
    return [
        _make_inference(gt, sqm_offset=offset)
        for gt, offset in zip(sample_ground_truth, offsets)
    ]


@pytest.fixture()
def mediocre_inference(sample_ground_truth: list[dict]) -> list[dict]:
    """Inference results with moderate errors (qualitative-only territory)."""
    offsets = [7.0, -8.0, 5.0, -6.0, 9.0]
    return [
        _make_inference(gt, sqm_offset=offset)
        for gt, offset in zip(sample_ground_truth, offsets)
    ]


@pytest.fixture()
def poor_inference(sample_ground_truth: list[dict]) -> list[dict]:
    """Inference results with large errors and poor agreement."""
    offsets = [15.0, -12.0, 20.0, -18.0, 14.0]
    return [
        _make_inference(gt, sqm_offset=offset, same_spacious=False, same_hosting=False)
        for gt, offset in zip(sample_ground_truth, offsets)
    ]
