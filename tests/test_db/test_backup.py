"""Backup proofs: live WAL, concurrent edits, interruption and isolated restore."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from home_finder.db import backup
from home_finder.db.backup import (
    BackupError,
    create_backup,
    inventory_database,
    restore_backup,
    verify_backup,
)


@pytest.fixture
def live_database(tmp_path):
    path = tmp_path / "live.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.executescript("""
            CREATE TABLE properties (id TEXT PRIMARY KEY, status TEXT, price INTEGER);
            CREATE TABLE status_events (id INTEGER PRIMARY KEY, property_id TEXT, status TEXT);
            CREATE TABLE viewing_messages (property_id TEXT PRIMARY KEY, note TEXT);
            CREATE TABLE price_history (id INTEGER PRIMARY KEY, old INTEGER, new INTEGER);
            CREATE TABLE source_aliases (alias TEXT PRIMARY KEY, anchor TEXT);
            CREATE TABLE paid_attempts (id TEXT PRIMARY KEY, state TEXT, held INTEGER);
            CREATE TABLE mixed (text TEXT, number REAL, blob BLOB, absent TEXT);
            INSERT INTO properties VALUES ('fixture', 'interested', 1900);
            INSERT INTO status_events VALUES (1, 'fixture', 'interested');
            INSERT INTO viewing_messages VALUES ('fixture', 'Owner note: café 漢字');
            INSERT INTO price_history VALUES (1, 2000, 1900);
            INSERT INTO source_aliases VALUES ('legacy:fixture', 'fixture');
            INSERT INTO paid_attempts VALUES ('held', 'unknown', 6315000000);
            INSERT INTO mixed VALUES ('Unicode 🏠', 1.25, x'00FF01', NULL);
            PRAGMA user_version=9;
        """)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # Deliberately leave committed changes in WAL, absent from the main file.
        conn.execute("INSERT INTO status_events VALUES (2, 'fixture', 'viewing')")
        conn.commit()
        yield path, conn


@pytest.fixture
def assets(tmp_path):
    root = tmp_path / "assets"
    (root / "fixture" / "floorplan").mkdir(parents=True)
    (root / "fixture" / "gallery.jpg").write_bytes(b"gallery fixture")
    (root / "fixture" / "floorplan" / "1.png").write_bytes(b"floorplan fixture")
    return root


def test_live_wal_and_all_histories_restore_exactly(live_database, assets, tmp_path):
    path, conn = live_database
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    before = path.read_bytes()
    original = inventory_database(path)
    package, restored = tmp_path / "backup", tmp_path / "restored"
    manifest = create_backup(path, package, assets=assets)
    assert path.read_bytes() == before  # No checkpoint/replacement of the live source.
    assert manifest.inventory == original and manifest.source_frozen_sha256 is None
    assert manifest.inventory.tables["status_events"].rows == 2
    assert manifest.assets_state == "copied_over_capture_window" and len(manifest.assets) == 2
    assert verify_backup(package) == manifest
    assert restore_backup(package, restored) == manifest
    assert verify_backup(restored) == manifest
    assert inventory_database(restored / "properties.db", frozen=True) == original
    for item in [package, restored, *package.rglob("*"), *restored.rglob("*")]:
        assert item.stat().st_mode & 0o777 == (0o700 if item.is_dir() else 0o600)
    for name in manifest.assets:
        assert (restored / "image_cache" / name).read_bytes() == (assets / name).read_bytes()
    assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == 2
    assert not (restored / "properties.db-wal").exists()


def test_concurrent_edits_survive_and_backup_stays_one_snapshot(
    live_database, tmp_path, monkeypatch
):
    path, writer = live_database
    writer.execute("CREATE TABLE padding (body BLOB)")
    writer.executemany("INSERT INTO padding VALUES (zeroblob(4096))", [()] * 512)
    writer.commit()
    original = backup._check_time
    calls = 0

    def edit_during_copy(deadline):
        nonlocal calls
        original(deadline)
        if calls == 0:
            writer.execute("UPDATE properties SET status='offered' WHERE id='fixture'")
            writer.execute("INSERT INTO status_events VALUES (3, 'fixture', 'offered')")
            writer.execute("UPDATE viewing_messages SET note='Edited during backup'")
            writer.commit()
        calls += 1

    monkeypatch.setattr(backup, "_check_time", edit_during_copy)
    manifest = create_backup(path, tmp_path / "backup")
    assert calls > 1 and manifest.inventory.tables["status_events"].rows == 2
    assert writer.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == 3
    assert (
        writer.execute("SELECT note FROM viewing_messages").fetchone()[0] == "Edited during backup"
    )
    with closing(
        backup._open_read_only(tmp_path / "backup" / "properties.db", frozen=True)
    ) as conn:
        assert conn.execute("SELECT status FROM properties").fetchone()[0] == "interested"
        assert (
            conn.execute("SELECT note FROM viewing_messages").fetchone()[0].startswith("Owner note")
        )


def test_frozen_export_is_unchanged_and_missing_assets_are_explicit(live_database, tmp_path):
    path, writer = live_database
    with pytest.raises(BackupError, match="WAL/journal"):
        create_backup(path, tmp_path / "rejected", frozen=True)
    assert not (tmp_path / "rejected").exists()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = path.read_bytes()
    manifest = create_backup(path, tmp_path / "frozen", frozen=True)
    assert manifest.source_mode == "frozen_immutable" and manifest.source_frozen_sha256
    assert manifest.assets_state == "not_included" and manifest.assets == {}
    assert path.read_bytes() == before
    assert inventory_database(path, frozen=True) == manifest.inventory


@pytest.mark.parametrize("kind", ["create", "restore"])
def test_existing_destination_is_never_touched(live_database, tmp_path, kind):
    path, _ = live_database
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "properties.db"
    sentinel.write_bytes(b"Do not replace this authority")
    if kind == "create":
        with pytest.raises(FileExistsError):
            create_backup(path, existing)
    else:
        package = tmp_path / "backup"
        create_backup(path, package)
        with pytest.raises(FileExistsError):
            restore_backup(package, existing)
    assert sentinel.read_bytes() == b"Do not replace this authority"


@pytest.mark.parametrize("change", ["database", "asset", "missing_asset", "extra", "inventory"])
def test_corrupt_or_incomplete_packages_cannot_restore(live_database, assets, tmp_path, change):
    path, _ = live_database
    package = tmp_path / "backup"
    create_backup(path, package, assets=assets)
    if change == "database":
        with closing(sqlite3.connect(package / "properties.db")) as conn:
            conn.execute("DELETE FROM price_history")
            conn.commit()
    elif change == "asset":
        (package / "image_cache" / "fixture" / "gallery.jpg").write_bytes(b"altered fixture")
    elif change == "missing_asset":
        (package / "image_cache" / "fixture" / "gallery.jpg").unlink()
    elif change == "extra":
        (package / "unexpected").write_text("Not in manifest")
    else:
        data = json.loads((package / "manifest.json").read_text())
        data["inventory"]["tables"]["price_history"]["rows"] = 0
        (package / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(BackupError):
        restore_backup(package, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "a/../b", "a//b", "./a", "a\\b", "\x00"]
)
def test_manifest_paths_cannot_escape_restore(live_database, assets, tmp_path, name):
    path, _ = live_database
    package = tmp_path / "backup"
    create_backup(path, package, assets=assets)
    data = json.loads((package / "manifest.json").read_text())
    data["assets"][name] = next(iter(data["assets"].values()))
    (package / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsafe asset path"):
        restore_backup(package, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("kind", ["file", "directory", "root"])
def test_symlinks_are_not_followed(live_database, assets, tmp_path, kind):
    path, _ = live_database
    if kind == "root":
        alias = tmp_path / "alias"
        alias.symlink_to(assets, target_is_directory=True)
        assets = alias
    elif kind == "directory":
        (assets / "link").symlink_to(tmp_path, target_is_directory=True)
    else:
        (assets / "link").symlink_to(path)
    with pytest.raises(BackupError, match="symlink"):
        create_backup(path, tmp_path / "backup", assets=assets)
    assert not (tmp_path / "backup").exists()


def test_asset_changes_during_capture_fail_without_publishing(
    live_database, assets, tmp_path, monkeypatch
):
    path, _ = live_database
    original = backup._file_digest
    changed = False

    def change_after_copy(source, deadline, **kwargs):
        nonlocal changed
        result = original(source, deadline, **kwargs)
        if kwargs.get("copy_to") is not None and not changed:
            changed = True
            (assets / "added.jpg").write_bytes(b"arrived during copy")
        return result

    monkeypatch.setattr(backup, "_file_digest", change_after_copy)
    with pytest.raises(BackupError, match="tree changed"):
        create_backup(path, tmp_path / "backup", assets=assets)
    assert not (tmp_path / "backup").exists()


def test_process_exit_leaves_no_completed_package(live_database, tmp_path):
    path, _ = live_database
    package = tmp_path / "interrupted"
    child = """
import os, sys
from pathlib import Path
from home_finder.db import backup
backup._publish_manifest = lambda *args: os._exit(23)
backup.create_backup(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    run = subprocess.run(
        [sys.executable, "-c", child, str(path), str(package)], capture_output=True, timeout=15
    )
    assert run.returncode == 23, run.stderr.decode()
    assert (package / "properties.db").exists() and not (package / "manifest.json").exists()
    with pytest.raises(FileNotFoundError):
        verify_backup(package)
    with pytest.raises(FileExistsError):
        create_backup(path, package)


def test_failed_backup_removes_only_its_new_output(live_database, tmp_path, monkeypatch):
    path, _ = live_database

    def expired(deadline):
        raise BackupError("deadline exceeded")

    monkeypatch.setattr(backup, "_check_time", expired)
    with pytest.raises(BackupError, match="deadline"):
        create_backup(path, tmp_path / "backup")
    assert path.exists() and not (tmp_path / "backup").exists()


def test_missing_input_and_recursive_destinations_are_rejected(live_database, assets, tmp_path):
    path, _ = live_database
    with pytest.raises(FileNotFoundError):
        create_backup(tmp_path / "missing.db", tmp_path / "backup")
    assert not (tmp_path / "missing.db").exists() and not (tmp_path / "backup").exists()
    with pytest.raises(BackupError, match="inside"):
        create_backup(path, assets / "backup", assets=assets)
    package = tmp_path / "backup"
    create_backup(path, package)
    with pytest.raises(BackupError, match="inside"):
        restore_backup(package, package / "restored")
    assert verify_backup(package)


def test_inventory_is_duplicate_preserving_and_independent_of_row_order(tmp_path):
    inventories = []
    for index, values in enumerate(([1, 2, 1], [1, 1, 2], [1, 2])):
        path = tmp_path / f"{index}.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE "odd""name" (value INTEGER)')
            conn.executemany('INSERT INTO "odd""name" VALUES (?)', [(v,) for v in values])
            conn.commit()
        inventories.append(inventory_database(path, frozen=True))
    assert inventories[0] == inventories[1]
    assert inventories[0].tables['odd"name'] != inventories[2].tables['odd"name']


def test_cli_round_trip_does_not_migrate_or_start_processing(live_database, tmp_path):
    path, _ = live_database
    package, restored = tmp_path / "backup", tmp_path / "restored"
    steps = [
        ["create", "--database", str(path), "--destination", str(package)],
        ["verify", "--package", str(package)],
        ["restore", "--package", str(package), "--destination", str(restored)],
    ]
    for args in steps:
        run = subprocess.run(
            [sys.executable, "-m", "home_finder.pipeline.backup", *args],
            capture_output=True,
            timeout=15,
        )
        assert run.returncode == 0, run.stderr.decode()
        report = json.loads(run.stdout)
        assert report["schema_version"] == 9 and report["table_rows"]["status_events"] == 2
        assert report["assets_state"] == "not_included" and report["asset_files"] == 0
    assert verify_backup(restored).inventory == inventory_database(path)


@pytest.mark.parametrize("limit", ["database", "assets", "rows", "manifest"])
def test_resource_limits_fail_without_replacing_data(
    live_database, assets, tmp_path, monkeypatch, limit
):
    path, _ = live_database
    package = tmp_path / "backup"
    if limit == "manifest":
        create_backup(path, package)
        monkeypatch.setattr(backup, "MAX_MANIFEST_BYTES", 10)
        with pytest.raises(BackupError, match="limit"):
            verify_backup(package)
        assert (package / "properties.db").exists()
    else:
        setting = {
            "database": "MAX_DATABASE_BYTES",
            "assets": "MAX_ASSET_FILES",
            "rows": "MAX_TABLE_ROWS",
        }[limit]
        monkeypatch.setattr(backup, setting, 1)
        with pytest.raises(BackupError, match="limit"):
            create_backup(path, package, assets=assets)
        assert not package.exists()
    assert path.exists()


def test_restore_detects_source_change_after_verification(live_database, tmp_path, monkeypatch):
    path, _ = live_database
    package = tmp_path / "backup"
    create_backup(path, package)
    original = backup.verify_backup

    def verified_then_changed(*args, **kwargs):
        manifest = original(*args, **kwargs)
        with closing(sqlite3.connect(package / "properties.db")) as conn:
            conn.execute("UPDATE viewing_messages SET note='changed after verification'")
            conn.commit()
        return manifest

    monkeypatch.setattr(backup, "verify_backup", verified_then_changed)
    with pytest.raises(BackupError, match="changed after verification"):
        restore_backup(package, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("args", [[], ["--full"], ["--force"]])
def test_retired_sync_script_cannot_call_remote_tools(tmp_path, args):
    marker = tmp_path / "called"
    fake_fly = tmp_path / "fly"
    fake_fly.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
    fake_fly.chmod(0o700)
    run = subprocess.run(
        ["bash", "scripts/sync-to-fly.sh", *args],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        timeout=5,
    )
    assert run.returncode == 2 and b"disabled" in run.stderr
    assert not marker.exists()
