"""Replay retained batch listings through same-run and cross-run dedup, offline.

Requires development dependencies (pytest-socket). Never opens the input database
for writing. Reconstructs input from retained source rows/detail observations;
this is a development replay, not the original in-memory live input or a label set.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from pytest_socket import disable_socket

from home_finder.db import PropertyStorage
from home_finder.db.listing_text import text_from_source_row
from home_finder.db.row_mappers import row_to_property
from home_finder.filters.deduplication import Deduplicator
from home_finder.logging import configure_logging
from home_finder.models import MergedProperty, PropertyImage, PropertySource
from home_finder.pipeline.stages import _cross_run_deduplicate
from home_finder.utils.image_cache import find_cached_file, get_cache_dir
from home_finder.utils.image_hash import count_gallery_hash_matches, hash_from_disk


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(source: Path) -> tuple[list[MergedProperty], dict[str, list[Path]]]:
    events = [json.loads(line) for line in (source / "events.jsonl").read_text().splitlines()]
    selected = next(e["property_ids"] for e in events if e["event"] == "scraping_selection")
    inputs = []
    files: dict[str, list[Path]] = {}
    with closing(sqlite3.connect(f"file:{source}/properties.db?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for uid in selected:
            row = conn.execute("SELECT * FROM source_listings WHERE unique_id=?", (uid,)).fetchone()
            prop = row_to_property(row)
            detail = conn.execute(
                "SELECT payload_json FROM source_observations "
                "WHERE source_unique_id=? AND origin='detail' ORDER BY observed_at DESC LIMIT 1",
                (uid,),
            ).fetchone()
            if detail is None:
                raise ValueError(f"No retained detail for {uid}")
            fields = json.loads(detail[0])["fields"]
            images = []
            floorplans = []
            files[uid] = []
            for kind, target in (("gallery", images), ("floorplan", floorplans)):
                for url in fields.get(f"{kind}_urls", {}).get("value", []):
                    cached = find_cached_file(str(source), uid, url, kind)
                    if cached is not None:
                        files[uid].append(cached)
                        target.append(PropertyImage(url=url, source=prop.source, image_type=kind))
            if not images:
                raise ValueError(f"No cached gallery for {uid}; refusing network fallback")
            inputs.append(
                MergedProperty(
                    canonical=prop,
                    sources=(prop.source,),
                    source_urls={prop.source: prop.url},
                    images=tuple(images),
                    floorplan=floorplans[0] if floorplans else None,
                    min_price=prop.price_pcm,
                    max_price=prop.price_pcm,
                    descriptions={prop.source: prop.description} if prop.description else {},
                    source_text={prop.source: text_from_source_row(row)},
                )
            )
    return inputs, files


async def replay(source: Path, output: Path) -> dict:
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    inputs, files = load_inputs(source)
    original_files = [source / name for name in ("properties.db", "events.jsonl", "report.json")]
    original_files += sorted({path for paths in files.values() for path in paths})
    before = {str(path): digest(path) for path in original_files}
    (output / "inputs.json").write_text(json.dumps([m.model_dump(mode="json") for m in inputs]))
    report = {
        "input_count": len(inputs),
        "cases": {},
        "input_sha256": digest(output / "inputs.json"),
    }
    for scenario in ("same_run", "split_run"):
        root = output / scenario
        root.mkdir(mode=0o700)
        for uid, paths in files.items():
            cache = get_cache_dir(str(root), uid)
            cache.mkdir(parents=True)
            for path in paths:
                shutil.copy2(path, cache / path.name)
        async with PropertyStorage(str(root / "properties.db")) as storage:
            if scenario == "split_run":
                first = [m for m in inputs if m.canonical.source == PropertySource.RIGHTMOVE]
                incoming = [m for m in inputs if m.canonical.source != PropertySource.RIGHTMOVE]
                await storage.upsert_source_listings([m.canonical for m in first])
                await storage.pipeline.save_pre_analysis_properties(first, {})
            else:
                incoming = inputs
            result = await _cross_run_deduplicate(
                Deduplicator(
                    enable_cross_platform=True, enable_image_hashing=True, data_dir=str(root)
                ),
                incoming,
                storage,
                set(),
                data_dir=str(root),
            )
            await storage.pipeline.save_pre_analysis_properties(result.genuinely_new, {})
            known_replay = await storage.filter_new_merged(inputs)
            async with storage._read_connection() as conn:
                groups = {}
                async with conn.execute(
                    "SELECT merged_id, unique_id FROM source_listings ORDER BY unique_id"
                ) as cursor:
                    for owner, uid in await cursor.fetchall():
                        groups.setdefault(owner, []).append(uid)
                async with conn.execute("PRAGMA foreign_key_check") as cursor:
                    fk_errors = len(await cursor.fetchall())
                async with conn.execute("PRAGMA integrity_check") as cursor:
                    integrity = [row[0] for row in await cursor.fetchall()]
            report["cases"][scenario] = {
                "anchors_updated": result.anchors_updated,
                "genuinely_new": len(result.genuinely_new),
                "groups": groups,
                "repeat_known_new_count": len(known_replay),
                "spending": await storage.spend.report(),
                "foreign_key_errors": fk_errors,
                "integrity": integrity,
            }
    # Review hash distances from original per-source files, never from caches
    # augmented by a previous merge (which would make the comparison circular).
    hashes = {
        uid: [
            h
            for path in paths
            if path.name.startswith("gallery_")
            if (h := hash_from_disk(path)) is not None
        ]
        for uid, paths in files.items()
    }
    events = [json.loads(line) for line in (source / "events.jsonl").read_text().splitlines()]
    pairs = [
        e
        for e in events
        if e["event"] == "deduplication_pair_scored" and e["reason"] != "below_match_threshold"
    ]
    report["pair_review"] = [
        {
            "ids": [e["prop1"], e["prop2"]],
            "live_reason": e["reason"],
            "score": e["score"],
            "minimum_phash_distance": min(
                (int(a, 16) ^ int(b, 16)).bit_count()
                for a in hashes[e["prop1"]]
                for b in hashes[e["prop2"]]
            ),
        }
        for e in pairs
    ]
    source_ids = list(hashes)
    report["same_source_photo_overlap"] = [
        {"ids": [left, right], "matching_gallery_images": matches}
        for index, left in enumerate(source_ids)
        for right in source_ids[index + 1 :]
        if left.split(":", 1)[0] == right.split(":", 1)[0]
        if (matches := count_gallery_hash_matches(hashes[left], hashes[right])) >= 2
    ]
    live_groups: dict[str, list[str]] = {}
    for entry in json.loads((source / "report.json").read_text())["source_membership"]:
        if entry["merged_id"] is not None:
            live_groups.setdefault(entry["merged_id"], []).append(entry["unique_id"])
    report["checks"] = {
        "same_and_split_match_live_membership": all(
            {key: sorted(ids) for key, ids in case["groups"].items()}
            == {key: sorted(ids) for key, ids in live_groups.items()}
            for case in report["cases"].values()
        ),
        "known_replay_produces_no_new_work": all(
            case["repeat_known_new_count"] == 0 for case in report["cases"].values()
        ),
        "no_model_attempts": all(
            case["spending"]["attempts"] == 0 for case in report["cases"].values()
        ),
        "database_integrity": all(
            case["integrity"] == ["ok"] and case["foreign_key_errors"] == 0
            for case in report["cases"].values()
        ),
    }
    report["original_artifacts_unchanged"] = all(digest(Path(p)) == h for p, h in before.items())
    report["checks"]["original_artifacts_unchanged"] = report["original_artifacts_unchanged"]
    report["original_sha256"] = before
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    disable_socket(allow_unix_socket=True)
    configure_logging(json_output=True, level=logging.DEBUG)
    result = asyncio.run(replay(args.input.resolve(), args.output.resolve()))
    print(
        json.dumps(
            {
                "input_count": result["input_count"],
                "original_artifacts_unchanged": result["original_artifacts_unchanged"],
                "report": str(args.output / "report.json"),
            }
        )
    )
    if not all(result["checks"].values()):
        raise SystemExit("Offline replay failed its operating checks; inspect report.json")


if __name__ == "__main__":
    main()
