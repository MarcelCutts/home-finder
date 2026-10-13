"""Real query/HTTP regressions for exact tags and complete dashboard URLs."""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import UserStatus
from home_finder.web.filters import PropertyFilter
from home_finder.web.routes import router


@pytest.fixture
async def storage():
    async with PropertyStorage(":memory:") as storage:
        yield storage


@pytest.fixture
def client(storage):
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = Settings(_env_file=None, database_path=":memory:")
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["Washing machine"], {"positive", "positive_without_gas"}),
        (["No washing machine"], {"negative"}),
        (["Washing machine", "Gas hob"], {"positive"}),
        (["Washing machine", "No washing machine"], set()),
    ],
)
async def test_exact_tag_membership_agrees_in_grid_count_and_map(
    storage, client, make_merged_property, make_quality_analysis, tags, expected
):
    homes = {}
    for name, highlights, lowlights in [
        ("positive", ["Washing machine", "Gas hob"], []),
        ("negative", ["Gas hob"], ["No washing machine"]),
        ("positive_without_gas", ["Washing machine"], []),
    ]:
        home = make_merged_property(source_id=name)
        homes[name] = home.unique_id
        await storage.save_merged_property(home)
        await storage.save_quality_analysis(
            home.unique_id, make_quality_analysis(highlights=highlights, lowlights=lowlights)
        )
    filters = PropertyFilter(tags=tags)
    rows, total = await storage.web.get_properties_paginated(filters)
    expected_ids = {homes[name] for name in expected}
    assert {row["unique_id"] for row in rows} == expected_ids
    assert total == await storage.web.get_filter_count(filters) == len(expected_ids)
    assert {row["id"] for row in await storage.web.get_map_markers(filters)} == expected_ids
    response = client.get("/count", params=[("tag", tag) for tag in tags])
    assert response.status_code == 200
    assert response.text == str(len(expected_ids))


async def test_pagination_preserves_all_filters_and_fetches_the_clamped_page(
    storage, client, make_merged_property, make_quality_analysis
):
    analysis = make_quality_analysis(
        rating=5,
        kitchen={"overall_quality": "modern", "hob_type": "gas"},
        light_space={"natural_light": "good", "floor_level": "upper"},
        space={"living_room_sqm": 25, "total_area_sqm": 65, "hosting_layout": "good"},
        bedroom={"office_separation": "separate_area"},
        outdoor_space={"has_balcony": True},
        flooring_noise={"building_construction": "solid_brick", "hosting_noise_risk": "low"},
        listing_extraction={
            "property_type": "victorian",
            "pets_allowed": "yes",
            "broadband_type": "fttp",
        },
        value={"rating": "good"},
        highlights=["Washing machine", "Gas hob"],
    )
    ids = []
    for index in range(28):
        home = make_merged_property(
            source_id=f"page-{index}",
            price_pcm=1800 + index,
            bedrooms=0,
            postcode="E8 3RH",
            first_seen=datetime.now(UTC),
            floor_area_sqm=65,
        )
        await storage.save_merged_property(home)
        await storage.save_quality_analysis(home.unique_id, analysis)
        await storage.update_user_status(home.unique_id, UserStatus.INTERESTED)
        if index < 27:
            await storage.mark_off_market(home.unique_id)
            ids.append(home.unique_id)
    params = {
        "min_price": "0",
        "max_price": "2400",
        "bedrooms": "0",
        "min_fit_score": "0",
        "area": "E8",
        "property_type": "victorian",
        "outdoor_space": "yes",
        "natural_light": "good",
        "pets": "yes",
        "value_rating": "good",
        "hob_type": "gas",
        "floor_level": "upper",
        "building_construction": "solid_brick",
        "office_separation": "separate_area",
        "hosting_layout": "good",
        "hosting_noise_risk": "low",
        "broadband_type": "fttp",
        "min_living_room_sqm": "20",
        "min_floor_area_sqm": "60",
        "tag": ["Washing machine", "Gas hob"],
        "added": "7d",
        "status": "interested",
        "processing": "assessed",
        "off_market": "only",
        "sort": "price_asc",
    }
    response = client.get("/?" + urlencode(params, doseq=True))
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert len(soup.select(".property-card")) == 24
    next_link = soup.select_one(".pagination a")
    next_url = str(next_link["href"])
    expected_params = {
        key: value if isinstance(value, list) else [value] for key, value in params.items()
    }
    expected_params["page"] = ["2"]
    assert parse_qs(urlsplit(next_url).query) == expected_params

    for query in [next_url, next_url.replace("page=2", "page=999")]:
        response = client.get("/" + query, headers={"HX-Request": "true"})
        assert response.status_code == 200
        page = BeautifulSoup(response.text, "html.parser")
        assert {card["data-property-id"] for card in page.select(".property-card")} == set(ids[24:])
        assert page.select_one(".pagination-info").get_text() == "Page 2 of 2"
        assert parse_qs(urlsplit(response.headers["HX-Push-Url"]).query) == expected_params
        map_data = json.loads(page.select_one("#properties-map-data").get_text())
        assert {home["id"] for home in map_data} == set(ids)


def test_partial_exposes_validated_state_and_history_restore_returns_a_full_page(client):
    query = "/?bedrooms=99&min_fit_score=999&sort=bogus&off_market=only&tag=Gas+hob&tag=bad"
    response = client.get(query, headers={"HX-Request": "true"})
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert soup.select_one(".filter-form") is None
    state = json.loads(soup.select_one("#applied-filters").get_text())
    assert state["values"]["bedrooms"] == 10
    assert state["values"]["min_fit_score"] == 100
    assert state["values"]["tags"] == ["Gas hob"]
    assert parse_qs(urlsplit(response.headers["HX-Push-Url"]).query) == {
        "page": ["1"],
        "sort": ["newest"],
        "bedrooms": ["10"],
        "min_fit_score": ["100"],
        "off_market": ["only"],
        "tag": ["Gas hob"],
    }
    restored = client.get(
        query, headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    )
    assert restored.status_code == 200
    assert BeautifulSoup(restored.text, "html.parser").select_one(".filter-form") is not None
    assert restored.text.lstrip().startswith("<!DOCTYPE html>")
    assert set(restored.headers["Vary"].split(", ")) == {"HX-Request", "HX-History-Restore-Request"}
