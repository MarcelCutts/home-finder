"""Tests for scripts/audit_image_cache.py."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from home_finder.utils.image_cache import get_cache_dir, save_image_bytes, url_to_filename

# Import the script module from the scripts/ directory (not a package)
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "audit_image_cache.py"
_spec = importlib.util.spec_from_file_location("audit_image_cache", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

phase1_db_to_disk = _mod.phase1_db_to_disk
phase2_disk_to_db = _mod.phase2_disk_to_db
main = _mod.main


def _setup_db(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Create property_images table and insert rows.

    Each row is (property_unique_id, source, url, image_type).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE property_images ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  property_unique_id TEXT NOT NULL,"
            "  source TEXT NOT NULL,"
            "  url TEXT NOT NULL,"
            "  image_type TEXT NOT NULL,"
            "  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "  UNIQUE(property_unique_id, url)"
            ")"
        )
        for prop_id, source, url, img_type in rows:
            conn.execute(
                "INSERT INTO property_images (property_unique_id, source, url, image_type)"
                " VALUES (?, ?, ?, ?)",
                (prop_id, source, url, img_type),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """Yield a sqlite3 connection with Row factory; auto-closed after test."""
    db_path = tmp_path / "properties.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn  # type: ignore[misc]
    conn.close()


class TestPhase1DbToDisk:
    """Tests for phase1_db_to_disk — DB rows vs disk cache."""

    def test_orphaned_rows_detected(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """DB rows with no disk file should be flagged as missing."""
        data_dir = str(tmp_path)
        uid = "zoopla:11111"
        url_with_file = "https://example.com/gallery1.jpg"
        url_without_file = "https://example.com/gallery2.jpg"

        fname = url_to_filename(url_with_file, "gallery", 0)
        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / fname, b"fake-image")

        _setup_db(tmp_path / "properties.db", [
            (uid, "zoopla", url_with_file, "gallery"),
            (uid, "zoopla", url_without_file, "gallery"),
        ])

        total, found, missing = phase1_db_to_disk(db_conn, data_dir)

        assert total == 2
        assert found == 1
        assert missing == 1

    def test_clean_db_no_orphans(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """All rows match disk files -> 0 missing."""
        data_dir = str(tmp_path)
        uid = "rightmove:22222"
        url1 = "https://example.com/img1.jpg"
        url2 = "https://example.com/img2.jpg"

        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / url_to_filename(url1, "gallery", 0), b"img1")
        save_image_bytes(cache_dir / url_to_filename(url2, "gallery", 1), b"img2")

        _setup_db(tmp_path / "properties.db", [
            (uid, "rightmove", url1, "gallery"),
            (uid, "rightmove", url2, "gallery"),
        ])

        total, found, missing = phase1_db_to_disk(db_conn, data_dir)

        assert total == 2
        assert found == 2
        assert missing == 0

    def test_fix_deletes_orphaned_rows(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """--fix should delete orphaned rows and return pre-fix counts."""
        data_dir = str(tmp_path)
        uid = "zoopla:33333"
        good_url = "https://example.com/good.jpg"
        bad_url = "https://example.com/bad.jpg"

        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / url_to_filename(good_url, "gallery", 0), b"ok")

        _setup_db(tmp_path / "properties.db", [
            (uid, "zoopla", good_url, "gallery"),
            (uid, "zoopla", bad_url, "gallery"),
        ])

        total, found, missing = phase1_db_to_disk(db_conn, data_dir, fix=True)

        # Return values reflect pre-fix state
        assert total == 2
        assert found == 1
        assert missing == 1

        # DB reflects post-fix state
        remaining = db_conn.execute("SELECT url FROM property_images").fetchall()
        assert len(remaining) == 1
        assert remaining[0]["url"] == good_url

    def test_fix_across_multiple_properties(
        self, tmp_path: Path, db_conn: sqlite3.Connection
    ) -> None:
        """--fix should delete orphans from multiple properties in one transaction."""
        data_dir = str(tmp_path)
        uid_a = "zoopla:10001"
        uid_b = "rightmove:10002"
        good_url_a = "https://example.com/a_good.jpg"
        bad_url_a = "https://example.com/a_bad.jpg"
        good_url_b = "https://example.com/b_good.jpg"
        bad_url_b1 = "https://example.com/b_bad1.jpg"
        bad_url_b2 = "https://example.com/b_bad2.jpg"

        # Property A: 1 good, 1 bad
        cache_a = get_cache_dir(data_dir, uid_a)
        save_image_bytes(cache_a / url_to_filename(good_url_a, "gallery", 0), b"ok")
        # Property B: 1 good, 2 bad
        cache_b = get_cache_dir(data_dir, uid_b)
        save_image_bytes(cache_b / url_to_filename(good_url_b, "gallery", 0), b"ok")

        _setup_db(tmp_path / "properties.db", [
            (uid_a, "zoopla", good_url_a, "gallery"),
            (uid_a, "zoopla", bad_url_a, "gallery"),
            (uid_b, "rightmove", good_url_b, "gallery"),
            (uid_b, "rightmove", bad_url_b1, "gallery"),
            (uid_b, "rightmove", bad_url_b2, "floorplan"),
        ])

        total, found, missing = phase1_db_to_disk(db_conn, data_dir, fix=True)

        assert total == 5
        assert found == 2
        assert missing == 3

        remaining = db_conn.execute(
            "SELECT property_unique_id, url FROM property_images ORDER BY id"
        ).fetchall()
        assert len(remaining) == 2
        assert remaining[0]["url"] == good_url_a
        assert remaining[1]["url"] == good_url_b

    def test_dry_run_preserves_rows(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """--fix --dry-run should report but not delete."""
        data_dir = str(tmp_path)
        uid = "zoopla:44444"
        bad_url = "https://example.com/missing.jpg"

        _setup_db(tmp_path / "properties.db", [(uid, "zoopla", bad_url, "gallery")])

        total, found, missing = phase1_db_to_disk(db_conn, data_dir, fix=True, dry_run=True)

        assert total == 1
        assert found == 0
        assert missing == 1

        remaining = db_conn.execute("SELECT COUNT(*) as c FROM property_images").fetchone()
        assert remaining["c"] == 1

    def test_property_losing_all_images_flagged(
        self, tmp_path: Path, db_conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Property with only orphaned images should appear in the losing-ALL warning."""
        data_dir = str(tmp_path)
        uid = "openrent:55555"
        url1 = "https://example.com/only1.jpg"

        _setup_db(tmp_path / "properties.db", [(uid, "openrent", url1, "gallery")])

        _total, _found, missing = phase1_db_to_disk(db_conn, data_dir)

        assert missing == 1

        captured = capsys.readouterr()
        assert "Properties losing ALL images after cleanup: 1" in captured.out
        assert "openrent:55555" in captured.out

    def test_epc_image_type_matches(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """DB row with image_type='epc' should match epc_* file on disk."""
        data_dir = str(tmp_path)
        uid = "zoopla:66666"
        epc_url = "https://example.com/epc.png"

        fname = url_to_filename(epc_url, "epc", 0)
        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / fname, b"epc-image")

        _setup_db(tmp_path / "properties.db", [(uid, "zoopla", epc_url, "epc")])

        total, found, missing = phase1_db_to_disk(db_conn, data_dir)

        assert total == 1
        assert found == 1
        assert missing == 0

    def test_empty_db(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """Empty property_images table should return all zeros."""
        data_dir = str(tmp_path)
        _setup_db(tmp_path / "properties.db", [])

        total, found, missing = phase1_db_to_disk(db_conn, data_dir)

        assert total == 0
        assert found == 0
        assert missing == 0


class TestPhase2DiskToDb:
    """Tests for phase2_disk_to_db — disk files vs DB records."""

    def test_disk_only_files_reported(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """Files on disk with no DB record should be counted."""
        data_dir = str(tmp_path)
        uid = "zoopla:77777"

        url_in_db = "https://example.com/tracked.jpg"
        url_not_in_db = "https://example.com/untracked.jpg"

        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / url_to_filename(url_in_db, "gallery", 0), b"ok")
        save_image_bytes(cache_dir / url_to_filename(url_not_in_db, "gallery", 1), b"extra")

        _setup_db(tmp_path / "properties.db", [(uid, "zoopla", url_in_db, "gallery")])

        total_files, with_record, without_record, epc_expected, unrecognized = (
            phase2_disk_to_db(db_conn, data_dir)
        )

        assert total_files == 2
        assert with_record == 1
        assert without_record == 1
        assert epc_expected == 0
        assert unrecognized == 0

    def test_epc_files_categorised_as_expected(
        self, tmp_path: Path, db_conn: sqlite3.Connection
    ) -> None:
        """Renamed epc_* files on disk should be counted as expected, not unexpected."""
        data_dir = str(tmp_path)
        uid = "zoopla:88888"

        epc_url = "https://example.com/chart.png"
        epc_fname = url_to_filename(epc_url, "epc", 0)
        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / epc_fname, b"epc-chart")

        _setup_db(tmp_path / "properties.db", [])

        total_files, with_record, without_record, epc_expected, unrecognized = (
            phase2_disk_to_db(db_conn, data_dir)
        )

        assert total_files == 1
        assert with_record == 0
        assert without_record == 1
        assert epc_expected == 1
        assert unrecognized == 0

    def test_no_cache_directory(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """Missing cache directory should be handled gracefully."""
        data_dir = str(tmp_path)
        _setup_db(tmp_path / "properties.db", [])

        total_files, with_record, without_record, epc_expected, unrecognized = (
            phase2_disk_to_db(db_conn, data_dir)
        )

        assert total_files == 0
        assert with_record == 0
        assert without_record == 0
        assert epc_expected == 0
        assert unrecognized == 0

    def test_unrecognized_filenames(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """Files that don't match the naming convention should be counted separately."""
        data_dir = str(tmp_path)
        uid = "zoopla:99999"

        url_in_db = "https://example.com/photo.jpg"
        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / url_to_filename(url_in_db, "gallery", 0), b"ok")
        # Non-conforming filenames
        save_image_bytes(cache_dir / ".DS_Store", b"junk")
        save_image_bytes(cache_dir / "thumbs.db", b"junk")

        _setup_db(tmp_path / "properties.db", [(uid, "zoopla", url_in_db, "gallery")])

        total_files, with_record, without_record, _epc_expected, unrecognized = (
            phase2_disk_to_db(db_conn, data_dir)
        )

        assert total_files == 3
        assert with_record == 1
        assert without_record == 0
        assert unrecognized == 2

    def test_multiple_property_dirs(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        """Phase 2 should handle multiple property directories correctly."""
        data_dir = str(tmp_path)
        uid_a = "zoopla:20001"
        uid_b = "rightmove:20002"

        url_a = "https://example.com/a.jpg"
        url_b = "https://example.com/b.jpg"
        url_extra = "https://example.com/extra.jpg"

        save_image_bytes(
            get_cache_dir(data_dir, uid_a) / url_to_filename(url_a, "gallery", 0), b"a"
        )
        save_image_bytes(
            get_cache_dir(data_dir, uid_b) / url_to_filename(url_b, "gallery", 0), b"b"
        )
        # Extra file in uid_b with no DB record
        save_image_bytes(
            get_cache_dir(data_dir, uid_b) / url_to_filename(url_extra, "gallery", 1), b"x"
        )

        _setup_db(tmp_path / "properties.db", [
            (uid_a, "zoopla", url_a, "gallery"),
            (uid_b, "rightmove", url_b, "gallery"),
        ])

        total_files, with_record, without_record, epc_expected, unrecognized = (
            phase2_disk_to_db(db_conn, data_dir)
        )

        assert total_files == 3
        assert with_record == 2
        assert without_record == 1
        assert epc_expected == 0
        assert unrecognized == 0


class TestMain:
    """Tests for the main() entry point."""

    def test_db_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """main() should print an error and return when DB is missing."""
        main(str(tmp_path))

        captured = capsys.readouterr()
        assert "Database not found" in captured.out

    def test_integration_both_phases(self, tmp_path: Path) -> None:
        """main() should run both phases on the same connection."""
        data_dir = str(tmp_path)
        uid = "zoopla:30001"
        good_url = "https://example.com/good.jpg"
        bad_url = "https://example.com/bad.jpg"

        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / url_to_filename(good_url, "gallery", 0), b"ok")

        _setup_db(tmp_path / "properties.db", [
            (uid, "zoopla", good_url, "gallery"),
            (uid, "zoopla", bad_url, "gallery"),
        ])

        # Should not raise
        main(data_dir)

    def test_fix_via_main(self, tmp_path: Path) -> None:
        """main(fix=True) should delete orphaned rows."""
        data_dir = str(tmp_path)
        uid = "zoopla:30002"
        good_url = "https://example.com/ok.jpg"
        bad_url = "https://example.com/gone.jpg"

        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(cache_dir / url_to_filename(good_url, "gallery", 0), b"ok")

        db_path = tmp_path / "properties.db"
        _setup_db(db_path, [
            (uid, "zoopla", good_url, "gallery"),
            (uid, "zoopla", bad_url, "gallery"),
        ])

        main(data_dir, fix=True)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            remaining = conn.execute("SELECT url FROM property_images").fetchall()
        finally:
            conn.close()

        assert len(remaining) == 1
        assert remaining[0]["url"] == good_url
