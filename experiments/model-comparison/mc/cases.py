"""Frozen case pack: homes from the 2026-10-10 operating trial, loaded as the pipeline does."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from home_finder.db import PropertyStorage
from home_finder.db.row_mappers import row_to_merged_property
from home_finder.models import MergedProperty, PropertyImage

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshot"
# The analyser reads `{data_dir}/image_cache`; the working copy holds a migrated DB
# beside a symlink to the read-only image snapshot.
WORK_DIR = ROOT / "data" / "work"


@dataclass(frozen=True)
class Case:
    case_id: str
    merged: MergedProperty
    gallery_count: int
    has_floorplan: bool
    source: str


async def prepare_work_dir() -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    db = WORK_DIR / "properties.db"
    if not db.exists():
        shutil.copy2(SNAPSHOT_DIR / "properties.db", db)
        storage = PropertyStorage(str(db))
        await storage.initialize()
        await storage.close()
    link = WORK_DIR / "image_cache"
    if not link.exists():
        link.symlink_to(SNAPSHOT_DIR / "image_cache")
    return WORK_DIR


async def load_cases() -> list[Case]:
    work = await prepare_work_dir()
    cases: list[Case] = []
    async with aiosqlite.connect(work / "properties.db") as conn:
        conn.row_factory = aiosqlite.Row

        async def images(property_id: str) -> list[PropertyImage]:
            async with conn.execute(
                """SELECT source,url,image_type,floorplan_origin FROM property_images
                   WHERE property_unique_id=? ORDER BY image_type,id""",
                (property_id,),
            ) as cur:
                return [PropertyImage.model_validate(dict(r)) for r in await cur.fetchall()]

        async with conn.execute("SELECT * FROM properties ORDER BY unique_id") as cur:
            rows = await cur.fetchall()
        for row in rows:
            merged = await row_to_merged_property(row, get_property_images=images)
            gallery = [i for i in merged.images if i.image_type == "gallery"]
            if not gallery:
                continue
            cases.append(
                Case(
                    case_id="c" + hashlib.sha256(merged.unique_id.encode()).hexdigest()[:10],
                    merged=merged,
                    gallery_count=len(gallery),
                    has_floorplan=merged.floorplan is not None,
                    source=merged.canonical.source.value,
                )
            )
    return cases


def select(cases: list[Case], n: int, *, seed: int = 20261021) -> list[Case]:
    """Deterministic sample stratified by floorplan presence and source."""
    rng = random.Random(seed)
    strata: dict[tuple[bool, str], list[Case]] = {}
    for case in cases:
        strata.setdefault((case.has_floorplan, case.source), []).append(case)
    for members in strata.values():
        rng.shuffle(members)
    ordered: list[Case] = []
    # Round-robin across strata so any prefix of the list stays balanced.
    while any(strata.values()):
        for key in sorted(strata, key=lambda k: (-len(strata[k]), k)):
            if strata[key]:
                ordered.append(strata[key].pop())
    return ordered[:n]


def write_manifest(path: Path, cases: list[Case]) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "case_id": c.case_id,
                    "unique_id": c.merged.unique_id,
                    "source": c.source,
                    "bedrooms": c.merged.canonical.bedrooms,
                    "price_pcm": c.merged.canonical.price_pcm,
                    "gallery_count": c.gallery_count,
                    "has_floorplan": c.has_floorplan,
                }
                for c in cases
            ],
            indent=2,
        )
    )
