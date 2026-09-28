"""Blind curation, identity grouping and evidence-bound human review files."""

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from PIL import Image
from pydantic import ValidationError

from home_finder.db.backup import create_backup
from home_finder.models.reference import CaseAnnotation
from home_finder.pipeline import reference_pack
from home_finder.pipeline.reference_pack import build_pack, validate_pack
from home_finder.utils.image_cache import safe_dir_name


def make_backup(root):
    database, assets = root / "fixture.db", root / "images"
    assets.mkdir()
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript("""
            CREATE TABLE properties (
                unique_id TEXT PRIMARY KEY, source TEXT, postcode TEXT, bedrooms INTEGER,
                user_status TEXT, model_answer TEXT
            );
            CREATE TABLE source_listings (
                unique_id TEXT PRIMARY KEY, source TEXT, source_id TEXT, url TEXT,
                merged_id TEXT, description TEXT, is_backfilled INTEGER
            );
            CREATE TABLE source_aliases (unique_id TEXT PRIMARY KEY, anchor_id TEXT);
            CREATE TABLE property_images (
                id INTEGER PRIMARY KEY, property_unique_id TEXT, url TEXT, image_type TEXT
            );
            CREATE TABLE viewing_messages (body TEXT);
            INSERT INTO viewing_messages VALUES ('PRIVATE VIEWING NOTE');
            INSERT INTO source_aliases VALUES ('rightmove:b', 'rightmove:a');
        """)
        image_bodies = {}
        for number, letter in enumerate("abcdefghijk", 1):
            key = f"rightmove:{letter}"
            conn.execute(
                "INSERT INTO properties VALUES (?,?,?,?,?,?)",
                (key, "rightmove", f"N{number} 1AA", number % 4, "offered", "MODEL ANSWER KEY"),
            )
            provider = "e" if letter == "f" else letter
            url = "https://example.test/" + ("g" if letter == "h" else letter)
            description = (
                "EPC band D. Pets are not permitted. <script>window.injected=true;</script>"
                if letter == "k"
                else None
            )
            conn.execute(
                "INSERT INTO source_listings VALUES (?,?,?,?,?,?,?)",
                (key, "rightmove", provider, url, key, description, int(letter == "k")),
            )
            buffer = io.BytesIO()
            Image.new("RGB", (80, 60), (number * 20, 60, 200)).save(buffer, format="PNG")
            body = buffer.getvalue()
            if letter == "d":
                body = image_bodies["c"]  # Same physical bytes, independent provider identity.
            elif letter == "i":
                body = b"not actually an image"
            elif letter == "j":
                buffer = io.BytesIO()
                Image.new("RGB", (40, 30), "black").save(buffer, format="GIF")
                body = buffer.getvalue()
            image_bodies[letter] = body
            image_url = f"https://example.test/{letter}.jpg"
            role = "floorplan" if number % 2 else "gallery"
            conn.execute(
                "INSERT INTO property_images VALUES (?,?,?,?)", (number, key, image_url, role)
            )
            directory = assets / safe_dir_name(key)
            directory.mkdir()
            token = hashlib.md5(image_url.encode()).hexdigest()[:8]
            (directory / f"{role}_0_{token}.jpg").write_bytes(body)
        conn.commit()
    package = root / "backup"
    create_backup(database, package, assets=assets)
    return package


@pytest.fixture
def reference_backup(tmp_path):
    return make_backup(tmp_path)


@pytest.fixture
def pack(reference_backup, tmp_path):
    destination = tmp_path / "pack"
    manifest = build_pack(reference_backup, destination, max_cases=60)
    return destination, manifest


def test_blind_pack_groups_aliases_provider_ids_urls_and_identical_bytes(pack, reference_backup):
    destination, manifest = pack
    assert len(manifest["cases"]) == 5
    origins = json.loads((destination / "origins.json").read_text())
    groups = {tuple(origin["connected_canonical_ids"]) for origin in origins.values()}
    assert groups == {
        ("rightmove:a", "rightmove:b"),
        ("rightmove:c", "rightmove:d"),
        ("rightmove:e", "rightmove:f"),
        ("rightmove:g", "rightmove:h"),
        ("rightmove:k",),
    }
    assert manifest["undecodable_or_unsupported_media"] == 2
    assert not manifest["promotion_ready"] and manifest["split"] == "development"
    assert manifest["human_labels_assigned"] == manifest["model_calls"] == 0
    for path in destination.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        if path.is_file() and path.suffix not in {".png", ".jpg", ".webp"}:
            text = path.read_text()
            assert "MODEL ANSWER KEY" not in text and "PRIVATE VIEWING NOTE" not in text
            assert "offered" not in text
    report = validate_pack(destination)
    assert report["fully_reviewed_cases"] == 0
    assert all(key.endswith(":unreviewed") for key in report["fact_states"])
    with pytest.raises(ValueError, match="Unreviewed"):
        validate_pack(destination, require_reviewed=True)
    assert (
        reference_pack._sha256(reference_backup / "properties.db")
        == manifest["backup_database_sha256"]
    )


def test_retains_exact_bytes_and_escapes_source_html(pack, reference_backup):
    destination, manifest = pack
    origins = json.loads((destination / "origins.json").read_text())
    case_id = next(key for key, value in origins.items() if value["canonical_id"] == "rightmove:k")
    case = json.loads((destination / "cases" / f"{case_id}.json").read_text())
    assert case["texts"][0]["is_backfilled"]
    assert case["images"][0]["file"].endswith(".png")  # Decode bytes, don't trust suffix.
    for item in case["images"]:
        original = (
            reference_backup / "image_cache" / origins[case_id]["media_origins"][item["sha256"]]
        )
        assert (destination / item["file"]).read_bytes() == original.read_bytes()
    html = (destination / "cases" / f"{case_id}.html").read_text()
    assert "<script>window.injected=true;</script>" not in html
    assert "&lt;script&gt;window.injected=true;&lt;/script&gt;" in html
    assert "connect-src 'none'" in html
    assert "https://example.test" not in html
    assert "No source description was retained" in next(
        (destination / "cases" / f"{entry['case_id']}.html").read_text()
        for entry in manifest["cases"]
        if entry["case_id"] != case_id
    )


def test_selection_is_deterministic_and_never_overwrites(reference_backup, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    left = build_pack(reference_backup, first, max_cases=3, seed="fixed")
    right = build_pack(reference_backup, second, max_cases=3, seed="fixed")
    assert left["cases"] == right["cases"] and len(left["cases"]) == 3
    before = (first / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        build_pack(reference_backup, first)
    assert (first / "manifest.json").read_bytes() == before
    with pytest.raises(ValueError, match="must not change"):
        build_pack(reference_backup, reference_backup / "output")


@pytest.mark.parametrize("kwargs", [{"max_cases": 0}, {"max_cases": 61}, {"seed": ""}])
def test_case_count_and_seed_are_bounded(reference_backup, tmp_path, kwargs):
    with pytest.raises(ValueError, match=r"1\.\.60"):
        build_pack(reference_backup, tmp_path / "rejected", **kwargs)
    assert not (tmp_path / "rejected").exists()


def test_pack_failure_removes_only_its_new_output(reference_backup, tmp_path, monkeypatch):
    monkeypatch.setattr(reference_pack, "_MAX_PACK_BYTES", 1)
    with pytest.raises(ValueError, match="allowance"):
        build_pack(reference_backup, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert (reference_backup / "manifest.json").exists()


@pytest.mark.parametrize("change", ["missing", "corrupt", "symlink", "escape", "empty", "holdout"])
def test_invalid_or_changed_input_cannot_validate(pack, tmp_path, change):
    destination, manifest = pack
    image = next(name for name in manifest["input_files"] if name.startswith("media/"))
    if change == "missing":
        (destination / image).unlink()
    elif change == "corrupt":
        (destination / image).write_bytes(b"changed image")
    elif change == "symlink":
        outside = tmp_path / "outside.png"
        outside.write_bytes((destination / image).read_bytes())
        (destination / image).unlink()
        (destination / image).symlink_to(outside)
    elif change == "escape":
        manifest["input_files"]["../outside.txt"] = "a" * 64
    elif change == "empty":
        manifest["cases"] = []
    elif change == "holdout":
        manifest["split"] = "holdout"
    (destination / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        validate_pack(destination)


def _review_labels(destination, manifest):
    for entry in manifest["cases"]:
        filename = destination / "labels" / f"{entry['case_id']}.json"
        label = json.loads(filename.read_text())
        label.update(
            reviewer_id="synthetic-test-reviewer",
            reviewed_at="2026-09-28T20:00:00Z",
            independent_of_candidate=True,
        )
        for fact in label["facts"].values():
            fact.update(state="unknown", reason="Synthetic fixture has no usable source evidence")
        filename.write_text(json.dumps(label))


def test_complete_unknown_review_is_valid_without_fabricated_scores(pack):
    destination, manifest = pack
    _review_labels(destination, manifest)
    result = validate_pack(destination, require_reviewed=True)
    assert result["fully_reviewed_cases"] == 5 and not result["promotion_ready"]
    entry = manifest["cases"][0]
    label = CaseAnnotation.model_validate_json(
        (destination / "labels" / f"{entry['case_id']}.json").read_bytes()
    )
    assert label.owner_judgment.workspace_rating is None


@pytest.mark.parametrize("change", ["case", "input", "citation", "quote", "incomplete", "reviewer"])
def test_review_is_bound_to_exact_case_and_source(pack, change):
    destination, manifest = pack
    _review_labels(destination, manifest)
    case = next(
        value
        for entry in manifest["cases"]
        if (value := json.loads((destination / entry["file"]).read_text()))["texts"]
    )
    path = destination / "labels" / f"{case['case_id']}.json"
    label = json.loads(path.read_text())
    fact = {
        "state": "supported",
        "value": "D",
        "evidence": [
            {
                "material_id": "text:" + case["texts"][0]["sha256"],
                "location": "First sentence",
                "quote": "EPC band D",
            }
        ],
    }
    label["facts"]["advertised_epc"] = fact
    if change == "case":
        label["case_id"] = "case-0000000000000000"
    elif change == "input":
        label["input_sha256"] = "0" * 64
    elif change == "citation":
        fact["evidence"][0]["material_id"] = "text:" + "0" * 64
    elif change == "quote":
        fact["evidence"][0]["quote"] = "EPC band A"
    elif change == "incomplete":
        label["facts"]["bathtub_visible"] = {"state": "unreviewed"}
    else:
        label["independent_of_candidate"] = False
    path.write_text(json.dumps(label))
    with pytest.raises((ValueError, ValidationError)):
        validate_pack(destination, require_reviewed=True)


def test_cli_validation_reports_counts_without_private_material(pack):
    destination, _ = pack
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "home_finder.pipeline.reference_pack",
            "validate",
            "--pack",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cases"] == 5
    assert "rightmove:" not in result.stdout and "MODEL ANSWER" not in result.stdout
