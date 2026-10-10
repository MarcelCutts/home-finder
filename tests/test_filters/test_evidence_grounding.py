"""Evidence contradictions through native two-phase requests and durable reuse."""

import copy
import json

import httpx2
import pytest

from home_finder.db import PropertyStorage
from home_finder.filters import quality as quality_module
from home_finder.filters.listing_text import supported_bills_included
from home_finder.filters.quality import APIUnavailableError, assess_value
from home_finder.models import PropertyQualityAnalysis
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)
from tests.test_filters.test_visual_checkpoints import (
    CONTENT,
    checkpoint_rows,
    quality,
    unavailable,
)


def floorplan_response(*, reception_basis="rectangular_dimensions", reception_area=43.8):
    body = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    visual = body["content"][0]["input"]
    visual["space"].update(
        living_room_sqm=reception_area,
        total_area_sqm=52.6,
        floorplan_total_area_sqm=52.6,
        area_estimation_method="measured_from_floorplan",
        room_areas=[
            {
                "name": "Reception / Kitchen",
                "length_m": 6.71,
                "width_m": 6.53,
                "area_sqm": reception_area,
                "area_basis": reception_basis,
                "area_scope": "internal",
            },
            {
                "name": "Bedroom",
                "length_m": 4.95,
                "width_m": 3.30,
                "area_sqm": 16.3,
                "area_basis": "rectangular_dimensions",
                "area_scope": "internal",
            },
        ],
    )
    visual["summary"] = "Vast reception of nearly 44 sqm."
    visual["light_space"]["notes"] = "Reception 43.8 sqm, superb for hosting."
    visual["bedroom"]["notes"] = "The 43.8 sqm reception has a desk corner."
    return body


def evaluation_visual(request):
    body = json.loads(request.content)
    if "tools" in body:
        return None
    prompt = body["messages"][0]["content"]
    if isinstance(prompt, list):
        prompt = "\n".join(block.get("text", "") for block in prompt)
    return json.loads(prompt.split("<visual_analysis>\n", 1)[1].split("\n</visual_analysis>", 1)[0])


async def test_contradictory_areas_are_withheld_before_evaluation_and_after_restart(
    tmp_path, cached_analysis_property
):
    merged, images = cached_analysis_property
    database = str(tmp_path / "area-restart.db")
    calls = {"visual": 0, "evaluation": 0}
    observed = []

    def respond(request):
        visual = evaluation_visual(request)
        if visual is None:
            calls["visual"] += 1
            return httpx2.Response(200, json=floorplan_response())
        calls["evaluation"] += 1
        observed.append(visual)
        if calls["evaluation"] == 1:
            return unavailable()
        return httpx2.Response(200, json=PHASE2_EVALUATION_RESPONSE)

    async with PropertyStorage(database) as storage:
        await storage.pipeline.save_pre_analysis_properties([merged], {})
        async with quality(storage, respond) as model:
            with pytest.raises(APIUnavailableError):
                await model.analyze_single_merged(merged, data_dir=images)
        checkpoint = (await checkpoint_rows(storage))[0]
        # Paid visual evidence is retained as received, not discarded or mutated.
        assert json.loads(checkpoint["data_json"])["space"]["living_room_sqm"] == 43.8

    async with PropertyStorage(database) as storage:
        recovered = (await storage.pipeline.get_pending_analysis_properties())[0]
        async with quality(storage, respond) as model:
            _, result = await model.analyze_single_merged(recovered, data_dir=images)
        assert result is not None and result.analysis_failure is None
        assert result.analysis_provenance.visual_input_sha256 == checkpoint["input_sha256"]
        assert result.space.living_room_sqm is None
        assert result.space.total_area_sqm == result.space.floorplan_total_area_sqm == 52.6
        assert result.space.area_estimation_method == "stated_on_floorplan"
        assert result.space.is_spacious_enough is None
        assert result.space.room_areas[0].length_m == 6.71
        assert result.space.room_areas[1].width_m == 3.30
        assert result.kitchen.hob_type == "gas"  # Independent observations survive.
        assert result.listing_extraction.bills_included == "unknown"  # Silent listing.

    assert calls == {"visual": 1, "evaluation": 2}
    assert observed[0] == observed[1]
    for visual in observed:
        assert all(room["area_sqm"] is None for room in visual["space"]["room_areas"])
        assert "43.8" not in json.dumps(visual) and "nearly 44" not in json.dumps(visual)
        assert "conflict" in visual["space"]["area_notes"]


@pytest.mark.parametrize("basis", ["rectangular_dimensions", "unknown", "stated_on_floorplan"])
async def test_supported_room_area_survives_but_bounding_dimensions_do_not(
    tmp_path, cached_analysis_property, basis
):
    merged, images = cached_analysis_property
    body = floorplan_response(reception_basis=basis, reception_area=24.0)
    visual = body["content"][0]["input"]
    visual["space"]["room_areas"][0].update(length_m=6.0, width_m=4.0)
    visual["summary"] = "Bright flat with a combined kitchen/reception."
    visual["light_space"]["notes"] = "Large windows."
    visual["bedroom"]["notes"] = "A separate double bedroom."
    seen = []

    def respond(request):
        outgoing = evaluation_visual(request)
        if outgoing is None:
            return httpx2.Response(200, json=body)
        seen.append(outgoing)
        return httpx2.Response(200, json=PHASE2_EVALUATION_RESPONSE)

    async with (
        PropertyStorage(str(tmp_path / "geometry.db")) as storage,
        quality(storage, respond) as model,
    ):
        _, result = await model.analyze_single_merged(merged, data_dir=images)
    expected = None if basis == "unknown" else 24.0
    assert result.space.living_room_sqm == seen[0]["space"]["living_room_sqm"] == expected
    assert result.space.room_areas[0].area_sqm == expected
    assert result.space.total_area_sqm == 52.6


@pytest.mark.parametrize("with_rooms", [False, True])
async def test_conflict_without_a_printed_total_withholds_computed_total(
    tmp_path, cached_analysis_property, with_rooms
):
    merged, images = cached_analysis_property
    body = floorplan_response()
    space = body["content"][0]["input"]["space"]
    space.pop("floorplan_total_area_sqm")
    if not with_rooms:
        space["room_areas"] = []
        space["living_room_sqm"] = 60.0

    def respond(request):
        visual = evaluation_visual(request)
        if visual is None:
            return httpx2.Response(200, json=body)
        assert visual["space"]["living_room_sqm"] is None
        assert visual["space"]["total_area_sqm"] is None
        assert visual["space"]["area_estimation_method"] is None
        return httpx2.Response(200, json=PHASE2_EVALUATION_RESPONSE)

    async with (
        PropertyStorage(str(tmp_path / "unprinted.db")) as storage,
        quality(storage, respond) as model,
    ):
        _, result = await model.analyze_single_merged(merged, data_dir=images)
    assert result.space.total_area_sqm is None


async def test_area_contract_change_invalidates_old_visual_checkpoint(tmp_path, monkeypatch):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    old_tool = copy.deepcopy(quality_module.VISUAL_ANALYSIS_TOOL)
    old_space = old_tool["input_schema"]["properties"]["space"]["properties"]
    for field in ("floorplan_total_area_sqm", "area_notes"):
        old_space.pop(field)
    old_space["room_areas"]["items"]["properties"].pop("area_basis")
    async with PropertyStorage(str(tmp_path / "contract.db")) as storage:
        with monkeypatch.context() as legacy:
            legacy.setattr(quality_module, "VISUAL_ANALYSIS_TOOL", old_tool)
            async with quality(storage, respond) as model:
                await model._run_visual_analysis(CONTENT, "fixture:area-contract")
                old_hash = model._analysis_provenance().visual_output_sha256
            old_key = (await checkpoint_rows(storage))[0]["input_sha256"]
        async with quality(storage, respond) as model:
            await model._run_visual_analysis(CONTENT, "fixture:area-contract")
            await model._run_visual_analysis(CONTENT, "fixture:area-contract")
            assert model._analysis_provenance().visual_output_sha256 != old_hash
        assert (await checkpoint_rows(storage))[0]["input_sha256"] != old_key
    assert len(calls) == 2


def test_old_analysis_remains_readable_without_retroactive_area_changes():
    old = floorplan_response()["content"][0]["input"]
    old["space"].pop("floorplan_total_area_sqm")
    for room in old["space"]["room_areas"]:
        room.pop("area_basis")
        room.pop("area_scope")
    result = PropertyQualityAnalysis.model_validate(old)
    assert result.space.living_room_sqm == 43.8
    assert result.space.room_areas[0].area_sqm == 43.8
    assert result.space.floorplan_total_area_sqm is None
    assert all(room.area_scope == "unknown" for room in result.space.room_areas)


@pytest.mark.parametrize(
    ("claimed", "description", "features", "expected"),
    [
        ("no", "Bright flat", None, "unknown"),
        ("yes", None, None, "unknown"),
        ("yes", "All bills included.", None, "yes"),
        ("yes", None, ["[Rightmove] Bills included"], "yes"),
        ("no", "Bills are not included.", None, "no"),
        ("no", None, ["Bills excluded"], "no"),
        ("no", None, ["[OpenRent] - Bills not included"], "no"),
        ("yes", "Bills included.", ["Bills excluded"], "unknown"),
        ("no", "Bills included.", None, "unknown"),
        ("yes", "Some bills included.", None, "unknown"),
        ("yes", "Bills included except electricity.", None, "unknown"),
        ("yes", "Bills included. Electricity extra.", None, "unknown"),
        ("no", "Assume bills are not included.", None, "unknown"),
        ("yes", "Bills included? Ask the agent.", None, "unknown"),
        ("unknown", "All bills included.", None, "unknown"),
    ],
)
def test_bills_flag_requires_explicit_unambiguous_text(claimed, description, features, expected):
    assert supported_bills_included(claimed, description, features) == expected


@pytest.mark.parametrize("marker", ["-", "*", "\u2022", "\u2013", "\u2014"])
@pytest.mark.parametrize(
    ("claimed", "declaration", "expected"),
    [
        ("no", "Bills not included", "no"),
        ("yes", "All bills included", "yes"),
        ("unknown", "All bills included", "unknown"),
        ("yes", "Some bills included", "unknown"),
        ("yes", "Bills included if agreed", "unknown"),
        ("yes", "Bills included except electricity", "unknown"),
        ("no", "Assume bills not included", "unknown"),
        ("yes", "Bills included? Ask the agent", "unknown"),
    ],
)
def test_html_bills_bullets_only_remove_leading_markers(marker, claimed, declaration, expected):
    description = (
        f"<strong>Summary:</strong><br />{marker} Furnished<br/>"
        f"{marker} {declaration}<br>{marker} Pets considered"
    )
    assert supported_bills_included(claimed, description, None) == expected


@pytest.mark.parametrize("claimed", ["yes", "no"])
def test_conflicting_html_bills_bullets_remain_unknown(claimed):
    description = "<ul><li>- Bills included</li><li>- Bills not included</li></ul>"
    assert supported_bills_included(claimed, description, None) == "unknown"


@pytest.mark.parametrize("price", [1800, 1900, 2200])
def test_value_note_always_qualifies_historical_reference(price):
    value = assess_value(price, "E8 2NN", 1)
    assert "unverified historical E8 reference" in value.note
    assert "average" not in value.note
