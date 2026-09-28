"""Actual indexed detail fixture plus explicit malformed-graph mutations."""

import json
from pathlib import Path

import httpx
import pytest

from home_finder.models import Property
from home_finder.pipeline.capture_replay import _ReplayDetailFetcher
from home_finder.scrapers import rightmove_payloads
from home_finder.scrapers.rightmove_payloads import MAX_DETAIL_DEPTH, detail_property


def indexed_html(table, **envelope):
    outer = {"data": json.dumps(table), "encoding": "on", **envelope}
    return "<script>window.__PAGE_MODEL = " + json.dumps(outer) + ";</script>"


@pytest.fixture
def indexed_fixture(fixtures_path: Path) -> str:
    return (fixtures_path / "rightmove_indexed_detail.html").read_text()


def test_retained_graph_preserves_literals_null_and_empty_collections(indexed_fixture):
    prop = detail_property(indexed_fixture, "100001")
    assert prop["status"] == {"published": True, "archived": False}
    assert prop["bedrooms"] == 1 and type(prop["bedrooms"]) is int
    assert prop["location"]["latitude"] == 51.54
    assert prop["images"][0]["caption"] is None
    assert prop["floorplans"] == [] and prop["sizings"] == []
    assert len(prop["images"]) == 9 and len(prop["keyFeatures"]) == 8
    assert "buildingId" not in prop  # Missing remains distinct from a retained null.


async def test_actual_detail_path_extracts_retained_indexed_fields(indexed_fixture):
    url = "https://www.rightmove.co.uk/properties/100001"
    fetcher = _ReplayDetailFetcher(
        httpx.Response(200, text=indexed_fixture, request=httpx.Request("GET", url))
    )
    prop = Property(
        source="rightmove",
        source_id="100001",
        url=url,
        title="Example flat",
        address="Example Road, London E8",
        price_pcm=2500,
        bedrooms=1,
    )
    result = await fetcher.fetch_detail_page(prop)
    assert result is not None
    assert result.description == "<p>A one bedroom flat with a shared garden.</p>"
    assert len(result.gallery_urls) == 9 and len(result.features) == 8
    assert result.postcode == "E8 1AA"
    assert result.latitude == 51.54 and result.longitude == -0.06
    assert result.floorplan_url is None and result.floor_area_sqm is None


def test_shared_container_is_reused_without_tree_expansion():
    table = [
        {"propertyData": 1},
        {"id": 2, "left": 3, "right": 3},
        "123",
        {"values": 4},
        [5, 6, 7, 8],
        0,
        False,
        None,
        -0.125,
    ]
    prop = detail_property(indexed_html(table), "123")
    assert prop["left"] is prop["right"]
    assert prop["left"]["values"] == [0, False, None, -0.125]
    assert type(prop["left"]["values"][0]) is int
    assert type(prop["left"]["values"][1]) is bool


@pytest.mark.parametrize("reference", [-1, 3, True, 1.0, "1", None, {}, []])
def test_invalid_references_are_not_coerced_or_guessed(reference):
    with pytest.raises(ValueError, match="invalid_page_model_reference"):
        detail_property(indexed_html([{"propertyData": reference}, {"id": 2}, "123"]), "123")


@pytest.mark.parametrize(
    "table,reason",
    [
        ([], "page_model_node_limit"),
        ({"propertyData": 1}, "page_model_node_limit"),
        ([None], "invalid_page_model_root"),
        ([{}], "missing_property_data"),
        ([{"propertyData": 1}, {}], "missing_property_data"),
        ([{"propertyData": 1}, {"description": 2}, "x"], "missing_property_id"),
        ([{"propertyData": 1}, {"id": 2}, "999"], "mismatched_property_id"),
        ([{"propertyData": 0}], "cyclic_page_model_reference"),
        ([{"propertyData": 1}, {"id": 2, "back": 0}, "123"], "cyclic_page_model_reference"),
        ([{"propertyData": 1}, {"id": 2}, "123", [3]], "cyclic_page_model_reference"),
        ([{"propertyData": 1}, {"id": 2}, "123", float("nan")], "nonfinite_page_model_number"),
    ],
)
def test_malformed_indexed_models_fail_explicitly(table, reason):
    with pytest.raises(ValueError, match=reason):
        detail_property(indexed_html(table), "123")


@pytest.mark.parametrize("envelope", [{"encoding": "off"}, {"data": []}, {"encoding": None}])
def test_unknown_envelopes_are_not_treated_as_plain_json(envelope):
    with pytest.raises(ValueError, match="unsupported_page_model_variant"):
        detail_property(indexed_html([{"propertyData": 1}, {"id": 2}, "123"], **envelope), "123")


@pytest.mark.parametrize(
    "limit,value,reason",
    [
        ("MAX_DETAIL_NODES", 2, "page_model_node_limit"),
        ("MAX_DETAIL_REFERENCES", 1, "page_model_reference_limit"),
    ],
)
def test_node_and_reference_budgets(monkeypatch, limit, value, reason):
    monkeypatch.setattr(rightmove_payloads, limit, value)
    with pytest.raises(ValueError, match=reason):
        detail_property(indexed_html([{"propertyData": 1}, {"id": 2}, "123"]), "123")


def test_deep_reference_chain_fails_before_python_recursion_limit():
    table = [{"propertyData": 1}, {"id": 2, "chain": 3}, "123"]
    table.extend([[index + 1] for index in range(3, MAX_DETAIL_DEPTH + 3)])
    table.append(None)
    with pytest.raises(ValueError, match="page_model_depth_limit"):
        detail_property(indexed_html(table), "123")


def test_cached_subtree_cannot_bypass_depth_limit():
    # Visit/cache the 40-edge subtree first, then reach it through another chain.
    table = [{"propertyData": 1, "long_path": 44}, {"id": 2, "short_path": 3}, "123"]
    table.extend([[index + 1] for index in range(3, 43)])
    table.append(None)  # index 43
    table.extend([[index + 1] for index in range(44, 84)])
    table.append([3])
    with pytest.raises(ValueError, match="page_model_depth_limit"):
        detail_property(indexed_html(table), "123")


def test_numeric_overflow_is_rejected():
    html = indexed_html([{"propertyData": 1}, {"id": 2, "value": 3}, "123", 1.25])
    with pytest.raises(ValueError, match="nonfinite_page_model_number"):
        detail_property(html.replace("1.25", "1e999"), "123")


def test_javascript_expression_is_never_evaluated():
    with pytest.raises(ValueError):
        detail_property("window.__PAGE_MODEL = (() => ({propertyData: {id: 123}}))()", "123")
