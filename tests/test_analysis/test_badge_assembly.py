"""The analyzer reconciles each new assembly's badges after correcting listing facts.

Each case runs both mocked phases against a fresh database; the rules themselves
are covered directly in test_badge_consistency.py.
"""

import copy
import json

import httpx2
import pytest
import structlog.testing

from home_finder.db import PropertyStorage
from tests.support.analysis_config import configuration
from tests.support.analysis_responses import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
    SONNET55,
)
from tests.support.visual_checkpoints import checkpoint_rows, quality


@pytest.fixture
def assemble(tmp_path, cached_analysis_property):
    async def run(*, description, visual=None, listing=None, highlights=()):
        merged, images = cached_analysis_property
        merged = merged.model_copy(
            update={
                "canonical": merged.canonical.model_copy(update={"description": description}),
                "descriptions": {},
                "source_text": {},
            }
        )
        visual_body = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
        visual_data = visual_body["content"][0]["input"]
        for section, fields in (visual or {}).items():
            visual_data[section].update(fields)
        eval_body = copy.deepcopy(PHASE2_EVALUATION_RESPONSE)
        eval_data = json.loads(eval_body["content"][0]["text"])
        eval_data.update(highlights=[*highlights], lowlights=[])
        eval_data["listing_extraction"].update(listing or {})
        eval_body["content"][0]["text"] = json.dumps(eval_data)
        visual_body["model"] = eval_body["model"] = SONNET55

        def respond(request):
            is_visual = "tools" in json.loads(request.content)
            return httpx2.Response(200, json=visual_body if is_visual else eval_body)

        with structlog.testing.capture_logs() as logs:
            async with (
                PropertyStorage(str(tmp_path / "badges.db")) as storage,
                quality(storage, respond, settings=configuration()) as analyzer,
            ):
                _, result = await analyzer.analyze_single_merged(merged, data_dir=images)
                checkpoints = await checkpoint_rows(storage)
        assert result is not None and result.analysis_failure is None
        # Badge checks neither rewrite nor invalidate the paid visual observations.
        assert json.loads(checkpoints[0]["data_json"]) == visual_data
        assert result.analysis_provenance.visual_input_sha256 == checkpoints[0]["input_sha256"]
        dropped = [log for log in logs if log["event"] == "analysis_badge_dropped"]
        return merged.unique_id, result, dropped

    return run


@pytest.mark.parametrize(
    ("description", "corrected"),
    [
        pytest.param(
            "Private listing text. Viewing link: https://private.example/room.",
            "unknown",
            id="listing-silent-on-bills",
        ),
        pytest.param(
            "<ul><li>- All bills included</li><li>- Furnished</li></ul>",
            "yes",
            id="html-list-includes-bills",
        ),
    ],
)
async def test_bills_badge_follows_the_listing_text_correction_not_the_raw_evaluation(
    assemble, description, corrected
):
    property_id, result, dropped = await assemble(
        description=description,
        listing={"bills_included": "yes"},
        highlights=["Modern kitchen", "Bills included", "Good natural light"],
    )
    assert result.listing_extraction.bills_included == corrected
    kept = corrected == "yes"
    assert result.highlights == [
        "Modern kitchen",
        *(["Bills included"] if kept else []),
        "Good natural light",
    ]
    assert dropped == (
        []
        if kept
        else [
            {
                "event": "analysis_badge_dropped",
                "log_level": "info",
                "property_id": property_id,
                "badge": "Bills included",
                "field": "listing_extraction.bills_included",
                "reason": "missing_structured_support",
            }
        ]
    )  # Correlation ID only; no description, URL, model response or other private payload.


async def test_saved_no_pets_receipt_cannot_advertise_pets_or_overstate_condition(assemble):
    # Minimal facts from bake-off case cbfa2bd6c46; no private advert/photos needed.
    # The pets fact comes from the evaluation, the finish and condition from the
    # visual phase, so the check must see the merged assembly.
    _, result, dropped = await assemble(
        description="<p>Summary &amp; Exclusions:</p><br>- No Pets, sorry",
        listing={"pets_allowed": "no"},
        visual={
            "condition": {"overall_condition": "good"},
            "bathroom": {"overall_condition": "decent"},
        },
        highlights=["Good natural light", "Modern bathroom", "Excellent condition", "Pets allowed"],
    )
    assert result.listing_extraction.pets_allowed == "no"
    assert result.highlights == ["Good natural light"]
    assert {log["badge"] for log in dropped} == {
        "Pets allowed",
        "Modern bathroom",
        "Excellent condition",
    }
