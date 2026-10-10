"""Stored historical zeros must not become evidence of outdoor absence in the UI."""

import json

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.fit_score import FIT_SCORE_VERSION, compute_fit_score
from home_finder.models.quality import OUTDOOR_SPACE_FLAGS
from home_finder.web.filters import PropertyFilter
from home_finder.web.routes import router

NO_TYPES = dict.fromkeys(OUTDOOR_SPACE_FLAGS, False)
CASES = {
    "legacy_false": (NO_TYPES, "unknown"),
    "legacy_patio_notes": (
        {**NO_TYPES, "notes": "Private patio/lightwell; check access."},
        "unknown",
    ),
    "missing": (None, "unknown"),
    "unknown": ({**NO_TYPES, "availability": "unknown"}, "unknown"),
    "explicit_no": ({**NO_TYPES, "availability": "no"}, "no"),
    "yes_type_unknown": ({**NO_TYPES, "availability": "yes"}, "yes"),
    "patio": ({**NO_TYPES, "availability": "yes", "has_terrace": True}, "yes"),
    "positive_overrides_no": ({**NO_TYPES, "availability": "no", "has_garden": True}, "yes"),
    **{f"legacy_{name}": ({**NO_TYPES, name: True}, "yes") for name in OUTDOOR_SPACE_FLAGS},
}


@pytest.fixture
async def outdoor_storage(make_merged_property, make_quality_analysis):
    async with PropertyStorage(":memory:") as storage:
        for name, (outdoor, expected) in CASES.items():
            merged = make_merged_property(source_id=name, title=f"Home {name}")
            await storage.save_merged_property(merged)
            analysis = make_quality_analysis(
                outdoor_space=outdoor, lowlights=["No outdoor space", "Potential traffic noise"]
            )
            await storage.save_quality_analysis(merged.unique_id, analysis)
            async with storage._transaction() as conn:
                row = await (
                    await conn.execute(
                        "SELECT has_outdoor_space FROM quality_analyses WHERE property_unique_id=?",
                        (merged.unique_id,),
                    )
                ).fetchone()
                assert row[0] == {"yes": 1, "no": 0, "unknown": None}[expected]
                # Preserve the old JSON's missing availability and wrong cached zero.
                if name.startswith("legacy_"):
                    raw = {**analysis.model_dump(mode="json"), "outdoor_space": outdoor}
                    await conn.execute(
                        "UPDATE quality_analyses SET analysis_json=?, has_outdoor_space=? "
                        "WHERE property_unique_id=?",
                        (json.dumps(raw), int(expected == "yes"), merged.unique_id),
                    )
        yield storage


@pytest.fixture
def outdoor_client(outdoor_storage):
    app = FastAPI()
    app.state.storage = outdoor_storage
    app.state.settings = Settings(database_path=":memory:")
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("availability", ["yes", "no", "unknown"])
async def test_outdoor_filter_count_grid_and_map_use_evidence(outdoor_storage, availability):
    filters = PropertyFilter(outdoor_space=availability)
    assert filters.outdoor_space == availability
    expected = {f"openrent:{name}" for name, (_, state) in CASES.items() if state == availability}
    rows, total = await outdoor_storage.web.get_properties_paginated(filters)
    assert {row["unique_id"] for row in rows} == expected
    assert {row["id"] for row in await outdoor_storage.web.get_map_markers(filters)} == expected
    assert total == await outdoor_storage.web.get_filter_count(filters) == len(expected)
    for row in rows:
        assert ("No outdoor space" in row["lowlights"]) == (availability == "no")
        assert "No outdoor space" in json.loads(row["analysis_json"])["lowlights"]


async def test_absence_badge_filter_also_requires_explicit_evidence(outdoor_storage):
    filters = PropertyFilter(tags=["No outdoor space"])
    rows, total = await outdoor_storage.web.get_properties_paginated(filters)
    assert total == await outdoor_storage.web.get_filter_count(filters) == 1
    assert rows[0]["unique_id"] == "openrent:explicit_no"
    assert [row["id"] for row in await outdoor_storage.web.get_map_markers(filters)] == [
        "openrent:explicit_no"
    ]


@pytest.mark.parametrize("name", [name for name in CASES if name != "missing"])
def test_detail_badge_preserves_unknown_and_positive_evidence(outdoor_client, name):
    response = outdoor_client.get(f"/property/openrent:{name}")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    item = soup.find("h4", string="Outdoor Space").parent
    badge = item.select_one(".badge")
    expected = CASES[name][1]
    if expected == "unknown":
        assert badge.text == "Unknown"
        assert "quality-item-poor" not in item["class"]
        assert "quality-item-excellent" not in item["class"]
        assert "No outdoor space" not in item.text
        assert "No outdoor space" not in response.text
    elif expected == "no":
        assert badge.text == "No outdoor space"
        assert "quality-item-poor" in item["class"]
    else:
        assert "badge-green" in badge["class"]
        assert "quality-item-excellent" in item["class"]
        assert "No outdoor space" not in response.text
    if name == "patio":
        assert badge.text == "Terrace / patio / courtyard"
    if name == "yes_type_unknown":
        assert badge.text == "Outdoor space available"
    if name == "legacy_patio_notes":
        assert "Private patio/lightwell; check access." in item.text


def test_dashboard_offers_unknown_filter_and_preserves_it_in_results(outdoor_client):
    page = outdoor_client.get("/?outdoor_space=unknown")
    soup = BeautifulSoup(page.text, "html.parser")
    assert soup.select_one('select[name="outdoor_space"] option[value="unknown"][selected]')
    assert "Outdoor: unknown" in page.text
    assert outdoor_client.get("/count?outdoor_space=no").text == "1"
    fragment = outdoor_client.get("/?outdoor_space=no", headers={"HX-Request": "true"})
    assert "Home explicit_no" in fragment.text
    assert "Home legacy_false" not in fragment.text
    assert "Home legacy_patio_notes" not in fragment.text


async def test_fit_backfill_updates_only_derived_scores_and_clears_lost_evidence(
    tmp_path, make_property, make_quality_analysis
):
    path = str(tmp_path / "history.db")
    raw = json.dumps({"outdoor_space": NO_TYPES})
    prop = make_property(bedrooms=1, postcode=None)
    async with PropertyStorage(path) as storage:
        await storage.save_property(prop)
        await storage.save_quality_analysis(prop.unique_id, make_quality_analysis())
        async with storage._transaction() as conn:
            await conn.execute(
                "UPDATE quality_analyses SET analysis_json=?, has_outdoor_space=0, "
                "fit_score=0, fit_score_version=?",
                (raw, FIT_SCORE_VERSION - 1),
            )
    assert compute_fit_score(json.loads(raw), 1) is None
    async with PropertyStorage(path) as storage:
        async with storage._read_connection() as conn:
            row = await (await conn.execute("SELECT * FROM quality_analyses")).fetchone()
            attempts = await (await conn.execute("SELECT count(*) FROM paid_attempts")).fetchone()
        assert row["analysis_json"] == raw
        assert row["has_outdoor_space"] == 0  # No historical data rewrite or migration.
        assert row["fit_score"] is None
        assert row["fit_score_version"] == FIT_SCORE_VERSION
        assert attempts[0] == 0
        assert await storage.web.get_filter_count(PropertyFilter(outdoor_space="no")) == 0
