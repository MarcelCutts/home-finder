"""Build a blind development annotation pack from verified historical evidence."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import re
import shutil
import sqlite3
import warnings
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from home_finder.db.backup import BackupManifest, verify_backup
from home_finder.models.reference import (
    RUBRIC_VERSION,
    CaseAnnotation,
    ReferenceCase,
    ReferenceFacts,
    ReferenceImage,
    ReferencePackManifest,
    ReferenceText,
)
from home_finder.pipeline.capture import _write_json, _write_private
from home_finder.utils.image_cache import safe_dir_name

_Role = Literal["gallery", "floorplan"]
_MAX_PACK_BYTES = 600_000_000


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass
class _Media:
    path: str
    sha256: str
    size: int
    roles: set[_Role] = field(default_factory=set)


@dataclass
class _Candidate:
    identifier: str
    source: str
    outcode: str
    bedrooms: int
    media: dict[str, _Media] = field(default_factory=dict)
    descriptions: list[tuple[str, str, bool]] = field(default_factory=list)
    group: tuple[str, ...] = ()

    @property
    def strata(self) -> tuple[str, ...]:
        return (
            f"source:{self.source}",
            f"area:{self.outcode}",
            f"bedrooms:{min(self.bedrooms, 3)}",
            "floorplan:" + str(any("floorplan" in m.roles for m in self.media.values())),
            "gallery:"
            + ("sparse" if sum("gallery" in m.roles for m in self.media.values()) < 5 else "5+"),
        )


class _Groups:
    def __init__(self) -> None:
        self.parents: dict[str, str] = {}

    def root(self, key: str) -> str:
        self.parents.setdefault(key, key)
        while self.parents[key] != key:
            self.parents[key] = self.parents[self.parents[key]]
            key = self.parents[key]
        return key

    def join(self, first: str, second: str) -> None:
        left, right = self.root(first), self.root(second)
        self.parents[max(left, right)] = min(left, right)


def _candidates(package: Path, manifest: BackupManifest) -> list[_Candidate]:
    """Never query model output, status, contact data, messages or private notes."""
    groups = _Groups()
    by_directory: dict[str, list[str]] = defaultdict(list)
    for name in manifest.assets:
        by_directory[Path(name).parent.as_posix()].append(name)
    database = package / "properties.db"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        result = {
            key: _Candidate(key, source, (postcode or "unknown").split()[0], bedrooms)
            for key, source, postcode, bedrooms in conn.execute(
                "SELECT unique_id, source, postcode, bedrooms FROM properties ORDER BY unique_id"
            )
        }
        if len(result) > 10_000:
            raise ValueError("Reference curation is bounded to 10,000 canonical rows")
        identities: dict[tuple[str, ...], str] = {}
        for key, source, provider, url, home, description, backfilled in conn.execute(
            "SELECT unique_id, source, source_id, url, merged_id, description, is_backfilled "
            "FROM source_listings WHERE merged_id IS NOT NULL ORDER BY unique_id"
        ):
            if home not in result:
                continue
            groups.join(key, home)
            for kind, value in (("provider", provider), ("url", url)):
                if not value:
                    continue
                identity = (kind, source, value)
                if identity in identities:
                    groups.join(home, identities[identity])
                else:
                    identities[identity] = home
            if description and description.strip():
                result[home].descriptions.append((source, description, bool(backfilled)))
        for alias, anchor in conn.execute("SELECT unique_id, anchor_id FROM source_aliases"):
            groups.join(alias, anchor)
        image_owners: dict[str, str] = {}
        for home, url, role in conn.execute(
            "SELECT property_unique_id, url, image_type FROM property_images ORDER BY id"
        ):
            if home not in result or role not in {"gallery", "floorplan"}:
                continue
            token = hashlib.md5(url.encode()).hexdigest()[:8]
            for name in by_directory[safe_dir_name(home)]:
                if not fnmatch.fnmatchcase(Path(name).name, f"{role}_*_{token}.*"):
                    continue
                saved = manifest.assets[name]
                media = result[home].media.setdefault(
                    saved.sha256, _Media(name, saved.sha256, saved.size)
                )
                media.roles.add(role)
                if saved.sha256 in image_owners:
                    groups.join(home, image_owners[saved.sha256])
                else:
                    image_owners[saved.sha256] = home
        members: dict[str, list[str]] = defaultdict(list)
        for key in result:
            members[groups.root(key)].append(key)
        for key, candidate in result.items():
            candidate.group = tuple(sorted(members[groups.root(key)]))
        return list(result.values())


def _image(package: Path, media: _Media) -> tuple[ReferenceImage, bytes] | None:
    if not 0 < media.size <= 20_000_000:
        return None
    path = package / "image_cache" / media.path
    if path.is_symlink() or path.stat().st_size != media.size:
        raise ValueError("Source media changed after backup verification")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != media.sha256:
        raise ValueError("Source media checksum changed")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(image.format or "")
                if (
                    extension is None
                    or image.width * image.height > 40_000_000
                    or getattr(image, "n_frames", 1) != 1
                ):
                    return None
                image.load()
                evidence = ReferenceImage(
                    sha256=media.sha256,
                    file=f"media/{media.sha256}.{extension}",
                    width=image.width,
                    height=image.height,
                    size=len(body),
                    roles=sorted(media.roles),
                )
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return None
    return evidence, body


def _publish_material(output: Path, filename: str, body: bytes, hashes: dict[str, str]) -> None:
    digest = hashlib.sha256(body).hexdigest()
    if filename in hashes:
        if hashes[filename] != digest:
            raise ValueError("Conflicting material path")
        return
    _write_private(output / filename, body)
    hashes[filename] = digest


def build_pack(
    package: Path, destination: Path, *, max_cases: int = 48, seed: str = "reference-v1"
) -> dict[str, Any]:
    """Curate development cases with blank labels, never a qualified holdout."""
    if not 1 <= max_cases <= 60 or not 1 <= len(seed) <= 100:
        raise ValueError("Pack requires 1..60 cases and a bounded deterministic seed")
    package = package.resolve(strict=True)
    if destination.resolve().is_relative_to(package):
        raise ValueError("Annotation output must not change its backup package")
    backup = verify_backup(package)
    if backup.assets_state == "not_included":
        raise ValueError("A reference pack requires actual retained media")
    candidates = _candidates(package, backup)
    eligible = [candidate for candidate in candidates if candidate.media]
    destination.mkdir(mode=0o700)
    try:
        for name in ("cases", "labels", "media", "text"):
            (destination / name).mkdir(mode=0o700)
        hashes: dict[str, str] = {}
        coverage: Counter[str] = Counter()
        selected: set[tuple[str, ...]] = set()
        cases: list[dict[str, Any]] = []
        origins: dict[str, Any] = {}
        total_bytes = 0
        invalid_media = 0
        while eligible and len(cases) < max_cases:
            candidate = min(
                eligible,
                key=lambda item: (
                    -sum(1 / (1 + coverage[stratum]) for stratum in item.strata),
                    _digest([seed, item.identifier]),
                ),
            )
            eligible.remove(candidate)
            if candidate.group in selected:
                continue
            images: list[ReferenceImage] = []
            for media in sorted(
                candidate.media.values(),
                key=lambda item: ("floorplan" not in item.roles, item.sha256),
            ):
                if len(images) >= 12:
                    break
                loaded = _image(package, media)
                if loaded is None:
                    invalid_media += 1
                    continue
                image, body = loaded
                total_bytes += len(body)
                if total_bytes > _MAX_PACK_BYTES:
                    raise ValueError("Selected material exceeds the 600 MB pack allowance")
                _publish_material(destination, image.file, body, hashes)
                images.append(image)
            if not images:
                continue
            texts: list[ReferenceText] = []
            omitted_texts = 0
            for source, description, backfilled in candidate.descriptions:
                body = description.encode()
                if len(body) > 1_000_000 or len(texts) >= 20:
                    omitted_texts += 1
                    continue
                digest = hashlib.sha256(body).hexdigest()
                text = ReferenceText(
                    sha256=digest,
                    file=f"text/{digest}.txt",
                    source=source,
                    is_backfilled=backfilled,
                    size=len(body),
                )
                total_bytes += len(body)
                if total_bytes > _MAX_PACK_BYTES:
                    raise ValueError("Selected material exceeds the 600 MB pack allowance")
                _publish_material(destination, text.file, body, hashes)
                texts.append(text)
            case_id = "case-" + _digest([1, candidate.identifier, candidate.group])[:16]
            case = ReferenceCase(
                case_id=case_id,
                group_sha256=_digest(candidate.group),
                connected_canonical_count=len(candidate.group),
                images=images,
                texts=texts,
                omitted_image_candidates=len(candidate.media) - len(images),
                omitted_descriptions=omitted_texts,
            )
            filename = f"cases/{case_id}.json"
            _write_json(destination / filename, case.model_dump(mode="json"))
            hashes[filename] = _sha256(destination / filename)
            label = CaseAnnotation(case_id=case_id, input_sha256=hashes[filename])
            _write_json(destination / "labels" / f"{case_id}.json", label.model_dump(mode="json"))
            cases.append({"case_id": case_id, "file": filename, "sha256": hashes[filename]})
            origins[case_id] = {
                "canonical_id": candidate.identifier,
                "connected_canonical_ids": candidate.group,
                "curation_strata": candidate.strata,
                "media_origins": {m.sha256: m.path for m in candidate.media.values()},
            }
            selected.add(candidate.group)
            coverage.update(candidate.strata)
        if not cases:
            raise ValueError("No decodable reference media is available")
        rubric = files("home_finder.data").joinpath("reference_rubric_v1.md").read_bytes()
        _publish_material(destination, "rubric.md", rubric, hashes)
        _write_json(destination / "origins.json", origins)
        hashes["origins.json"] = _sha256(destination / "origins.json")
        manifest: dict[str, Any] = {
            "version": 1,
            "rubric_version": RUBRIC_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "corpus_kind": "historical_projection_with_retained_assets",
            "split": "development",
            "promotion_ready": False,
            "human_labels_assigned": 0,
            "model_calls": 0,
            "network_requests": 0,
            "backup_database_sha256": backup.database.sha256,
            "selection_seed": seed,
            "selection_policy": "greedy_stratum_coverage_one_canonical_per_connected_group_v1",
            "requested_cases": max_cases,
            "canonical_rows": len(candidates),
            "candidate_groups_with_media": len({c.group for c in candidates if c.media}),
            "selected_strata": dict(sorted(coverage.items())),
            "selected_material_bytes": total_bytes,
            "undecodable_or_unsupported_media": invalid_media,
            "cases": cases,
            "input_files": hashes,
        }
        from home_finder.pipeline.reference_render import render_pack

        render_pack(destination, manifest)
        for path in [destination / "index.html", *sorted((destination / "cases").glob("*.html"))]:
            hashes[path.relative_to(destination).as_posix()] = _sha256(path)
        if _sha256(package / "properties.db") != backup.database.sha256:
            raise ValueError("Source database changed while preparing the pack")
        _write_json(destination / "manifest.json", manifest)
        return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def _bounded_bytes(path: Path, limit: int = 4_000_000) -> bytes:
    if path.is_symlink() or not 0 < path.stat().st_size <= limit:
        raise ValueError("Reference metadata must be a bounded regular file")
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Reference metadata exceeds its size limit")
    return body


def validate_pack(
    package: Path, *, labels: Path | None = None, require_reviewed: bool = False
) -> dict[str, Any]:
    """Verify retained bytes and human declaration syntax; never certify label truth."""
    package = package.resolve(strict=True)
    manifest = ReferencePackManifest.model_validate_json(_bounded_bytes(package / "manifest.json"))
    for filename, expected in manifest.input_files.items():
        relative = Path(filename)
        if (
            relative.is_absolute()
            or relative.as_posix() != filename
            or ".." in relative.parts
            or "\\" in filename
            or "\x00" in filename
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
        ):
            raise ValueError("Unsafe pack input path")
        path = package / relative
        if path.is_symlink() or not path.resolve().is_relative_to(package):
            raise ValueError("Pack input escapes its namespace")
        if not path.is_file() or path.stat().st_size > 50_000_000:
            raise ValueError("Pack input is not a bounded regular file")
        if _sha256(path) != expected:
            raise ValueError("Reference input checksum mismatch")
    if not {"index.html", "origins.json", "rubric.md"}.issubset(manifest.input_files):
        raise ValueError("Reference rubric and origin manifest are required")
    labels = labels or package / "labels"
    states: Counter[str] = Counter()
    reviewed_cases = 0
    group_ids: set[str] = set()
    image_groups: dict[str, str] = {}
    case_ids: set[str] = set()
    for entry in manifest.cases:
        filename = entry.file
        if manifest.input_files.get(filename) != entry.sha256:
            raise ValueError("Case checksum is not in the retained input manifest")
        case = ReferenceCase.model_validate_json(_bounded_bytes(package / filename))
        if (
            entry.case_id != case.case_id
            or case.case_id in case_ids
            or case.group_sha256 in group_ids
        ):
            raise ValueError("Duplicate group or mismatched case identity")
        case_ids.add(case.case_id)
        group_ids.add(case.group_sha256)
        material: list[ReferenceImage | ReferenceText] = [*case.images, *case.texts]
        for item in material:
            if manifest.input_files.get(item.file) != item.sha256:
                raise ValueError("Material checksum is not in the retained input manifest")
            if (package / item.file).stat().st_size != item.size:
                raise ValueError("Material size differs from its reference")
        for image in case.images:
            if image.sha256 in image_groups:
                raise ValueError("Identical images occur across selected groups")
            image_groups[image.sha256] = case.group_sha256
        annotation = CaseAnnotation.model_validate_json(
            _bounded_bytes(labels / f"{case.case_id}.json")
        )
        if annotation.case_id != case.case_id or annotation.input_sha256 != entry.sha256:
            raise ValueError("Annotation refers to different input bytes")
        annotation.validate_material(case.material_ids, require_reviewed=require_reviewed)
        text_bodies = {
            f"text:{item.sha256}": (package / item.file).read_text() for item in case.texts
        }
        case_states = []
        for name in ReferenceFacts.model_fields:
            fact = getattr(annotation.facts, name)
            states[f"{name}:{fact.state}"] += 1
            case_states.append(fact.state)
            for citation in fact.evidence:
                if (
                    citation.material_id in text_bodies
                    and citation.quote is not None
                    and (citation.quote not in text_bodies[citation.material_id])
                ):
                    raise ValueError("Quoted text is not present in the cited material")
        reviewed_cases += int(all(state != "unreviewed" for state in case_states))
    return {
        "cases": len(manifest.cases),
        "fully_reviewed_cases": reviewed_cases,
        "fact_states": dict(sorted(states.items())),
        "split": "development",
        "promotion_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    build = commands.add_parser("build")
    build.add_argument("--backup", type=Path, required=True)
    build.add_argument("--destination", type=Path, required=True)
    build.add_argument("--max-cases", type=int, default=48)
    build.add_argument("--seed", default="reference-v1")
    validate = commands.add_parser("validate")
    validate.add_argument("--pack", type=Path, required=True)
    validate.add_argument("--labels", type=Path)
    validate.add_argument("--require-reviewed", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "build":
            build_pack(args.backup, args.destination, max_cases=args.max_cases, seed=args.seed)
            report = validate_pack(args.destination)
        else:
            report = validate_pack(
                args.pack, labels=args.labels, require_reviewed=args.require_reviewed
            )
    except ValidationError:
        parser.exit(
            1, "Invalid reference case or annotation; check its schema and evidence state\n"
        )
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Reference operation failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
