"""Read-only projection review respects WAL inputs without consuming pending work."""

import json
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from home_finder.db import PropertyStorage
from home_finder.pipeline.projection_audit import (
    main,
    read_projection_audit,
    write_projection_audit,
)
from tests.test_db.test_membership import REVIEW, snapshot


@pytest.fixture
async def homes(tmp_path, make_property):
    async with PropertyStorage(str(tmp_path / "projection ? audit.db")) as db:
        props = [
            make_property(source_id=str(i), url=f"https://rightmove.co.uk/properties/{i}")
            for i in (123, 456)
        ]
        for prop in props:
            await db.save_property(prop)
        targets = [await db.homes.resolve(prop.unique_id) for prop in props]
        yield db, props, targets


async def test_audit_preserves_pending_work_and_uses_committed_wal_inputs(homes, tmp_path):
    db, props, targets = homes
    await db.projections.rebuild_pending()
    # Keep the application connection open: latest changes are in WAL, not the main file.
    merge = await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)
    await db.memberships.apply(merge, allow_incomplete_projections=True)
    before = await snapshot(db)
    output = tmp_path / "private-audit"
    path = await write_projection_audit(Path(db.db_path), output, home_ids=(targets[0].home_id,))
    report = json.loads(path.read_text())
    assert report["selected_homes"] == 1 and report["pending_homes"] == 2
    entry = report["entries"][0]
    assert entry["members_added"] == [props[1].unique_id]
    assert entry["members_removed"] == []
    assert len(entry["previous"]["listings"]) == 1
    assert len(entry["candidate"]["listings"]) == 2
    assert entry["material_changed"] and not entry["route_changed"]
    assert entry["expected_input_generation"] == entry["candidate"]["input_generation"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        await write_projection_audit(Path(db.db_path), output)
    assert await snapshot(db) == before
    assert json.loads(path.read_text()) == report
    assert (await read_projection_audit(Path(db.db_path), limit=1))["selected_homes"] == 1
    await db.projections.rebuild_pending()
    assert (await read_projection_audit(Path(db.db_path)))["entries"] == []


async def test_audit_refuses_unsupported_database_without_migrating_or_creating_it(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        await read_projection_audit(missing)
    assert not missing.exists()
    with closing(sqlite3.connect(missing)) as conn:
        conn.execute("PRAGMA user_version = 14")
    before = missing.read_bytes()
    with pytest.raises(ValueError, match="already migrated schema 15"):
        await write_projection_audit(missing, tmp_path / "refused")
    assert missing.read_bytes() == before and not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    "kwargs",
    [{"limit": 0}, {"limit": 26}, {"home_ids": ("x", "x")}, {"home_ids": ("x", "y"), "limit": 1}],
)
async def test_audit_refuses_invalid_bounds(homes, kwargs):
    db, _, _ = homes
    with pytest.raises(ValueError):
        await read_projection_audit(Path(db.db_path), **kwargs)


async def test_audit_size_or_missing_home_failure_does_not_write_output(
    homes, tmp_path, monkeypatch
):
    db, _, _ = homes
    with pytest.raises(ValueError, match="unknown_home_projection"):
        await write_projection_audit(Path(db.db_path), tmp_path / "absent", home_ids=("missing",))
    assert not (tmp_path / "absent").exists()
    monkeypatch.setattr("home_finder.pipeline.projection_audit.MAX_AUDIT_BYTES", 1)
    with pytest.raises(ValueError, match="review bound"):
        await write_projection_audit(Path(db.db_path), tmp_path / "oversize")
    assert not (tmp_path / "oversize").exists()


async def test_console_reports_counts_without_listing_evidence(
    homes, tmp_path, monkeypatch, capsys
):
    # main owns its event loop, so execute it in a worker thread alongside the open WAL writer.
    import asyncio

    db, props, targets = homes
    monkeypatch.setattr(
        "sys.argv",
        [
            "projection_audit",
            "--database",
            db.db_path,
            "--output",
            str(tmp_path / "cli"),
            "--limit",
            "1",
        ],
    )
    capsys.readouterr()
    await asyncio.to_thread(main)
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["selected_homes"] == 1 and summary["pending_homes"] == 2
    assert all(prop.unique_id not in output for prop in props)
    assert all(home.home_id not in output for home in targets)
    assert "https://" not in output
