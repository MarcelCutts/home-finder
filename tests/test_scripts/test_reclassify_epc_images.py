"""Tests for scripts/reclassify_epc_images.py."""

import importlib.util
import sqlite3
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from home_finder.utils.image_cache import get_cache_dir, save_image_bytes, url_to_filename

# Import the script module from the scripts/ directory (not a package)
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "reclassify_epc_images.py"
_spec = importlib.util.spec_from_file_location("reclassify_epc_images", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_reclassify_in_db = _mod._reclassify_in_db
_undo_disk_renames = _mod._undo_disk_renames


def _make_epc_bytes() -> bytes:
    """Create synthetic EPC chart image bytes (coloured bands on white)."""
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    band_colors = [
        (0, 128, 0), (50, 180, 50), (140, 200, 60),
        (255, 255, 0), (255, 165, 0), (255, 100, 0), (255, 0, 0),
    ]
    band_height = 25
    for i, color in enumerate(band_colors):
        y = 50 + i * 29
        band_width = 120 + 30 * i
        draw.rectangle([40, y, 40 + band_width, y + band_height], fill=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_photo_bytes() -> bytes:
    """Create synthetic room photo image bytes."""
    import random

    rng = random.Random(42)
    img = Image.new("RGB", (400, 300), (135, 206, 235))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 200, 400, 300], fill=(34, 139, 34))
    draw.rectangle([50, 100, 150, 200], fill=(139, 69, 19))
    pixels = img.load()
    assert pixels is not None
    for y in range(300):
        for x in range(400):
            r, g, b = pixels[x, y]  # type: ignore[misc]
            n = rng.randint(-20, 20)
            pixels[x, y] = (
                max(0, min(255, r + n)),
                max(0, min(255, g + n)),
                max(0, min(255, b + n)),
            )
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _setup_db(db_path: Path, rows: list[tuple[str, str, str]]) -> None:
    """Create property_images table and insert rows (property_unique_id, url, image_type)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE property_images ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  property_unique_id TEXT NOT NULL,"
        "  url TEXT NOT NULL,"
        "  image_type TEXT NOT NULL"
        ")"
    )
    for prop_id, url, img_type in rows:
        conn.execute(
            "INSERT INTO property_images (property_unique_id, url, image_type) VALUES (?, ?, ?)",
            (prop_id, url, img_type),
        )
    conn.commit()
    conn.close()


class TestReclassifyInDb:
    """Tests for _reclassify_in_db() — Phase 2."""

    def test_reclassifies_epc_and_renames_on_disk(self, tmp_path: Path) -> None:
        """Should update DB to image_type='epc' AND rename file to epc_* on disk."""
        data_dir = str(tmp_path)
        unique_id = "zoopla:11111"
        epc_url = "https://example.com/epc.png"
        photo_url = "https://example.com/photo.jpg"

        # Cache files on disk
        epc_fname = url_to_filename(epc_url, "gallery", 0)
        photo_fname = url_to_filename(photo_url, "gallery", 1)
        cache_dir = get_cache_dir(data_dir, unique_id)
        epc_path = cache_dir / epc_fname
        photo_path = cache_dir / photo_fname
        save_image_bytes(epc_path, _make_epc_bytes())
        save_image_bytes(photo_path, _make_photo_bytes())

        # Set up DB with both as gallery
        db_path = tmp_path / "test.db"
        _setup_db(db_path, [
            (unique_id, epc_url, "gallery"),
            (unique_id, photo_url, "gallery"),
        ])

        scanned, reclassified, errors = _reclassify_in_db(db_path, data_dir, dry_run=False)

        assert scanned == 2
        assert reclassified == 1
        assert errors == 0

        # DB should have image_type='epc' for the EPC
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT url, image_type FROM property_images ORDER BY id"
        ).fetchall()
        conn.close()
        assert rows[0]["image_type"] == "epc"
        assert rows[1]["image_type"] == "gallery"

        # Disk: EPC file renamed, photo file unchanged
        assert not epc_path.exists()
        epc_renamed = cache_dir / epc_fname.replace("gallery_", "epc_", 1)
        assert epc_renamed.exists()
        assert photo_path.exists()

    def test_dry_run_does_not_rename(self, tmp_path: Path) -> None:
        """Dry run should not modify DB or rename files."""
        data_dir = str(tmp_path)
        unique_id = "zoopla:22222"
        epc_url = "https://example.com/epc.png"

        epc_fname = url_to_filename(epc_url, "gallery", 0)
        cache_dir = get_cache_dir(data_dir, unique_id)
        epc_path = cache_dir / epc_fname
        save_image_bytes(epc_path, _make_epc_bytes())

        db_path = tmp_path / "test.db"
        _setup_db(db_path, [(unique_id, epc_url, "gallery")])

        _scanned, reclassified, _errors = _reclassify_in_db(db_path, data_dir, dry_run=True)

        assert reclassified == 1  # counted but not applied

        # DB unchanged
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT image_type FROM property_images").fetchone()
        conn.close()
        assert row["image_type"] == "gallery"

        # File unchanged
        assert epc_path.exists()


class TestUndoDiskRenames:
    """Tests for _undo_disk_renames() — Phase 1."""

    def test_reverts_epc_files_to_gallery(self, tmp_path: Path) -> None:
        """Should rename epc_* files back to gallery_*."""
        cache_root = tmp_path / "image_cache"
        prop_dir = cache_root / "zoopla_33333"
        prop_dir.mkdir(parents=True)

        epc_file = prop_dir / "epc_000_abc12345.jpg"
        epc_file.write_bytes(b"fake")

        reverted = _undo_disk_renames(cache_root, dry_run=False)

        assert reverted == 1
        assert not epc_file.exists()
        assert (prop_dir / "gallery_000_abc12345.jpg").exists()

    def test_dry_run_does_not_revert(self, tmp_path: Path) -> None:
        """Dry run should count but not rename."""
        cache_root = tmp_path / "image_cache"
        prop_dir = cache_root / "zoopla_33333"
        prop_dir.mkdir(parents=True)

        epc_file = prop_dir / "epc_000_abc12345.jpg"
        epc_file.write_bytes(b"fake")

        reverted = _undo_disk_renames(cache_root, dry_run=True)

        assert reverted == 1
        assert epc_file.exists()  # not renamed
