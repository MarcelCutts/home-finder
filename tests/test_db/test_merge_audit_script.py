"""Legacy maintenance must not mistake different photographs for an erase command."""

import json
import sqlite3

import pytest

from home_finder.db import PropertyStorage
from scripts import audit_merges


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("matches,expected", [(0, "REVIEW NEEDED"), (1, "SHARED PHOTOGRAPHS")])
async def test_audit_always_opens_readonly_and_preserves_rows_and_cache(
    tmp_path, sample_property, monkeypatch, capsys, dry_run, matches, expected
):
    database = tmp_path / "properties.db"
    cache = tmp_path / "image_cache" / "original.jpeg"
    cache.parent.mkdir()
    cache.write_bytes(b"retained cache sentinel")
    async with PropertyStorage(str(database)) as db:
        await db.save_property(sample_property)
        async with db._transaction() as conn:
            await conn.execute(
                "UPDATE properties SET sources = ?", (json.dumps(["rightmove", "onthemarket"]),)
            )
            await conn.executemany(
                "INSERT INTO property_images (property_unique_id, source, url, image_type) "
                "VALUES (?, ?, ?, 'gallery')",
                [
                    (sample_property.unique_id, source, f"https://example.test/{source}.jpg")
                    for source in ("rightmove", "onthemarket")
                ],
            )
        before = database.read_bytes()
        monkeypatch.setattr(audit_merges, "_hash_gallery_for_source", lambda *args: ["0"] * 3)
        monkeypatch.setattr(audit_merges, "count_gallery_hash_matches", lambda *args: matches)
        connect = sqlite3.connect

        def readonly_connect(path, **kwargs):
            assert path.endswith("?mode=ro") and kwargs == {"uri": True}
            return connect(path, **kwargs)

        monkeypatch.setattr(audit_merges.sqlite3, "connect", readonly_connect)
        audit_merges.main(str(tmp_path), dry_run=dry_run)
        assert expected in capsys.readouterr().out
        assert database.read_bytes() == before
        assert cache.read_bytes() == b"retained cache sentinel"
        assert await db.get_property(sample_property.unique_id) is not None
        assert len(await db.get_property_images(sample_property.unique_id)) == 2


def test_missing_database_is_never_created(tmp_path, capsys):
    audit_merges.main(str(tmp_path))
    assert not (tmp_path / "properties.db").exists()
    assert "Database not found" in capsys.readouterr().out
