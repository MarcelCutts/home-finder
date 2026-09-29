"""Receipt provenance is durable without becoming property facts or content changes."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.filters.listing_text import select_listing_text
from home_finder.models import FurnishType, MergedProperty
from home_finder.models.observations import CollectionQuery, SourceObservation
from home_finder.scrapers.observations import collection_query, request_context, search_observations

AT = datetime(2026, 9, 1, tzinfo=UTC)


def query(**changes):
    return collection_query(
        **{
            "area": "scope-only-area",
            "min_price": 1000,
            "max_price": 3000,
            "min_bedrooms": 0,
            "max_bedrooms": 3,
            "furnish_types": (FurnishType.PART_FURNISHED,),
            "min_bathrooms": 1,
            "include_let_agreed": False,
        }
        | changes
    )


def observed(prop, *, scope=None):
    props, errors = search_observations([prop], observed_at=AT, request=scope)
    assert errors == 0
    return props[0].source_observation


def revised(event, **changes):
    return SourceObservation.model_validate(
        event.model_dump(mode="json", exclude_none=True) | changes
    )


def test_request_fingerprint_retains_no_url_credentials_or_raw_query_strings():
    raw = "https://user:secret@example.invalid/search?access_token=never-store-this#fragment"
    context = request_context(raw, query=query(), page_index=2)
    serialized = context.model_dump_json()
    assert context.requested_url_sha256 == hashlib.sha256(raw.encode()).hexdigest()
    assert "secret" not in serialized and "never-store-this" not in serialized
    assert "example.invalid" not in serialized and "fragment" not in serialized
    assert context.query.furnish_types == ("part_furnished",)
    assert context.query.min_bathrooms == 1 and not context.query.include_let_agreed
    assert context.page_index == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"min_price": 3001},
        {"min_bedrooms": 4},
        {"min_price": True},
        {"max_price": 1_000_001},
        {"area": " "},
        {"area": "x" * 101},
        {"furnish_types": ["unknown"]},
        {"furnish_types": ["furnished"] * 4},
        {"include_let_agreed": "false"},
    ],
)
def test_scope_schema_is_bounded_and_keeps_real_boolean_semantics(changes):
    with pytest.raises(ValidationError):
        CollectionQuery.model_validate(query().model_dump() | changes)


@pytest.mark.parametrize("page", [-1, 1001, True])
def test_invalid_page_index_is_not_a_receipt_scope(page):
    with pytest.raises(ValueError):
        request_context("https://example.invalid/", page_index=page)


@pytest.mark.parametrize("url", ["", "x" * 16_385])
def test_url_fingerprinting_is_bounded(url):
    with pytest.raises(ValueError, match="bound"):
        request_context(url)


def test_query_and_page_changes_are_not_content_revisions(sample_property):
    first = observed(
        sample_property, scope=request_context("https://example.invalid/1", query=query())
    )
    second = revised(
        first,
        event_id="another-receipt",
        producer_id="another-collector",
        observed_at=AT + timedelta(days=1),
        request=request_context(
            "https://example.invalid/2", query=query(max_price=4000), page_index=1
        ),
        parser=first.parser.model_copy(update={"version": "2", "code_sha256": "c" * 64}),
        raw_capture={
            "capture_manifest_sha256": "a" * 64,
            "record_index": 0,
            "response_sha256": "b" * 64,
            "response_bytes": 123,
            "capture_mode": "fixture",
        },
    )
    assert first.sha256 != second.sha256
    assert first.material_sha256 == second.material_sha256
    assert first.field_material_sha256("description") == second.field_material_sha256("description")
    same_fact_correction = revised(
        second, origin="correction", correction_reason="Confirmed unchanged"
    )
    assert same_fact_correction.material_sha256 == first.material_sha256


def test_actual_changes_affect_only_their_field_dependency_hash(sample_property):
    first = observed(sample_property)
    fields = first.fields.model_dump(mode="json", exclude_none=True)
    second = revised(first, event_id="price-changed", fields=fields | {"price_pcm": {"value": 999}})
    assert first.material_sha256 != second.material_sha256
    assert first.field_material_sha256("price_pcm") != second.field_material_sha256("price_pcm")
    assert first.field_material_sha256("description") == second.field_material_sha256("description")
    third = revised(
        first, event_id="text-changed", fields=fields | {"description": {"value": "Changed"}}
    )
    assert third.field_material_sha256("description") != first.field_material_sha256("description")
    assert third.field_material_sha256("price_pcm") == first.field_material_sha256("price_pcm")
    assert first.field_material_sha256("availability_date") is None
    with pytest.raises(ValueError, match="Unknown"):
        first.field_material_sha256("query")


def test_missing_clear_and_omission_are_distinct_material_states(sample_property):
    first = observed(sample_property)
    assert revised(first, normalization_omissions={}).material_sha256 == first.material_sha256
    fields = first.fields.model_dump(mode="json", exclude_none=True)
    absent = revised(first, fields=fields | {"description": None})
    cleared = revised(
        first,
        origin="correction",
        correction_reason="Explicit withdrawal",
        fields=fields | {"description": {"state": "cleared"}},
    )
    assert len({event.material_sha256 for event in (first, absent, cleared)}) == 3
    assert absent.field_material_sha256("description") is None
    assert cleared.field_material_sha256("description") is not None
    omitted = revised(
        first, normalization_omissions={"floor_area_readings": "unsupported_quantity"}
    )
    assert omitted.material_sha256 != first.material_sha256
    assert omitted.field_material_sha256("description") == first.field_material_sha256(
        "description"
    )


def test_query_scope_never_becomes_a_source_fact_or_selected_description(sample_property):
    prop = sample_property.model_copy(
        update={"postcode": None, "description": "Actual source text"}
    )
    context = request_context("https://example.invalid/search", query=query())
    event = observed(prop, scope=context)
    assert event.fields.postcode is None
    assert "scope-only-area" not in event.fields.model_dump_json()
    tagged = prop.model_copy(update={"source_observation": event})
    merged = MergedProperty(
        canonical=tagged,
        sources=(tagged.source,),
        source_urls={tagged.source: tagged.url},
        min_price=tagged.price_pcm,
        max_price=tagged.price_pcm,
    )
    selected = select_listing_text(merged)
    assert selected.description == "Actual source text"
    assert "scope-only-area" not in selected.model_dump_json()


async def test_context_and_material_hashes_survive_storage_without_changing_old_payloads(
    tmp_path, sample_property
):
    legacy = observed(sample_property)
    assert "request" not in json.loads(legacy.payload_json())
    with_context = observed(
        sample_property, scope=request_context("https://example.invalid/", query=query())
    )
    payload = with_context.payload_json()
    async with PropertyStorage(str(tmp_path / "evidence.db")) as db:
        await db.observations.ingest([legacy, with_context])
        saved = await db.observations.get(with_context.sha256)
        assert saved.request == with_context.request and saved.payload_json() == payload
        assert saved.material_sha256 == legacy.material_sha256
        assert (await db.observations.get(legacy.sha256)).payload_json() == legacy.payload_json()
