"""Read-only export of historical projections, never a fresh source capture."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from home_finder.pipeline.capture import _write_json
from home_finder.pipeline.runner import runner_lock

# Explicit allowlists keep user notes, contact details and future private fields out.
_PROPERTY_FIELDS = [
    "unique_id",
    "source",
    "source_id",
    "url",
    "title",
    "price_pcm",
    "bedrooms",
    "address",
    "postcode",
    "latitude",
    "longitude",
    "description",
    "image_url",
    "available_from",
    "first_seen",
    "min_price",
    "max_price",
    "sources",
    "source_urls",
    "descriptions_json",
    "source_text_json",
    "notification_status",
    "enrichment_status",
    "enrichment_attempts",
    "analysis_attempts",
    "floor_area_sqm",
    "floor_area_source",
]
_SOURCE_FIELDS = [
    "unique_id",
    "source",
    "source_id",
    "url",
    "title",
    "price_pcm",
    "bedrooms",
    "address",
    "postcode",
    "latitude",
    "longitude",
    "description",
    "image_url",
    "available_from",
    "first_seen",
    "last_seen",
    "merged_id",
    "is_backfilled",
    "created_at",
    "is_off_market",
    "off_market_since",
    "off_market_reason",
    "last_checked_at",
    "text_json",
]
_QUALITY_FIELDS = [
    "property_unique_id",
    "analysis_json",
    "overall_rating",
    "condition_concerns",
    "concern_severity",
    "created_at",
    "epc_rating",
    "has_outdoor_space",
    "red_flag_count",
    "fit_score",
    "fit_score_version",
]
_IMAGE_FIELDS = ["property_unique_id", "source", "url", "image_type", "created_at"]


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _columns(connection: sqlite3.Connection, table: str, allowed: list[str]) -> str:
    available = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
    columns = [f'"{name}"' for name in allowed if name in available]
    if not columns:
        raise ValueError(f"Snapshot does not contain the expected {table} projection")
    return ", ".join(columns)


def export_snapshot(snapshot: Path, output_root: Path, *, max_cases: int = 24) -> Path:
    """Export a bounded development sample from a supplied frozen SQLite copy.

    This must be a quiescent copy, not the live database. Immutable mode deliberately
    ignores locking and journals; a nonempty WAL is refused, and before/after hashes
    detect changes. No storage initialisation, migration or application settings run.
    """
    if not 1 <= max_cases <= 100:
        raise ValueError("Historical exports require 1..100 cases")
    snapshot = snapshot.resolve(strict=True)
    wal = snapshot.with_name(snapshot.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Use a frozen, checkpointed snapshot without a nonempty WAL")
    before = _sha256(snapshot)
    root = output_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with runner_lock(str(root / "capture-authority")) as acquired:
        if not acquired:
            raise RuntimeError("A capture is already running in this output namespace")
        run_dir = root / ("historical-" + uuid4().hex)
        run_dir.mkdir(mode=0o700)
        manifest_path = run_dir / "manifest.json"
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "mode": "historical_projection",
            "state": "running",
            "snapshot_sha256": before,
            "snapshot_filename": snapshot.name,
            "exported_at": datetime.now(UTC).isoformat(),
            "selection": "first_seen then unique_id; development only, not representative/holdout",
            "max_cases": max_cases,
            "source_response_evidence": False,
            "image_bytes_retained": False,
            "live_requests_reserved": 0,
            "model_calls": 0,
            "commute_calls": 0,
            "notifications": 0,
            "cases": [],
        }
        _write_json(manifest_path, manifest)
        try:
            with closing(
                sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                fields = {
                    "properties": _columns(connection, "properties", _PROPERTY_FIELDS),
                    "source_listings": _columns(connection, "source_listings", _SOURCE_FIELDS),
                    "quality_analyses": _columns(connection, "quality_analyses", _QUALITY_FIELDS),
                    "property_images": _columns(connection, "property_images", _IMAGE_FIELDS),
                }
                manifest["table_counts"] = {
                    table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in fields
                }
                properties = connection.execute(
                    f"SELECT {fields['properties']} FROM properties "
                    "ORDER BY first_seen, unique_id LIMIT ?",
                    (max_cases,),
                ).fetchall()
                for index, row in enumerate(properties):
                    unique_id = row["unique_id"]
                    case: dict[str, Any] = {
                        "historical_property_projection": dict(row),
                        "human_labels": None,
                    }
                    for table, key, foreign_key, order in (
                        ("source_listings", "source_listing_projections", "merged_id", "unique_id"),
                        (
                            "quality_analyses",
                            "unverified_model_output",
                            "property_unique_id",
                            "property_unique_id",
                        ),
                        ("property_images", "image_references_only", "property_unique_id", "id"),
                    ):
                        case[key] = [
                            dict(item)
                            for item in connection.execute(
                                f'SELECT {fields[table]} FROM "{table}" '
                                f'WHERE "{foreign_key}" = ? ORDER BY "{order}"',
                                (unique_id,),
                            )
                        ]
                    filename = f"{index:03d}.json"
                    _write_json(run_dir / filename, case)
                    manifest["cases"].append(
                        {
                            "file": filename,
                            "unique_id": unique_id,
                            "sha256": _sha256(run_dir / filename),
                        }
                    )
                if _sha256(snapshot) != before or (wal.exists() and wal.stat().st_size):
                    raise RuntimeError("Snapshot changed during export; discard this sample")
                manifest["state"] = "completed"
        except BaseException:
            manifest["state"] = "failed"
            raise
        finally:
            _write_json(manifest_path, manifest)
        return run_dir
