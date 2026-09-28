"""Current text agrees with immutable replay and survives sparse arrival/rebuild."""

import itertools
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.filters.deduplication import Deduplicator
from home_finder.filters.listing_text import select_listing_text, supported_council_tax_band
from home_finder.models import ListingTextEvidence, UserStatus
from home_finder.models.listing_text import merge_listing_text
from home_finder.models.observations import (
    SourceObservation,
    observation_field_rows,
    project_field_rows,
)

AT = datetime(2026, 9, 1, tzinfo=UTC)


def event(prop, name, *, minute=0, origin="detail", **fields):
    return SourceObservation(
        producer_id="text-projection-fixture",
        event_id=name,
        source=prop.source,
        source_id=prop.source_id,
        listing_url=prop.url,
        origin=origin,
        observed_at=AT + timedelta(minutes=minute),
        parser={"name": "fixture", "version": "1", "code_sha256": "1" * 64},
        correction_reason="Explicit fixture correction" if origin == "correction" else None,
        fields=fields,
    )


def fold(events):
    result = ListingTextEvidence()
    for item in events:
        result = merge_listing_text(result, ListingTextEvidence.from_observation(item))
    return result


@pytest.mark.parametrize("scenario", ["authority", "conflict", "clear", "resolve"])
def test_all_arrival_orders_and_duplicate_delivery_agree_with_ledger(sample_property, scenario):
    events = [
        event(
            sample_property, "search", minute=10, origin="search", description={"value": "Summary"}
        ),
        event(
            sample_property,
            "detail",
            description={"value": "Detail"},
            features={"value": ["Gas hob"]},
        ),
        event(sample_property, "features", minute=1, features={"value": ["Induction hob"]}),
    ]
    if scenario == "conflict":
        events.append(
            event(sample_property, "different", description={"value": "Conflicting detail"})
        )
    if scenario in {"clear", "resolve"}:
        events.append(
            event(
                sample_property,
                "clear",
                origin="correction",
                minute=2,
                description={"state": "cleared"},
                features={"state": "cleared"},
            )
        )
    if scenario == "resolve":
        events.append(
            event(sample_property, "resolved", minute=3, description={"value": "Revised detail"})
        )
    projection = project_field_rows(
        sample_property.unique_id, [row for item in events for row in observation_field_rows(item)]
    )
    outcomes = set()
    for order in itertools.permutations(events):
        current = fold([*order, *reversed(order)])
        outcomes.add(current.model_dump_json())
        for name, selected in projection.fields.items():
            value = getattr(current, name)
            assert getattr(current, f"{name}_conflicting") == (selected.state == "conflicting")
            assert getattr(current, f"{name}_observed_at") == selected.observed_at
            assert getattr(current, f"{name}_observation_ids") == selected.observation_ids
            if selected.state in {"cleared", "conflicting"}:
                assert value == ("" if name == "description" else ())
            else:
                assert value == (selected.value if name == "description" else tuple(selected.value))
    assert len(outcomes) == 1


def test_identical_ties_retain_all_ids_but_do_not_report_conflict(sample_property):
    events = [event(sample_property, str(i), description={"value": "Same text"}) for i in range(3)]
    text = fold(events)
    assert text.description == "Same text" and not text.description_conflicting
    assert text.description_observation_ids == tuple(sorted(item.sha256 for item in events))


def test_conflicts_withhold_disputed_facts_from_actual_selection(make_merged_property):
    merged = make_merged_property()
    first = event(
        merged.canonical,
        "first",
        description={"value": "Council tax Band C"},
        features={"value": ["Private garden"]},
    )
    other = event(
        merged.canonical,
        "other",
        description={"value": "Council tax Band D"},
        features={"value": ["No garden access"]},
    )
    current = fold([first, other])
    merged = merged.model_copy(update={"source_text": {merged.canonical.source: current}})
    selected = select_listing_text(merged)
    assert selected.policy_version == 2
    assert "Conflicting descriptions" in selected.description
    assert "Conflicting listing features" in selected.features[0]
    assert "Band C" not in selected.description and "Band D" not in selected.description
    assert "Private garden" not in str(selected.features)
    assert selected.source_observation_ids == {
        merged.canonical.source.value: tuple(sorted([first.sha256, other.sha256]))
    }
    assert (
        supported_council_tax_band("C", selected.description, list(selected.features)) == "unknown"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"description_conflicting": True},
        {
            "description_conflicting": True,
            "description": "A selected disputed value",
            "description_observed_at": AT,
            "description_observation_ids": ("a" * 64, "b" * 64),
        },
    ],
)
def test_conflict_requires_dated_reference_ids_and_no_selected_value(changes):
    with pytest.raises(ValidationError, match="text conflict"):
        ListingTextEvidence(**changes)


def test_tied_provenance_is_bounded_without_truncating_a_conflict():
    previous = ListingTextEvidence(
        description="Same text",
        description_observed_at=AT,
        description_origin="detail",
        description_observation_ids=tuple(f"{i:064x}" for i in range(512)),
    )
    incoming = previous.model_copy(update={"description_observation_ids": (f"{512:064x}",)})
    with pytest.raises(ValueError, match="Too many tied"):
        merge_listing_text(previous, incoming)


async def test_receipts_before_source_row_are_recovered_and_clear_survives_save(sample_property):
    detail = event(sample_property, "detail", description={"value": "Earlier detail"})
    clear = event(
        sample_property, "clear", minute=1, origin="correction", description={"state": "cleared"}
    )
    search = event(
        sample_property, "search", minute=2, origin="search", description={"value": "Later summary"}
    )
    prop = sample_property.model_copy(
        update={"description": "Later summary", "source_observation": search}
    )
    async with PropertyStorage(":memory:") as storage:
        await storage.retain_detail_observation(detail)
        await storage.retain_text_correction(clear)
        assert await storage.get_all_known_source_ids() == {}
        await storage.upsert_source_listings([prop])
        await storage.save_property(prop)
        _, loaded, text = await storage.get_property_images_and_row(prop.unique_id)
        assert loaded.description == ""
        assert text[prop.source].description_observation_ids == (clear.sha256,)
        assert text[prop.source].description_origin == "correction"
        assert len(await storage.observations.history(prop.unique_id)) == 3


async def test_sql_projection_survives_all_save_paths_and_preserves_user_history(
    make_merged_property,
):
    merged = make_merged_property()
    first = event(merged.canonical, "first", description={"value": "Band C"})
    other = event(merged.canonical, "other", description={"value": "Band D"})
    async with PropertyStorage(":memory:") as storage:
        await storage.save_merged_property(merged)
        await storage.update_user_status(
            merged.unique_id, UserStatus.INTERESTED, note="Keep my note"
        )
        await storage.save_viewing_message(merged.unique_id, "Keep my message")
        await storage.detect_and_record_price_change(merged.unique_id, merged.min_price - 50)
        await storage.retain_detail_observation(first)
        await storage.retain_detail_observation(other)
        await storage.upsert_source_listings([merged.canonical])
        await storage.update_merged_sources(merged.unique_id, merged)
        await storage.pipeline.save_unenriched_property(merged)
        await storage.pipeline.save_pre_analysis_properties([merged], {})
        loaded = (await storage.pipeline.get_pending_analysis_properties())[0]
        text = loaded.text_by_source[merged.canonical.source]
        projection = await storage.observations.project(merged.unique_id)
        assert text.description_conflicting and loaded.canonical.description == ""
        assert text.description_observation_ids == projection.fields["description"].observation_ids
        assert await storage.get_viewing_message(merged.unique_id) == "Keep my message"
        assert (await storage.get_status_history(merged.unique_id))[0]["note"] == "Keep my note"
        assert len(await storage.get_price_history(merged.unique_id)) == 1
        correction = event(
            merged.canonical,
            "resolved",
            minute=1,
            origin="correction",
            description={"value": "Confirmed Band D"},
        )
        await storage.retain_text_correction(correction)
        current = (await storage.get_current_text([merged.unique_id]))[merged.unique_id]
        assert current[merged.canonical.source].description == "Confirmed Band D"
        assert not current[merged.canonical.source].description_conflicting


def test_in_memory_dedup_keeps_original_search_text_and_features(sample_property):
    capture = event(
        sample_property,
        "search",
        origin="search",
        description={"value": "Original source text"},
        features={"value": ["Gas hob"]},
    )
    prop = sample_property.model_copy(
        update={"description": "Later inferred text", "source_observation": capture}
    )
    merged = Deduplicator().properties_to_merged([prop])[0]
    selected = select_listing_text(merged)
    assert selected.description == "Original source text"
    assert selected.features == ("[OpenRent] Gas hob",)
    assert selected.source_observation_ids == {prop.source.value: (capture.sha256,)}


async def test_text_lookup_bounds_large_batches_without_losing_last_item(make_property):
    properties = [make_property(description=f"Text {index}") for index in range(105)]
    async with PropertyStorage(":memory:") as storage:
        for prop in properties:
            await storage.save_property(prop)
        current = await storage.get_current_text([prop.unique_id for prop in properties])
        assert len(current) == 105
        assert current[properties[-1].unique_id][properties[-1].source].description == "Text 104"
        assert await storage.get_current_text([]) == {}
        with pytest.raises(ValueError, match="500-property"):
            await storage.get_current_text([properties[0].unique_id] * 501)


async def test_missing_ledger_in_current_schema_is_a_failure_not_legacy_fallback(sample_property):
    async with PropertyStorage(":memory:") as storage:
        async with storage._transaction() as conn:
            await conn.execute("DROP TABLE source_observation_fields")
        with pytest.raises(sqlite3.OperationalError, match="source_observation_fields"):
            await storage.save_property(sample_property)
        assert await storage.get_all_known_source_ids() == {}
