"""Persist current listing text within the caller's owned transaction."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import aiosqlite

from home_finder.models import ListingTextEvidence, MergedProperty, PropertySource
from home_finder.models.listing_text import merge_listing_text
from home_finder.models.observations import MAX_BATCH_BYTES


def decode_source_text(value: str | None) -> dict[PropertySource, ListingTextEvidence]:
    if not value:
        return {}
    return {
        PropertySource(source): ListingTextEvidence.model_validate(evidence)
        for source, evidence in json.loads(value).items()
    }


def text_from_property_row(
    row: Mapping[str, Any] | aiosqlite.Row,
) -> dict[PropertySource, ListingTextEvidence]:
    """Recover saved evidence and undated legacy fields without inventing dates."""
    result = decode_source_text(row["source_text_json"])
    descriptions = json.loads(row["descriptions_json"] or "{}")
    if row["description"] and row["source"] not in descriptions:
        descriptions[row["source"]] = row["description"]
    for source, description in descriptions.items():
        key = PropertySource(source)
        result[key] = merge_listing_text(
            result.get(key, ListingTextEvidence()), ListingTextEvidence(description=description)
        )
    return result


def text_from_source_row(row: aiosqlite.Row) -> ListingTextEvidence:
    evidence = (
        ListingTextEvidence.model_validate_json(row["text_json"])
        if row["text_json"]
        else ListingTextEvidence()
    )
    return merge_listing_text(evidence, ListingTextEvidence(description=row["description"]))


async def _captured_text(conn: aiosqlite.Connection, unique_id: str) -> ListingTextEvidence:
    """Fold all winning captured fields, including receipts predating a source row.

    Rank identically to SourceProjection while keeping exact original origins.
    This uses the immutable field index in the caller's owned transaction.
    """
    try:
        cursor = await conn.execute(
            """WITH ranked AS (
                SELECT *, DENSE_RANK() OVER (
                    PARTITION BY field_name ORDER BY authority DESC, observed_at DESC
                ) AS rank FROM source_observation_fields
                WHERE source_unique_id = ? AND field_name IN ('description', 'features')
            ) SELECT f.*, o.origin FROM ranked f
              JOIN source_observations o ON o.observation_id = f.observation_id
              WHERE rank = 1 ORDER BY field_name, observation_id LIMIT 513""",
            (unique_id,),
        )
    except aiosqlite.OperationalError as exc:
        if str(exc) not in {
            "no such table: source_observation_fields",
            "no such table: source_observations",
        }:
            raise
        async with conn.execute("PRAGMA user_version") as version_cursor:
            version = await version_cursor.fetchone()
        if version is not None and version[0] < 10:
            return ListingTextEvidence()  # Explicit historical migration construction.
        raise  # Missing evidence tables in a current database are corruption, not absence.
    async with cursor:
        result = ListingTextEvidence()
        size = count = 0
        async for row in cursor:
            count += 1
            size += len(row["value_json"].encode())
            if count > 512 or size > MAX_BATCH_BYTES:
                raise ValueError("Too many tied text observations; adjudication is required")
            name = row["field_name"]
            value = json.loads(row["value_json"])
            if row["field_state"] == "cleared":
                value = "" if name == "description" else ()
            evidence = ListingTextEvidence.model_validate(
                {
                    name: value,
                    f"{name}_observed_at": datetime.fromisoformat(row["observed_at"]),
                    f"{name}_origin": row["origin"],
                    f"{name}_observation_ids": (row["observation_id"],),
                }
            )
            result = merge_listing_text(result, evidence)
        return result


async def apply_source_text(
    conn: aiosqlite.Connection, unique_id: str, incoming: ListingTextEvidence
) -> str | None:
    """Update a known provider row; return its existing canonical link."""
    cursor = await conn.execute(
        "SELECT description, text_json, merged_id FROM source_listings WHERE unique_id = ?",
        (unique_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    result = merge_listing_text(text_from_source_row(row), incoming)
    result = merge_listing_text(result, await _captured_text(conn, unique_id))
    await conn.execute(
        "UPDATE source_listings SET description = ?, text_json = ? WHERE unique_id = ?",
        (result.description, result.model_dump_json(), unique_id),
    )
    return row["merged_id"]  # type: ignore[no-any-return]


async def refresh_merged_text(
    conn: aiosqlite.Connection,
    unique_id: str,
    incoming: dict[PropertySource, ListingTextEvidence] | None = None,
) -> None:
    """Refresh text projections without erasing richer or explicitly cleared fields."""
    cursor = await conn.execute("SELECT * FROM properties WHERE unique_id = ?", (unique_id,))
    row = await cursor.fetchone()
    if row is None:
        return
    result = text_from_property_row(row)
    for source, evidence in (incoming or {}).items():
        result[source] = merge_listing_text(result.get(source, ListingTextEvidence()), evidence)
    cursor = await conn.execute(
        "SELECT * FROM source_listings WHERE merged_id = ? ORDER BY last_seen, unique_id",
        (unique_id,),
    )
    for source_row in await cursor.fetchall():
        source = PropertySource(source_row["source"])
        result[source] = merge_listing_text(
            result.get(source, ListingTextEvidence()), text_from_source_row(source_row)
        )
    descriptions = {
        source.value: value.description
        for source, value in result.items()
        if value.description is not None
    }
    canonical = result.get(PropertySource(row["source"]), ListingTextEvidence())
    await conn.execute(
        "UPDATE properties SET description = ?, descriptions_json = ?, source_text_json = ? "
        "WHERE unique_id = ?",
        (
            canonical.description if canonical.description is not None else row["description"],
            json.dumps(descriptions, ensure_ascii=False) if descriptions else None,
            json.dumps(
                {source.value: value.model_dump(mode="json") for source, value in result.items()},
                ensure_ascii=False,
            ),
            unique_id,
        ),
    )


async def persist_merged_text(
    conn: aiosqlite.Connection, merged: MergedProperty, *, unique_id: str | None = None
) -> None:
    """Write each source's text to matching rows, then rebuild its projection.

    Never invent the non-canonical provider ID or move another home's rows.
    Missing source rows retain the evidence in the canonical projection until
    provider identity is resolved by the later observation/identity work.
    """
    canonical_id = unique_id or merged.unique_id
    incoming = merged.text_by_source
    for source, evidence in incoming.items():
        url = merged.source_urls.get(source)
        if url is None:
            continue
        cursor = await conn.execute(
            "SELECT unique_id FROM source_listings WHERE source = ? AND url = ? "
            "AND (merged_id = ? OR merged_id IS NULL)",
            (source.value, str(url), canonical_id),
        )
        for row in await cursor.fetchall():
            await apply_source_text(conn, row["unique_id"], evidence)
    await refresh_merged_text(conn, canonical_id, incoming)
