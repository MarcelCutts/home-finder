"""Keep external and unclassified floorplan areas out of internal arithmetic."""

import copy
import json

import httpx2
import pytest

from home_finder.analysis import analyzer as quality_module
from home_finder.analysis.analyzer import APIUnavailableError, _VisualAnalysisResponse
from home_finder.analysis.contracts import AnalysisOutputError, validate_output
from home_finder.analysis.space_evidence import reconcile_floorplan_areas
from home_finder.config import new_runtime_analysis_config
from home_finder.db import PropertyStorage
from tests.support.analysis_responses import evaluation_visual, response
from tests.support.visual_checkpoints import CONTENT, checkpoint_rows, quality, unavailable


def scoped_floorplan_response():
    """Synthetic dimensions reproduce the arithmetic, not a historical receipt."""
    body = response()
    visual = body["content"][-1]["input"]
    dimensions = [(4.36, 4.07), (3.33, 3.09), (2.52, 1.98), (2.41, 2.39), (2.14, 1.98)]
    rooms = [
        {
            "name": f"Interior {index}",
            "length_m": length,
            "width_m": width,
            "area_sqm": round(length * width, 2),
            "area_basis": "rectangular_dimensions",
            "area_scope": "internal",
        }
        for index, (length, width) in enumerate(dimensions, 1)
    ]
    rooms.append(
        {
            "name": "Balcony",
            "length_m": 2.9,
            "width_m": 2.3,
            "area_sqm": 6.67,
            "area_basis": "rectangular_dimensions",
            "area_scope": "external",
        }
    )
    visual["space"].update(
        living_room_sqm=17.75,
        total_area_sqm=47.1,
        floorplan_total_area_sqm=47.1,
        area_estimation_method="stated_on_floorplan",
        area_notes="Printed internal total; external space listed separately.",
        room_areas=rooms,
    )
    visual["summary"] = "Bright flat with separate outdoor space."
    visual["light_space"]["notes"] = "Large windows light the living space."
    visual["bedroom"]["notes"] = "The bedroom is separate from the living space."
    return body


@pytest.mark.parametrize("name", ["Balcony", "Terrace", "Garden", "Living room", "Unlabelled"])
@pytest.mark.parametrize("scope", ["external", "unknown"])
def test_only_explicit_internal_scope_contributes_to_internal_total(name, scope):
    visual = scoped_floorplan_response()["content"][-1]["input"]
    visual["space"]["room_areas"][-1].update(name=name, area_scope=scope)
    before = copy.deepcopy(visual)
    assert sum(room["area_sqm"] for room in visual["space"]["room_areas"]) == pytest.approx(49.7)
    assert reconcile_floorplan_areas(visual) == before
    assert visual == before


def test_missing_legacy_scope_remains_unknown_without_reclassifying_names():
    visual = scoped_floorplan_response()["content"][-1]["input"]
    for room in visual["space"]["room_areas"]:
        room.pop("area_scope")
    before = copy.deepcopy(visual)
    reconciled = reconcile_floorplan_areas(visual)
    assert visual == before
    assert reconciled["space"]["living_room_sqm"] == 17.75
    assert reconciled["space"]["total_area_sqm"] == 47.1
    assert reconciled["summary"] == before["summary"]
    assert all(room["area_scope"] == "unknown" for room in reconciled["space"]["room_areas"])


def test_real_internal_oversum_withholds_internal_claims_but_preserves_external_evidence():
    visual = scoped_floorplan_response()["content"][-1]["input"]
    rooms = visual["space"]["room_areas"]
    external = copy.deepcopy(rooms[-1])
    rooms[-1]["area_scope"] = "internal"  # Scope, not the name, controls arithmetic.
    rooms.append(external)
    before = copy.deepcopy(visual)
    reconciled = reconcile_floorplan_areas(visual)
    assert visual == before
    assert reconciled["space"]["living_room_sqm"] is None
    assert reconciled["space"]["total_area_sqm"] == 47.1
    assert all(room["area_sqm"] is None for room in reconciled["space"]["room_areas"][:-1])
    assert reconciled["space"]["room_areas"][-1] == external
    assert "conflict" in reconciled["space"]["area_notes"]


@pytest.mark.parametrize("scope", ["external", "unknown"])
def test_non_internal_entries_do_not_inflate_internal_rounding_tolerance(scope):
    visual = scoped_floorplan_response()["content"][-1]["input"]
    rooms = visual["space"]["room_areas"]
    rooms[-1].update(length_m=5.0, width_m=1.0, area_sqm=5.0, area_scope="internal")
    rooms.extend(
        {"name": "Other", "area_sqm": 1, "area_basis": "stated_on_floorplan", "area_scope": scope}
        for _ in range(20)
    )
    assert reconcile_floorplan_areas(visual)["space"]["living_room_sqm"] is None


def test_unsupported_external_geometry_does_not_withhold_internal_claims():
    visual = scoped_floorplan_response()["content"][-1]["input"]
    visual["space"]["room_areas"][-1].update(name="Living terrace", area_basis="unknown")
    reconciled = reconcile_floorplan_areas(visual)
    assert reconciled["space"]["room_areas"][-1]["area_sqm"] is None
    assert reconciled["space"]["living_room_sqm"] == 17.75
    assert reconciled["summary"] == visual["summary"]
    assert reconciled["bedroom"] == visual["bedroom"]
    assert reconciled["light_space"] == visual["light_space"]


@pytest.mark.parametrize("scope", ["internal", "unknown"])
@pytest.mark.parametrize("missing_room", ["Bathroom", "Living room"])
def test_unmeasured_room_only_withholds_existing_living_claim_when_it_is_the_living_room(
    scope, missing_room
):
    visual = scoped_floorplan_response()["content"][-1]["input"]
    visual["space"].update(
        living_room_sqm=20,
        total_area_sqm=60,
        floorplan_total_area_sqm=60,
        room_areas=[
            {
                "name": "Living room" if missing_room == "Bathroom" else "Bedroom",
                "length_m": 4,
                "width_m": 5,
                "area_sqm": 20,
                "area_basis": "rectangular_dimensions",
                "area_scope": "internal",
            },
            {
                "name": missing_room,
                "length_m": None,
                "width_m": None,
                "area_sqm": None,
                "area_basis": "unknown",
                "area_scope": scope,
            },
        ],
    )
    before = copy.deepcopy(visual)
    reconciled = reconcile_floorplan_areas(visual)
    assert visual == before
    if missing_room == "Bathroom":
        assert reconciled == before
    else:
        assert reconciled["space"]["living_room_sqm"] is None
        assert "geometry" in reconciled["space"]["area_notes"]


def test_unsupported_internal_area_is_not_used_to_claim_an_arithmetic_conflict():
    visual = scoped_floorplan_response()["content"][-1]["input"]
    visual["space"]["room_areas"][0]["area_sqm"] = 100
    reconciled = reconcile_floorplan_areas(visual)
    assert reconciled["space"]["living_room_sqm"] is None
    assert "geometry" in reconciled["space"]["area_notes"]
    assert "conflict" not in reconciled["space"]["area_notes"]
    assert reconciled["space"]["room_areas"][1]["area_sqm"] == 10.29


@pytest.mark.parametrize("scope", [None, "inside", 1, False, "missing"])
def test_live_room_scope_is_explicit_and_typed(scope):
    visual = scoped_floorplan_response()["content"][-1]["input"]
    room = visual["space"]["room_areas"][0]
    if scope == "missing":
        room.pop("area_scope")
    else:
        room["area_scope"] = scope
    with pytest.raises(AnalysisOutputError):
        validate_output(visual, _VisualAnalysisResponse, "visual", None)


async def test_scoped_areas_survive_native_evaluation_restart_without_mutating_checkpoint(
    tmp_path, cached_analysis_property
):
    merged, images = cached_analysis_property
    database = str(tmp_path / "scoped-area-restart.db")
    body = scoped_floorplan_response()
    raw = body["content"][-1]["input"]
    calls = {"visual": 0, "evaluation": 0}
    observed = []

    def respond(request):
        outgoing = evaluation_visual(request)
        if outgoing is None:
            calls["visual"] += 1
            return httpx2.Response(200, json=body)
        calls["evaluation"] += 1
        observed.append(outgoing)
        if calls["evaluation"] == 1:
            return unavailable()
        return httpx2.Response(200, json=response("evaluation"))

    async with PropertyStorage(database) as storage:
        await storage.processing.save_pre_analysis_properties([merged], {})
        async with quality(storage, respond, settings=new_runtime_analysis_config()) as model:
            with pytest.raises(APIUnavailableError):
                await model.analyze_single_merged(merged, data_dir=images)
        checkpoint = (await checkpoint_rows(storage))[0]
        assert json.loads(checkpoint["data_json"]) == raw

    async with PropertyStorage(database) as storage:
        pending = (await storage.processing.get_pending_analysis_properties())[0]
        async with quality(storage, respond, settings=new_runtime_analysis_config()) as model:
            _, result = await model.analyze_single_merged(pending, data_dir=images)
        assert result.analysis_failure is None
        assert result.space.living_room_sqm == 17.75
        assert result.space.total_area_sqm == result.space.floorplan_total_area_sqm == 47.1
        assert result.space.room_areas[-1].area_scope == "external"
        assert result.space.room_areas[-1].area_sqm == 6.67
        assert (await checkpoint_rows(storage))[0]["data_json"] == checkpoint["data_json"]

    assert calls == {"visual": 1, "evaluation": 2}
    assert observed[0] == observed[1]
    for outgoing in observed:
        assert outgoing["space"] == raw["space"]
        for section in ("summary", "light_space", "bedroom"):
            assert outgoing[section] == raw[section]


async def test_scope_schema_change_invalidates_old_checkpoint_without_rewriting_it(
    tmp_path, monkeypatch
):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx2.Response(200, json=response())

    old_tool = copy.deepcopy(quality_module.VISUAL_ANALYSIS_TOOL)
    room = old_tool["input_schema"]["properties"]["space"]["properties"]["room_areas"]["items"]
    room["properties"].pop("area_scope")
    room["required"].remove("area_scope")
    async with PropertyStorage(str(tmp_path / "scope-contract.db")) as storage:
        with monkeypatch.context() as legacy:
            legacy.setattr(quality_module, "VISUAL_ANALYSIS_TOOL", old_tool)
            async with quality(storage, respond, settings=new_runtime_analysis_config()) as model:
                await model._run_visual_analysis(CONTENT, "fixture:scope-contract")
                old_hash = model._analysis_provenance().visual_output_sha256
            old_checkpoint = (await checkpoint_rows(storage))[0]
        async with quality(storage, respond, settings=new_runtime_analysis_config()) as model:
            await model._run_visual_analysis(CONTENT, "fixture:scope-contract")
            await model._run_visual_analysis(CONTENT, "fixture:scope-contract")
            assert model._analysis_provenance().visual_output_sha256 != old_hash
        current = (await checkpoint_rows(storage))[0]
        assert current["input_sha256"] != old_checkpoint["input_sha256"]
        assert current["data_json"] == old_checkpoint["data_json"]
    assert len(calls) == 2
