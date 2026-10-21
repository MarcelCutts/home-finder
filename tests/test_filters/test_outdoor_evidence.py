"""Outdoor absence requires evidence; unobserved type flags retain uncertainty."""

import copy
import json

import httpx2
import pytest

from home_finder.db import PropertyStorage
from home_finder.filters.analysis_contracts import AnalysisOutputError, validate_output
from home_finder.filters.fit_score import _score_hosting, compute_lifestyle_icons
from home_finder.filters.quality import VISUAL_ANALYSIS_TOOL, _VisualAnalysisResponse
from home_finder.models import OutdoorSpaceAnalysis
from home_finder.models.quality import OUTDOOR_SPACE_FLAGS, outdoor_availability
from home_finder.notifiers.telegram import _format_outdoor_info, _format_quality_block
from tests.test_filters.test_quality_respx import PHASE1_VISUAL_RESPONSE
from tests.test_filters.test_sonnet55 import configuration, response
from tests.test_filters.test_visual_checkpoints import quality

NO_TYPES = dict.fromkeys(OUTDOOR_SPACE_FLAGS, False)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, "unknown"),
        (NO_TYPES, "unknown"),
        ({**NO_TYPES, "notes": "Private patio/lightwell; verify access."}, "unknown"),
        ({**NO_TYPES, "availability": "unknown"}, "unknown"),
        ({**NO_TYPES, "availability": "no"}, "no"),
        ({**NO_TYPES, "availability": "yes"}, "yes"),
        ({**NO_TYPES, "availability": "no", "has_terrace": True}, "yes"),
        *[({**NO_TYPES, name: True}, "yes") for name in OUTDOOR_SPACE_FLAGS],
    ],
)
def test_old_and_new_evidence_has_one_interpretation(data, expected, make_quality_analysis):
    original = copy.deepcopy(data)
    outdoor = OutdoorSpaceAnalysis.model_validate(data)
    assert data == original
    assert outdoor.availability == data.get("availability", "unknown")
    assert outdoor.effective_availability == outdoor_availability(data) == expected
    assert OutdoorSpaceAnalysis.model_validate_json(outdoor.model_dump_json()) == outdoor

    hosting = _score_hosting({"outdoor_space": data}, 1)
    factors = [item for item in hosting.factors if "outdoor" in item["label"].lower()]
    if expected == "unknown":
        assert factors == []
        assert hosting.confidence == 0
    else:
        assert factors == [
            {
                "label": "Outdoor space" if expected == "yes" else "No outdoor space",
                "state": "earned" if expected == "yes" else "missed",
            }
        ]
        assert hosting.confidence > 0
    assert compute_lifestyle_icons({"outdoor_space": data}, 1) is None
    analysis = make_quality_analysis(
        outdoor_space=outdoor, lowlights=["No outdoor space", "Potential traffic noise"]
    )
    icons = compute_lifestyle_icons(analysis, 1)
    assert icons is not None
    assert ("Has outdoor space" in icons["space"]["tooltip"]) == (expected == "yes")
    assert analysis.display_lowlights == [
        *(["No outdoor space"] if expected == "no" else []),
        "Potential road or rail noise",
    ]
    assert analysis.lowlights == ["No outdoor space", "Potential traffic noise"]
    block = "\n".join(_format_quality_block(analysis))
    assert ("No outdoor space" in block) == (expected == "no")
    message = _format_outdoor_info(analysis)
    if expected == "unknown":
        assert message == "Unknown"
    elif expected == "no":
        assert message == "No outdoor space"
    else:
        assert message and message not in ("Unknown", "No outdoor space")


def test_new_visual_contract_requires_overall_evidence_and_includes_patios():
    schema = VISUAL_ANALYSIS_TOOL["input_schema"]["properties"]["outdoor_space"]
    assert "availability" in schema["required"]
    assert schema["properties"]["availability"]["enum"] == ["yes", "no", "unknown"]
    assert "patios and courtyards" in schema["properties"]["has_terrace"]["description"]
    raw = copy.deepcopy(PHASE1_VISUAL_RESPONSE["content"][0]["input"])
    raw["outdoor_space"].pop("availability")
    with pytest.raises(AnalysisOutputError, match="visual:malformed_output"):
        validate_output(raw, _VisualAnalysisResponse, "visual", None)
    # Historical parsing remains deliberately separate from the live contract.
    assert OutdoorSpaceAnalysis.model_validate(raw["outdoor_space"]).availability == "unknown"


@pytest.mark.parametrize("availability", ["yes", "no", "unknown"])
async def test_new_outdoor_state_survives_native_phases_assembly_and_save(
    cached_analysis_property, availability
):
    merged, images = cached_analysis_property
    outdoor = {
        **NO_TYPES,
        "availability": availability,
        "has_terrace": availability == "yes",
        "notes": "Private patio/courtyard per listing." if availability == "yes" else "Fixture",
    }
    calls = []

    def respond(request):
        body = json.loads(request.content)
        phase = "visual" if "tools" in body else "evaluation"
        calls.append(phase)
        reply = response(phase)
        if phase == "visual":
            tool = next(block for block in reply["content"] if block["type"] == "tool_use")
            tool["input"]["outdoor_space"] = outdoor
        else:
            assert f'"availability": "{availability}"' in body["messages"][0]["content"]
        return httpx2.Response(200, json=reply)

    async with PropertyStorage(":memory:") as storage:
        await storage.save_merged_property(merged)
        async with quality(storage, respond, settings=configuration()) as analyst:
            _, result = await analyst.analyze_single_merged(merged, data_dir=images)
        assert result.analysis_failure is None
        assert result.outdoor_space.availability == availability
        assert result.outdoor_space.effective_availability == availability
        await storage.save_quality_analysis(merged.unique_id, result)
        saved = await storage.get_quality_analysis(merged.unique_id)
        assert saved.outdoor_space == result.outdoor_space
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT has_outdoor_space FROM quality_analyses")
            ).fetchone()
        assert row[0] == {"yes": 1, "no": 0, "unknown": None}[availability]
        assert calls == ["visual", "evaluation"]
