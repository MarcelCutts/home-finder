"""Coverage and uncertainty through native requests, caches and queue restarts."""

import copy
import json

import httpx2
import pytest

from home_finder.db import PropertyStorage
from home_finder.filters.quality import APIUnavailableError, PropertyQualityFilter
from home_finder.models import PropertyImage, PropertyQualityAnalysis
from home_finder.models.analysis import AnalysisConfig
from home_finder.utils.image_cache import find_cached_file
from tests.test_filters.test_quality import _populate_image_cache
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)

pytestmark = pytest.mark.usefixtures("native_analysis_spending")


def gallery_property(make_merged_property, tmp_path, count=8, origin="source_declared"):
    merged = make_merged_property()
    merged = merged.model_copy(
        update={
            "images": tuple(
                PropertyImage(
                    url=f"https://example.com/gallery-{index}.jpg",
                    source=merged.canonical.source,
                    image_type="gallery",
                )
                for index in range(count)
            ),
            "floorplan": PropertyImage(
                url="https://example.com/plan.jpg",
                source=merged.canonical.source,
                image_type="floorplan",
                floorplan_origin=origin,
            )
            if origin
            else None,
        }
    )
    _populate_image_cache(str(tmp_path), merged)
    return merged


def responses_with_photo_accusations():
    visual = copy.deepcopy(PHASE1_VISUAL_RESPONSE)
    data = visual["content"][0]["input"]
    data["listing_red_flags"] = {
        "missing_room_photos": ["bathroom"],
        "too_few_photos": True,
        "selective_angles": True,
        "description_concerns": ["Clarify the advertised tenancy restriction"],
        "red_flag_count": 4,
        "notes": "The advert omits bathroom photos and hides problems",
    }
    data["summary"] = "The advert omits bathroom photos and hides problems"
    data["bathroom"].update(
        overall_condition="unknown",
        has_bathtub="unknown",
        shower_type="unknown",
        is_ensuite="unknown",
        notes="The advert omits bathroom photos",
    )
    data["condition"]["maintenance_concerns"] = ["Visible damaged wall"]
    # Application context cannot be supplied or invalidated by either model phase.
    data["image_review_coverage"] = {"reviewed_gallery_count": 900}
    evaluation = copy.deepcopy(PHASE2_EVALUATION_RESPONSE)
    evaluated = json.loads(evaluation["content"][0]["text"])
    evaluated["lowlights"] = [
        "No bathroom photos",
        "No interior photos",
        "Missing key photos",
        "Needs updating",
    ]
    evaluated["image_review_coverage"] = {"available_gallery_count": "injected"}
    evaluation["content"][0]["text"] = json.dumps(evaluated)
    return visual, evaluation


def configuration():
    return AnalysisConfig.model_validate(
        {"visual": {"sdk_retries": 0}, "evaluation": {"sdk_retries": 0}}
    )


def model_for(respond, **kwargs):
    return PropertyQualityFilter(
        "fixture",
        max_images=8,
        analysis_config=configuration(),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        **kwargs,
    )


def visual_prompt(request):
    return request["messages"][0]["content"][-1]["text"]


def evaluated_visual(request):
    prompt = request["messages"][0]["content"]
    return json.loads(prompt.split("<visual_analysis>\n", 1)[1].split("</visual_analysis>", 1)[0])


@pytest.mark.parametrize(
    ("count", "origin", "cache_failure", "reviewed", "available", "blocks"),
    [
        (8, "source_declared", None, 7, 8, 8),
        (12, "source_declared", None, 7, 12, 8),
        (8, None, None, 8, 8, 8),
        (2, None, None, 2, 2, 2),
        (0, "source_declared", None, 0, 0, 1),
        (8, "source_declared", "corrupt_gallery", 6, 8, 7),
        (8, "source_declared", "missing_floorplan", 8, 8, 8),
        (8, "source_declared", "corrupt_floorplan", 8, 8, 8),
        (8, "unknown", None, 8, 9, 8),
    ],
)
async def test_actual_native_image_coverage_qualifies_photo_accusations(
    make_merged_property, tmp_path, count, origin, cache_failure, reviewed, available, blocks
):
    merged = gallery_property(make_merged_property, tmp_path, count, origin)
    if cache_failure:
        target = merged.images[0] if cache_failure == "corrupt_gallery" else merged.floorplan
        cached = find_cached_file(
            str(tmp_path), merged.unique_id, str(target.url), target.image_type
        )
        assert cached is not None
        if cache_failure == "missing_floorplan":
            cached.unlink()
        else:
            cached.write_bytes(b"corrupt synthetic image")

    visual, evaluation = responses_with_photo_accusations()
    original = copy.deepcopy(visual)
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx2.Response(200, json=visual if "tools" in payload else evaluation)

    async with model_for(respond) as model:
        _, analysis = await model.analyze_single_merged(merged, data_dir=str(tmp_path))
    assert analysis is not None and analysis.analysis_failure is None
    assert len(requests) == 2
    content = requests[0]["messages"][0]["content"]
    assert sum(block["type"] == "image" for block in content) == blocks
    coverage = analysis.image_review_coverage
    assert coverage is not None
    assert (coverage.reviewed_gallery_count, coverage.available_gallery_count) == (
        reviewed,
        available,
    )
    for prompt in (visual_prompt(requests[0]), requests[1]["messages"][0]["content"]):
        assert f"{reviewed} of {available} available gallery images reviewed" in prompt
        assert "Source-declared floorplans are excluded" in prompt
        assert "Even a complete review cannot establish advert completeness" in prompt
        assert "not seen in supplied images" in prompt
        if reviewed < available:
            assert "photo completeness is unknown" in prompt
            assert "all ratings or value penalties" in prompt

    flags = analysis.listing_red_flags
    forwarded_flags = evaluated_visual(requests[1])["listing_red_flags"]
    if reviewed < available:
        assert coverage.rooms_not_seen_in_review == ("bathroom",)
        assert flags.missing_room_photos == []
        assert flags.too_few_photos is flags.selective_angles is False
        assert flags.red_flag_count == 1
        assert forwarded_flags["missing_room_photos"] == []
        assert forwarded_flags["too_few_photos"] is forwarded_flags["selective_angles"] is False
        assert forwarded_flags["red_flag_count"] == 1
        assert "notes" not in forwarded_flags
        assert analysis.lowlights == ["Needs updating"]
        assert "The advert omits" not in requests[1]["messages"][0]["content"]
        assert "photo completeness is unknown" in analysis.summary
        assert analysis.bathroom.notes == (
            "Bathroom not seen in reviewed photos. "
            "Other gallery photos were not reviewed; verify at viewing."
        )
    else:
        assert coverage.rooms_not_seen_in_review == ()
        assert flags.missing_room_photos == ["bathroom"]
        assert flags.too_few_photos is flags.selective_angles is True
        assert flags.red_flag_count == 4
        assert "No bathroom photos supplied" in analysis.lowlights
        assert "No bathroom photos" not in analysis.lowlights
    assert flags.description_concerns == ["Clarify the advertised tenancy restriction"]
    assert analysis.condition.maintenance_concerns == ["Visible damaged wall"]
    assert analysis.bathroom.overall_condition == "unknown"
    assert visual == original


async def test_partial_review_preserves_observation_prose_without_photo_accusations(
    make_merged_property, tmp_path
):
    merged = gallery_property(make_merged_property, tmp_path)

    def respond(request):
        return httpx2.Response(
            200,
            json=PHASE1_VISUAL_RESPONSE
            if "tools" in json.loads(request.content)
            else PHASE2_EVALUATION_RESPONSE,
        )

    async with model_for(respond) as model:
        _, analysis = await model.analyze_single_merged(merged, data_dir=str(tmp_path))
    raw = PHASE1_VISUAL_RESPONSE["content"][0]["input"]
    assert analysis.image_review_coverage.is_partial
    assert analysis.summary == raw["summary"]
    assert analysis.bathroom.notes == raw["bathroom"]["notes"]
    assert analysis.kitchen.notes == raw["kitchen"]["notes"]


@pytest.mark.parametrize("floor_area_source", ["estimated", "rightmove"])
async def test_checkpoint_restart_retains_raw_observations_and_persists_coverage(
    make_merged_property, tmp_path, native_analysis_spending, floor_area_source
):
    merged = gallery_property(make_merged_property, tmp_path).model_copy(
        update={"floor_area_sqm": 55.0, "floor_area_source": floor_area_source}
    )
    database = str(tmp_path / "restart.db")
    visual, evaluation = responses_with_photo_accusations()
    requests = []
    fail_evaluation = True

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if "tools" in payload:
            return httpx2.Response(200, json=visual)
        if fail_evaluation:
            return httpx2.Response(
                503, json={"type": "error", "error": {"type": "api_error", "message": "fixture"}}
            )
        return httpx2.Response(200, json=evaluation)

    _, budget = native_analysis_spending
    async with PropertyStorage(database) as storage:
        await storage.pipeline.save_pre_analysis_properties([merged], {})
        async with model_for(respond, spend_repository=storage.spend, spend_policy=budget) as model:
            with pytest.raises(APIUnavailableError):
                await model.analyze_single_merged(merged, data_dir=str(tmp_path))
        async with storage._read_connection() as conn:
            cursor = await conn.execute("SELECT * FROM visual_checkpoints")
            checkpoint = dict(await cursor.fetchone())
        assert (
            json.loads(checkpoint["data_json"])["listing_red_flags"]
            == (visual["content"][0]["input"]["listing_red_flags"])
        )

    fail_evaluation = False
    async with PropertyStorage(database) as storage:
        recovered = (await storage.pipeline.get_pending_analysis_properties())[0]
        assert len(recovered.images) == 8
        assert recovered.floorplan.is_source_floorplan
        assert recovered.floor_area_source == floor_area_source
        async with model_for(respond, spend_repository=storage.spend, spend_policy=budget) as model:
            _, analysis = await model.analyze_single_merged(recovered, data_dir=str(tmp_path))
        assert analysis.image_review_coverage.reviewed_gallery_count == 7
        assert analysis.image_review_coverage.available_gallery_count == 8
        assert analysis.image_review_coverage.rooms_not_seen_in_review == ("bathroom",)
        assert analysis.lowlights == ["Needs updating"]
        async with storage._read_connection() as conn:
            cursor = await conn.execute("SELECT * FROM visual_checkpoints")
            assert dict(await cursor.fetchone()) == checkpoint
        await storage.pipeline.complete_analysis(recovered.unique_id, analysis)
        saved = await storage.get_quality_analysis(recovered.unique_id)
        assert saved.image_review_coverage == analysis.image_review_coverage
        assert saved.listing_red_flags.red_flag_count == 1
        assert saved.analysis_provenance.visual_input_sha256 == checkpoint["input_sha256"]
        # Historical records have unknown coverage, not invented counts.
        legacy = saved.model_dump(mode="json", exclude={"image_review_coverage"})
        assert PropertyQualityAnalysis.model_validate(legacy).image_review_coverage is None

    assert len(requests) == 3 and sum("tools" in request for request in requests) == 1
    assert ("<scraped_floor_area>" in visual_prompt(requests[0])) == (
        floor_area_source == "rightmove"
    )
    if floor_area_source == "estimated":
        assert "55.0 m²" not in visual_prompt(requests[0])
    assert evaluated_visual(requests[1]) == evaluated_visual(requests[2])


async def test_changed_available_gallery_invalidates_checkpoint_even_with_same_selected_images(
    make_merged_property, tmp_path, native_analysis_spending
):
    merged = gallery_property(make_merged_property, tmp_path)
    visual, _ = responses_with_photo_accusations()
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx2.Response(
            200 if "tools" in payload else 503,
            json=visual
            if "tools" in payload
            else {"type": "error", "error": {"type": "api_error", "message": "fixture"}},
        )

    async with model_for(respond) as model:
        with pytest.raises(APIUnavailableError):
            await model.analyze_single_merged(merged, data_dir=str(tmp_path))
        # Remove only the unreviewed eighth gallery image; the image request is
        # otherwise identical, but its completeness claim has changed.
        complete_gallery = merged.model_copy(update={"images": merged.images[:7]})
        with pytest.raises(APIUnavailableError):
            await model.analyze_single_merged(complete_gallery, data_dir=str(tmp_path))
    visual_requests = [request for request in requests if "tools" in request]
    assert len(visual_requests) == 2
    first, second = [request["messages"][0]["content"] for request in visual_requests]
    assert first[:-1] == second[:-1]
    assert "7 of 8 available" in first[-1]["text"]
    assert "7 of 7 available" in second[-1]["text"]
