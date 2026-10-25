"""Processing failures must not remove otherwise useful homes from owner review."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import PropertyImage, PropertySource
from home_finder.web.filters import PropertyFilter
from home_finder.web.routes import router


@pytest_asyncio.fixture
async def processing_storage(make_merged_property, make_quality_analysis):
    storage = PropertyStorage(":memory:")
    await storage.initialize()
    states = {
        "assessed": ("pending", "enriched"),
        "refresh": ("sent", "enriched"),
        "details": ("pending_enrichment", "pending"),
        "details_failed": ("pending_enrichment", "failed"),
        "analysis": ("pending_analysis", "enriched"),
        "fallback": ("pending_analysis", "enriched"),
        "plain": ("pending", "enriched"),
        "dropped": ("dropped", "enriched"),
        "off_market": ("pending", "enriched"),
    }
    for name, (notification, enrichment) in states.items():
        merged = make_merged_property(
            source_id=name, title=f"Home {name}", description="Retained listing description"
        )
        await storage.save_merged_property(merged)
        if name in {"assessed", "refresh", "fallback"}:
            await storage.save_quality_analysis(
                merged.unique_id,
                make_quality_analysis(overall_rating=None if name == "fallback" else 4),
            )
        async with storage._transaction() as conn:
            await conn.execute(
                "UPDATE properties SET notification_status=?, enrichment_status=? "
                "WHERE unique_id=?",
                (notification, enrichment, merged.unique_id),
            )
    async with storage._transaction() as conn:
        await conn.execute(
            "UPDATE quality_analyses SET fit_score=99 WHERE property_unique_id='openrent:fallback'"
        )
        await conn.execute(
            "UPDATE quality_analyses SET reanalysis_requested_at='2026-10-10T12:00:00Z' "
            "WHERE property_unique_id='openrent:refresh'"
        )
    await storage.mark_off_market("openrent:off_market")
    yield storage
    await storage.close()


@pytest.fixture
def processing_client(processing_storage):
    app = FastAPI()
    app.state.storage = processing_storage
    app.state.settings = Settings(database_path=":memory:")
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (
            PropertyFilter(),
            {"assessed", "refresh", "details", "details_failed", "analysis", "fallback", "plain"},
        ),
        (PropertyFilter(processing="assessed"), {"assessed", "refresh"}),
        (
            PropertyFilter(processing="unassessed"),
            {"details", "details_failed", "analysis", "fallback", "plain"},
        ),
        (PropertyFilter(min_fit_score=0), {"assessed", "refresh"}),
        (PropertyFilter(min_fit_score=0, processing="unassessed"), set()),
        (PropertyFilter(off_market="only"), {"off_market"}),
        (PropertyFilter(status="interested"), set()),
    ],
)
async def test_grid_count_map_share_processing_contract(processing_storage, filters, expected):
    rows, count = await processing_storage.web.get_properties_paginated(filters)
    markers = await processing_storage.web.get_map_markers(filters)
    ids = {f"openrent:{name}" for name in expected}
    assert {row["unique_id"] for row in rows} == ids
    assert {marker["id"] for marker in markers} == ids
    assert count == len(expected) == await processing_storage.web.get_filter_count(filters)
    for marker in markers:
        if not marker["is_assessed"]:
            assert marker["fit_score"] is None
            assert marker["one_line"] is None


async def test_fallback_score_does_not_rank_above_assessments(processing_storage):
    rows, _ = await processing_storage.web.get_properties_paginated(
        PropertyFilter(), sort="fit_desc"
    )
    assert {row["unique_id"] for row in rows[:2]} == {"openrent:assessed", "openrent:refresh"}


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (PropertyFilter(value_rating="excellent"), {"assessed", "refresh"}),
        (PropertyFilter(natural_light="good"), {"assessed", "refresh"}),
        (PropertyFilter(min_living_room_sqm=10), {"assessed", "refresh"}),
        (PropertyFilter(outdoor_space="no"), {"assessed"}),
    ],
)
async def test_explicit_assessment_filters_exclude_fallbacks(processing_storage, filters, expected):
    # The failed assessment retains plausible defaults, including a cheap rent
    # rating. Those defaults must not satisfy an explicit owner preference.
    async with processing_storage._transaction() as conn:
        await conn.execute(
            "UPDATE quality_analyses SET has_outdoor_space=0, "
            "analysis_json=json_set(analysis_json, '$.value', json('{\"rating\":\"excellent\"}'), "
            "'$.outdoor_space', json('{\"availability\":\"no\"}'))"
        )
        await conn.execute(
            "UPDATE quality_analyses SET has_outdoor_space=NULL, "
            "analysis_json=json_remove(analysis_json, '$.outdoor_space.availability') "
            "WHERE property_unique_id='openrent:refresh'"
        )
    rows, count = await processing_storage.web.get_properties_paginated(filters)
    markers = await processing_storage.web.get_map_markers(filters)
    ids = {f"openrent:{name}" for name in expected}
    assert {row["unique_id"] for row in rows} == ids
    assert {marker["id"] for marker in markers} == ids
    assert count == len(ids) == await processing_storage.web.get_filter_count(filters)


async def test_floor_area_filter_keeps_source_measurements_without_assessment(processing_storage):
    async with processing_storage._transaction() as conn:
        await conn.execute(
            "UPDATE properties SET floor_area_sqm=60 WHERE unique_id='openrent:plain'"
        )
        await conn.execute(
            "UPDATE quality_analyses SET "
            "analysis_json=json_set(analysis_json, '$.space.total_area_sqm', 65)"
        )
    filters = PropertyFilter(min_floor_area_sqm=50)
    rows, count = await processing_storage.web.get_properties_paginated(filters)
    markers = await processing_storage.web.get_map_markers(filters)
    ids = {"openrent:plain", "openrent:assessed", "openrent:refresh"}
    assert {row["unique_id"] for row in rows} == ids
    assert {marker["id"] for marker in markers} == ids
    assert count == len(ids) == await processing_storage.web.get_filter_count(filters)


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("details", "Awaiting listing details"),
        ("details_failed", "Listing details incomplete"),
        ("analysis", "Awaiting assessment"),
        ("fallback", "Assessment incomplete"),
        ("plain", "Not assessed"),
        ("refresh", "Assessment refresh requested"),
        ("dropped", "Excluded from search"),
    ],
)
def test_imageless_detail_remains_accessible(processing_client, name, label):
    response = processing_client.get(f"/property/openrent:{name}")
    assert response.status_code == 200
    assert label in response.text
    assert "Photos not available" in response.text
    assert "Retained listing description" in response.text
    assert "pending for" not in response.text.lower()
    if name != "refresh":
        assert "A fit score is not available yet" in response.text
    assert processing_client.get("/property/unknown").status_code == 404


def test_processing_filter_survives_htmx_and_does_not_show_fallback_score(processing_client):
    response = processing_client.get("/?processing=unassessed", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Home fallback" in response.text
    assert "Home assessed" not in response.text
    assert "Home dropped" not in response.text
    assert "First seen" in response.text
    assert "fit-score-number" not in response.text
    assert processing_client.get("/count?processing=unassessed").text == "5"
    start = response.text.index('id="properties-map-data"')
    map_json = response.text[start:].split(">", 1)[1].split("</script>", 1)[0]
    assert all(marker["fit_score"] is None for marker in json.loads(map_json))


async def test_pending_owner_status_persists_and_refresh_retains_sent_state(
    processing_client, processing_storage
):
    response = processing_client.patch(
        "/property/openrent:details/status", data={"status": "interested", "source": "card"}
    )
    assert response.status_code == 200
    rows, count = await processing_storage.web.get_properties_paginated(
        PropertyFilter(status="interested", processing="unassessed")
    )
    assert count == 1
    assert rows[0]["unique_id"] == "openrent:details"
    refreshed = await processing_storage.web.get_property_detail("openrent:refresh")
    assert refreshed["quality_analysis"] is not None
    assert refreshed["notification_status"] == "sent"
    assert refreshed["processing_state"] == "refresh_requested"


def test_diagnostics_reports_recorded_states(processing_client):
    counts = processing_client.get("/diagnostics").json()["processing_counts"]
    assert sum(counts.values()) == 9
    assert counts["excluded"] == 1
    assert counts["assessment_incomplete"] == 1
    assert counts["refresh_requested"] == 1


@pytest.mark.parametrize("value", [None, "", "all", "bogus"])
def test_all_or_invalid_processing_is_no_filter(value):
    assert PropertyFilter(processing=value).processing is None


def test_processing_filter_chip():
    assert PropertyFilter(processing="Unassessed").active_filter_chips() == [
        {"key": "processing", "label": "Unassessed"}
    ]


@pytest.mark.parametrize("origin", ["unknown", "source_declared"])
async def test_floorplan_role_labels_preserve_uncertainty(
    processing_storage, processing_client, origin
):
    await processing_storage.save_property_images(
        "openrent:details",
        [
            PropertyImage(
                url="https://example.com/floorplan.jpg",
                source=PropertySource.RIGHTMOVE,
                image_type="floorplan",
                floorplan_origin=origin,
            )
        ],
    )
    response = processing_client.get("/property/openrent:details")
    assert response.status_code == 200
    if origin == "unknown":
        assert "Floorplan from Rightmove; the listing's own label wasn't recorded." in response.text
        assert "as labelled on" not in response.text
    else:
        assert "Floorplan as labelled on Rightmove." in response.text
        assert "label wasn't recorded" not in response.text


@pytest.mark.parametrize("processing", ["assessed", "unassessed"])
async def test_pagination_retains_processing_and_zero_score_filters(
    processing_storage, processing_client, make_merged_property, make_quality_analysis, processing
):
    for index in range(25):
        merged = make_merged_property(source_id=f"page-{index}")
        await processing_storage.save_merged_property(merged)
        if processing == "assessed":
            await processing_storage.save_quality_analysis(
                merged.unique_id, make_quality_analysis()
            )
    query = f"/?processing={processing}"
    if processing == "assessed":
        query += "&min_fit_score=0"
    response = processing_client.get(query)
    link = BeautifulSoup(response.text, "html.parser").select_one(".pagination a")["href"]
    params = parse_qs(urlsplit(link).query)
    assert params["processing"] == [processing]
    if processing == "assessed":
        assert params["min_fit_score"] == ["0"]
    page_two = BeautifulSoup(processing_client.get(link).text, "html.parser")
    assert page_two.select_one(".pagination-info").get_text() == "Page 2 of 2"
