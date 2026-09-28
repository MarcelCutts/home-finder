"""Reported ranges, units and fractional quantities survive parsing and replay."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import enrich_merged_properties
from home_finder.filters.quality_prompts import build_user_prompt
from home_finder.models import MergedProperty, PropertySource
from home_finder.models.observations import SourceObservation
from home_finder.models.reference import PrintedArea
from home_finder.models.source_area import SourceAreaReading, display_area_square_metres
from home_finder.pipeline.capture import CapturePlan, capture
from home_finder.pipeline.capture_replay import replay
from home_finder.scrapers.detail_fetcher import DetailFetcher, _zoopla_size_from_rsc
from home_finder.scrapers.rightmove_payloads import detail_property
from home_finder.scrapers.source_area import rightmove_areas, single_area


def reading(**changes):
    return SourceAreaReading.model_validate(
        {
            "source_field": "sizings[0]",
            "unit": "sq_ft",
            "source_unit": "sqft",
            "lower": "500",
            "upper": "500",
            **changes,
        }
    )


def rightmove_html(raw_sizings, *, with_image=False):
    return (
        '<html><script>window.PAGE_MODEL = {"propertyData":{"id":100001,'
        + ('"images":[{"url":"https://example.com/area.jpg"}],' if with_image else "")
        + '"text":{"description":"Retained source description"},"sizings":'
        + raw_sizings
        + "}};</script></html>"
    )


def zoopla_html(raw_quantity):
    chunk = '1:{"taxonomy":{"sizeSqft":' + raw_quantity + "}}"
    return "<html><script>self.__next_f.push([1," + json.dumps(chunk) + "]);</script></html>"


@pytest.mark.parametrize(
    "changes",
    [
        {"lower": True},
        {"lower": 500.25},
        {"lower": "NaN"},
        {"lower": "Infinity"},
        {"lower": "0"},
        {"lower": "100001"},
        {"lower": "500.000000001"},
        {"lower": "501", "upper": "500"},
        {"lower": None, "upper": None},
        {"unit": "sq_m", "source_unit": "sqft"},
        {"basis": "gross_internal"},
    ],
)
def test_quantity_contract_rejects_ambiguous_or_unsupported_input(changes):
    with pytest.raises(ValidationError):
        reading(**changes)


def test_exact_conversion_matches_independent_printed_reference_contract():
    reported = reading()
    reference = PrintedArea(amount="500", unit="sq_ft", definition="unspecified_total")
    assert reported.lower_square_metres == reference.square_metres == Decimal("46.45152000")
    assert reported.lower == Decimal("500") and reported.basis == "unspecified"
    assert display_area_square_metres((reported,)) == 46.5
    assert reading(unit="sq_m", source_unit="sqm", lower="46.45152", upper="46.45152").kind == (
        "point"
    )


@pytest.mark.parametrize(
    "lower,upper,kind",
    [(None, "500", "maximum"), ("500", None, "minimum"), ("500", "600", "range")],
)
def test_bound_or_range_never_becomes_a_scalar_total(lower, upper, kind):
    area = reading(lower=lower, upper=upper)
    assert area.kind == kind and display_area_square_metres((area,)) is None


def test_disagreeing_units_or_a_mixed_bound_are_not_arbitrarily_selected():
    point = reading()
    compatible = reading(unit="sq_m", source_unit="sqm", lower="46.45", upper="46.45")
    disagreement = reading(unit="sq_m", source_unit="sqm", lower="55", upper="55")
    assert display_area_square_metres((point, compatible)) == 46.5
    assert display_area_square_metres((point, disagreement)) is None
    assert display_area_square_metres((point, reading(upper=None))) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"unit": []},
        {"unit": "acres"},
        {"unit": "sqft", "minimumSize": True},
        {"unit": "sqft", "minimumSize": 0},
        {"unit": "sqft"},
    ],
)
def test_invalid_optional_sizing_marks_the_whole_set_unavailable(bad):
    valid = {"unit": "sqft", "minimumSize": 500, "maximumSize": 500}
    assert rightmove_areas([valid, bad]) == (None, "unsupported_quantity")
    assert rightmove_areas([valid] * 11) == (None, "unsupported_quantity")


async def test_rightmove_fractional_json_preserves_range_and_ledger_projection(
    tmp_path, make_property, monkeypatch
):
    prop = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url="https://www.rightmove.co.uk/properties/100001",
    )
    html = rightmove_html('[{"unit":"sqft","minimumSize":650.1250,"maximumSize":700.8750}]')
    response = httpx.Response(200, text=html, request=httpx.Request("GET", str(prop.url)))
    async with DetailFetcher() as fetcher, PropertyStorage(str(tmp_path / "area.db")) as storage:
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))
        detail = await fetcher.fetch_detail_page(prop)
        assert detail.floor_area_sqm is None
        event = detail.source_observation
        area = event.fields.floor_area_readings.value[0]
        assert str(area.lower) == "650.1250" and str(area.upper) == "700.8750"
        assert area.lower_square_metres == Decimal("60.398588880000")
        await storage.retain_detail_observation(event)
        saved = await storage.observations.get(event.sha256)
        assert saved.payload_json() == event.payload_json()
        projection = await storage.observations.project(prop.unique_id)
        assert projection.fields["floor_area_readings"].value[0]["lower"] == "650.1250"
        assert projection.fields["floor_area_readings"].observation_ids == (event.sha256,)


@pytest.mark.parametrize("source", [PropertySource.RIGHTMOVE, PropertySource.ZOOPLA])
async def test_point_quantity_keeps_fraction_before_display_rounding(
    make_property, monkeypatch, source
):
    html = (
        rightmove_html('[{"unit":"sqft","minimumSize":750.75,"maximumSize":750.75}]')
        if source == PropertySource.RIGHTMOVE
        else zoopla_html("750.75")
    )
    url = (
        "https://www.rightmove.co.uk/properties/100001"
        if source == PropertySource.RIGHTMOVE
        else "https://www.zoopla.co.uk/to-rent/details/100001/"
    )
    prop = make_property(source=source, source_id="100001", url=url)
    response = httpx.Response(200, text=html, request=httpx.Request("GET", url))
    async with DetailFetcher() as fetcher:
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))
        monkeypatch.setattr(fetcher, "_curl_get_with_retry", AsyncMock(return_value=response))
        result = await fetcher.fetch_detail_page(prop)
    area = result.source_observation.fields.floor_area_readings.value[0]
    assert area.kind == "point" and area.lower == Decimal("750.75")
    assert area.lower_square_metres == Decimal("69.74695728")
    assert result.floor_area_sqm == 69.7
    if source == PropertySource.ZOOPLA:
        assert _zoopla_size_from_rsc(html) == Decimal("750.75")


async def test_malformed_optional_area_keeps_description_without_fabricating_quantity(
    make_property, monkeypatch
):
    prop = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url="https://www.rightmove.co.uk/properties/100001",
    )
    html = rightmove_html('[{"unit":"sqft","minimumSize":true,"maximumSize":1000}]')
    response = httpx.Response(200, text=html, request=httpx.Request("GET", str(prop.url)))
    async with DetailFetcher() as fetcher:
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))
        result = await fetcher.fetch_detail_page(prop)
    event = result.source_observation
    assert event.fields.description.value == "Retained source description"
    assert event.fields.floor_area_readings is None
    assert event.normalization_omissions == {"floor_area_readings": "unsupported_quantity"}
    assert result.floor_area_sqm is None


async def test_range_does_not_reach_prompt_as_exact_total_and_is_durable_before_media(
    tmp_path, make_property, monkeypatch
):
    prop = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url="https://www.rightmove.co.uk/properties/100001",
    )
    merged = MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )
    html = rightmove_html('[{"unit":"sqft","minimumSize":500,"maximumSize":800}]', with_image=True)
    response = httpx.Response(200, text=html, request=httpx.Request("GET", str(prop.url)))
    async with PropertyStorage(":memory:") as storage, DetailFetcher() as fetcher:
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))

        async def download(_url):
            retained = await storage.observations.history(prop.unique_id)
            assert retained[0].fields.floor_area_readings.value[0].kind == "range"
            return b"fixture photo bytes"

        monkeypatch.setattr(fetcher, "download_image_bytes", download)
        result = await enrich_merged_properties(
            [merged], fetcher, storage=storage, data_dir=str(tmp_path)
        )
        enriched = result.enriched[0]
        assert enriched.floor_area_sqm is None
        history = await storage.observations.history(prop.unique_id)
        assert history[0].fields.floor_area_readings.value[0].kind == "range"
    prompt = build_user_prompt(2000, 1, 2000, floor_area_sqm=enriched.floor_area_sqm)
    assert "<scraped_floor_area>" not in prompt
    point_prompt = build_user_prompt(2000, 1, 2000, floor_area_sqm=46.5)
    assert (
        "46.5 m² (rounded)" in point_prompt
        and "provenance and measurement basis are unspecified" in point_prompt
    )


async def test_offline_replay_serializes_exact_quantities_and_retains_the_raw_reference(
    tmp_path, make_property
):
    html = rightmove_html('[{"unit":"sqm","minimumSize":65.1250,"maximumSize":65.1250}]')
    (tmp_path / "input.html").write_text(html)
    prop = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url="https://www.rightmove.co.uk/properties/100001",
    )
    plan = CapturePlan.model_validate(
        {
            "source": "rightmove",
            "max_requests": 1,
            "requests": [
                {
                    "url": str(prop.url),
                    "kind": "detail",
                    "scope": "synthetic decimal quantity",
                    "listing": prop.model_dump(mode="json"),
                    "fixture": "input.html",
                    "fixture_observed_at": datetime(2026, 9, 1, tzinfo=UTC),
                }
            ],
        }
    )
    raw = await capture(plan, tmp_path / "captures", fixture_root=tmp_path)
    output = await replay(raw, tmp_path / "replays")
    record = json.loads((output / "replay.json").read_bytes())["results"][0]
    assert record["detail"]["floor_area_readings"][0]["lower"] == "65.1250"
    event = SourceObservation.model_validate(record["observations"][0])
    assert event.fields.floor_area_readings.value[0].lower == Decimal("65.1250")
    assert event.raw_capture.capture_mode == "fixture" and event.raw_capture.response_bytes > 0


def test_legacy_converted_float_is_not_relabelled_as_exact_source_evidence():
    assert single_area(650.5, source_field="taxonomy.sizeSqft") == (None, "unsupported_quantity")


def test_indexed_json_keeps_decimal_leaf_precision_without_weakening_reference_rules():
    # Synthetic indexed quantity contract, not a claimed live positive-size capture.
    table = (
        '[{"propertyData":1},{"id":2,"sizings":3},100001,[4],'
        '{"unit":5,"minimumSize":6,"maximumSize":6},"sqft",650.12345678]'
    )
    html = "window.__PAGE_MODEL = " + json.dumps({"data": table, "encoding": "on"})
    parsed = detail_property(html, "100001", exact_numbers=True)
    assert parsed["sizings"][0]["minimumSize"] == Decimal("650.12345678")
    readings, omission = rightmove_areas(parsed["sizings"])
    assert readings[0].lower == Decimal("650.12345678") and omission is None
    bad = html.replace('\\"sizings\\":3', '\\"sizings\\":3.0')
    with pytest.raises(ValueError, match="invalid_page_model_reference"):
        detail_property(bad, "100001", exact_numbers=True)
