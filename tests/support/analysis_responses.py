"""Synthetic provider messages and deliberately malformed response shapes."""

import copy
import json
from typing import Any

from home_finder.analysis.contracts import (
    MAX_OUTPUT_CHARACTERS,
)

PHASE1_VISUAL_RESPONSE: dict[str, Any] = {
    "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_01A09q90qw90lq917835lBSQ",
            "name": "property_visual_analysis",
            "input": {
                "kitchen": {
                    "overall_quality": "modern",
                    "hob_type": "gas",
                    "has_dishwasher": "yes",
                    "has_washing_machine": "yes",
                    "notes": "Modern integrated kitchen with gas hob and quartz worktops",
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
                    "natural_light": "excellent",
                    "window_sizes": "large",
                    "feels_spacious": True,
                    "ceiling_height": "high",
                    "floor_level": "upper",
                    "notes": "South-facing with floor-to-ceiling windows",
                },
                "space": {
                    "living_room_sqm": 24.0,
                    "is_spacious_enough": True,
                    "confidence": "high",
                    "hosting_layout": "good",
                },
                "bathroom": {
                    "overall_condition": "modern",
                    "has_bathtub": "yes",
                    "shower_type": "overhead",
                    "is_ensuite": "no",
                    "notes": "Clean and modern bathroom with heated towel rail",
                },
                "bedroom": {
                    "primary_is_double": "yes",
                    "has_built_in_wardrobe": "yes",
                    "can_fit_desk": "yes",
                    "office_separation": "dedicated_room",
                    "notes": "Good-sized double bedroom with built-in storage",
                },
                "outdoor_space": {
                    "availability": "yes",
                    "has_balcony": True,
                    "has_garden": False,
                    "has_terrace": False,
                    "has_shared_garden": False,
                    "notes": "Small balcony off living room",
                },
                "storage": {
                    "has_built_in_wardrobes": "yes",
                    "has_hallway_cupboard": "yes",
                    "storage_rating": "good",
                },
                "flooring_noise": {
                    "primary_flooring": "hardwood",
                    "has_double_glazing": "yes",
                    "building_construction": "solid_brick",
                    "noise_indicators": [],
                    "hosting_noise_risk": "low",
                    "notes": "Solid Victorian brick construction, quiet street",
                },
                "listing_red_flags": {
                    "missing_room_photos": [],
                    "too_few_photos": False,
                    "selective_angles": False,
                    "description_concerns": [],
                    "red_flag_count": 0,
                },
                "overall_rating": 4,
                "condition_concerns": False,
                "concern_severity": "none",
                "summary": (
                    "Well-maintained Victorian conversion with modern kitchen"
                    " and good natural light. Spacious living room suits WFH and hosting."
                ),
            },
        }
    ],
    "model": "claude-sonnet-4-6",
    "stop_reason": "tool_use",
    "usage": {
        "input_tokens": 1523,
        "output_tokens": 312,
        "cache_read_input_tokens": 1200,
        "cache_creation_input_tokens": 0,
    },
}


_PHASE2_EVAL_DATA: dict[str, Any] = {
    "listing_extraction": {
        "epc_rating": "C",
        "service_charge_pcm": None,
        "deposit_weeks": 5,
        "bills_included": "no",
        "pets_allowed": "unknown",
        "parking": "street",
        "council_tax_band": "C",
        "property_type": "victorian",
        "furnished_status": "furnished",
        "broadband_type": "fttc",
    },
    "viewing_notes": {
        "check_items": [
            "Check water pressure in shower",
            "Inspect sash windows for drafts",
            "Test broadband speed",
        ],
        "questions_for_agent": [
            "What is the service charge?",
            "Any planned building works?",
        ],
        "deal_breaker_tests": [
            "Run hot water for 2 minutes",
            "Check mobile signal in all rooms",
        ],
    },
    "highlights": [
        "Gas hob",
        "Modern kitchen",
        "Excellent natural light",
        "Private balcony",
        "Dedicated office room",
    ],
    "lowlights": ["No dishwasher"],
    "one_line": "Bright Victorian conversion with modern kitchen and dedicated office",
    "value_for_quality": {
        "rating": "good",
        "reasoning": "Well-maintained property at fair price for E8 Victorian stock",
    },
}


PHASE2_EVALUATION_RESPONSE: dict[str, Any] = {
    "id": "msg_01YGDUEZKgBBDzvnqtvWpZFL",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": json.dumps(_PHASE2_EVAL_DATA),
        }
    ],
    "model": "claude-sonnet-4-6",
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 892,
        "output_tokens": 187,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 0,
    },
}


def failed_message(phase, scenario):
    data = copy.deepcopy(
        PHASE1_VISUAL_RESPONSE if phase == "visual" else PHASE2_EVALUATION_RESPONSE
    )
    data["usage"]["cache_creation_input_tokens"] = 17
    if scenario in {"refusal", "max_tokens", "model_context_window_exceeded", "pause_turn"}:
        # The payload is otherwise complete: the stop reason still takes precedence.
        data["stop_reason"] = scenario
    elif scenario == "wrong_tool":
        data["content"][0]["name"] = "unrequested_tool"
    elif scenario == "two_tools":
        data["content"] *= 2
    elif scenario == "missing_tool":
        data["content"] = [{"type": "text", "text": "{}"}]
    elif scenario == "missing_visual_field":
        del data["content"][0]["input"]["kitchen"]["has_dishwasher"]
    elif scenario == "string_boolean":
        data["content"][0]["input"]["space"]["is_spacious_enough"] = "false"
    elif scenario == "invalid_rating":
        data["content"][0]["input"]["overall_rating"] = 6
    elif scenario in {"fractional_rating", "boolean_rating", "string_rating"}:
        data["content"][0]["input"]["overall_rating"] = {
            "fractional_rating": 4.5,
            "boolean_rating": True,
            "string_rating": "4",
        }[scenario]
    elif scenario == "null_notes":
        data["content"][0]["input"]["kitchen"]["notes"] = None
    elif scenario == "missing_notes":
        del data["content"][0]["input"]["kitchen"]["notes"]
    elif scenario == "oversized_visual":
        data["content"][0]["input"]["unused_extension"] = "x" * MAX_OUTPUT_CHARACTERS
    elif scenario == "missing_eval_field":
        payload = json.loads(data["content"][0]["text"])
        del payload["viewing_notes"]
        data["content"][0]["text"] = json.dumps(payload)
    elif scenario == "invalid_enum":
        payload = json.loads(data["content"][0]["text"])
        payload["lowlights"] = ["Invented category"]
        data["content"][0]["text"] = json.dumps(payload)
    elif scenario == "wrapped_one_line":
        payload = json.loads(data["content"][0]["text"])
        payload["one_line"] = {"one_line": "Legacy wrapped text"}
        data["content"][0]["text"] = json.dumps(payload)
    elif scenario == "duplicate_field":
        data["content"][0]["text"] = '{"one_line":"first",' + data["content"][0]["text"][1:]
    elif scenario == "two_text_blocks":
        data["content"] *= 2
    elif scenario == "malformed_json":
        data["content"][0]["text"] = '{"broken"'
    elif scenario == "oversized_evaluation":
        data["content"][0]["text"] = " " * (MAX_OUTPUT_CHARACTERS + 1)
    else:
        raise AssertionError(scenario)
    return data


def recorded_malformed_visual(case: str):
    """Minimal shapes from the two failed Sonnet receipts; omit advert prose."""
    raw = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    if case == "c218d61fc0d__r2":
        # The saved space string ends at its room_areas array, without the final }.
        raw["space"] = json.dumps(raw["space"])[:-1]
    elif case == "c6a9db682ca__r0":
        # Pseudo-XML starts the section; its remaining fields spill into the root.
        kitchen = raw.pop("kitchen")
        raw["kitchen"] = '\n<parameter name="overall_quality">modern'
        raw.update({key: value for key, value in kitchen.items() if key != "overall_quality"})
        raw["space"] = '\n<parameter name="living_room_sqm">null'
    else:
        raise AssertionError(case)
    return raw


SONNET55 = "claude-sonnet-5-5"


USAGE = {
    "input_tokens": 100,
    "output_tokens": 200,
    "cache_creation_input_tokens": 30,
    "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
    "cache_read_input_tokens": 300,
    "service_tier": "standard",
    "inference_geo": "global",
    "output_tokens_details": {"thinking_tokens": 70},
}


def response(phase="visual", model=SONNET55, *, thinking=True):
    output = copy.deepcopy(
        PHASE1_VISUAL_RESPONSE if phase == "visual" else PHASE2_EVALUATION_RESPONSE
    )
    output["model"] = model
    output["usage"] = copy.deepcopy(USAGE)
    if thinking:
        output["content"].insert(0, {"type": "thinking", "thinking": "", "signature": "fixture"})
    return output


def evaluation_visual(request):
    body = json.loads(request.content)
    if "tools" in body:
        return None
    prompt = body["messages"][0]["content"]
    if isinstance(prompt, list):
        prompt = "\n".join(block.get("text", "") for block in prompt)
    return json.loads(prompt.split("<visual_analysis>\n", 1)[1].split("\n</visual_analysis>", 1)[0])
