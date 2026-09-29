"""Offline identity audits work before migration and do not mutate their input."""

import hashlib
import json
import sqlite3
import stat

import pytest

from home_finder.pipeline.identity_audit import main, read_identity_audit, write_identity_audit


def add_source(conn, source_id="123"):
    conn.execute(
        "INSERT INTO source_listings VALUES (?, 'rightmove', ?, ?, 0, 'existing-home')",
        (f"rightmove:{source_id}", source_id, f"https://rightmove.co.uk/properties/{source_id}"),
    )
    conn.commit()


@pytest.fixture
def legacy_database(tmp_path):
    database = tmp_path / "historical ? copy.db"
    conn = sqlite3.connect(database)
    conn.execute("PRAGMA user_version = 6")
    conn.execute(
        "CREATE TABLE source_listings (unique_id TEXT PRIMARY KEY, source TEXT, source_id TEXT, "
        "url TEXT, is_backfilled INTEGER, merged_id TEXT)"
    )
    add_source(conn)
    conn.close()
    return database


def test_read_only_audit_preserves_legacy_database_and_writes_private_report(
    legacy_database, tmp_path
):
    before = legacy_database.read_bytes()
    output = tmp_path / "audit"
    path = write_identity_audit(legacy_database, output)
    report = json.loads(path.read_text())
    assert report["database_schema_version"] == 6
    assert report["audit"]["resolved_rows"] == 1
    assert report["audit"]["groups"][0]["current_home_ids"] == ["existing-home"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert legacy_database.read_bytes() == before
    assert (
        read_identity_audit(legacy_database)[1].snapshot_sha256
        != hashlib.sha256(before).hexdigest()
    )
    with pytest.raises(FileExistsError):
        write_identity_audit(legacy_database, output)
    assert json.loads(path.read_text()) == report


def test_read_only_audit_respects_committed_wal_data(legacy_database):
    writer = sqlite3.connect(legacy_database)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        add_source(writer, "456")
        version, audit = read_identity_audit(legacy_database)
        assert version == 6
        assert audit.provider_entities == 2
    finally:
        writer.close()


def test_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        read_identity_audit(missing)
    assert not missing.exists()


def test_console_summary_omits_raw_ids_and_urls(legacy_database, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["identity_audit", "--database", str(legacy_database), "--output", str(tmp_path / "audit")],
    )
    main()
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["source_rows"] == summary["provider_entities"] == 1
    assert "rightmove:123" not in output
    assert "https://" not in output
    assert "existing-home" not in output
