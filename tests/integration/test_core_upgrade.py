"""Upgrade the deployed owner schema without losing decisions or rollback evidence."""

from pathlib import Path
from unittest.mock import patch

import aiosqlite

from home_finder.db.backup import create_backup, restore_backup, verify_backup
from home_finder.db.migrations import MIGRATIONS, run_migrations


async def test_owner_schema_upgrade_preserves_state_and_isolated_rollback(tmp_path: Path):
    database = tmp_path / "authority.db"
    async with aiosqlite.connect(database) as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:7]):
            assert await run_migrations(conn) == 7
        await conn.executescript("""
            INSERT INTO properties (
                unique_id, source, source_id, url, title, price_pcm, bedrooms,
                address, first_seen, user_status, description
            ) VALUES (
                'openrent:12345', 'openrent', '12345',
                'https://www.openrent.co.uk/property-to-rent/london/flat/12345',
                'Upgrade fixture', 2000, 1, 'Example address',
                '2026-01-01T00:00:00+00:00', 'interested', 'Retained advert text'
            );
            INSERT INTO status_events (
                property_unique_id, from_status, to_status, note, source, telegram_update_id
            ) VALUES ('openrent:12345', 'new', 'interested', 'Owner note', 'telegram', 123);
            INSERT INTO viewing_messages VALUES (
                'openrent:12345', 'Please arrange a viewing', '2026-01-02'
            );
            INSERT INTO price_history (
                property_unique_id, old_price, new_price, change_amount, source
            ) VALUES ('openrent:12345', 2100, 2000, -100, 'openrent');
            INSERT INTO source_aliases (unique_id, source, source_id, anchor_id)
                VALUES ('rightmove:67890', 'rightmove', '67890', 'openrent:12345');
            INSERT INTO source_listings (
                unique_id, source, source_id, url, title, price_pcm, bedrooms, address,
                first_seen, last_seen, merged_id, description
            ) SELECT unique_id, source, source_id, url, title, price_pcm, bedrooms, address,
                first_seen, first_seen, unique_id, description FROM properties;
        """)
        await conn.commit()
        tables = (
            "properties",
            "source_listings",
            "status_events",
            "viewing_messages",
            "price_history",
            "source_aliases",
        )
        before = {}
        columns = {}
        for table in tables:
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            columns[table] = [row[1] for row in await cursor.fetchall()]
            cursor = await conn.execute(f"SELECT * FROM {table}")
            before[table] = await cursor.fetchall()

        package = tmp_path / "backup"
        manifest = create_backup(database, package)
        assert manifest.inventory.schema_version == 7
        assert verify_backup(package) == manifest

        assert await run_migrations(conn) == len(MIGRATIONS)
        for table in tables:
            names = ", ".join(columns[table])
            cursor = await conn.execute(f"SELECT {names} FROM {table}")
            assert await cursor.fetchall() == before[table]
        cursor = await conn.execute("SELECT source_text_json FROM properties")
        assert await cursor.fetchone() == (None,)  # No invented historical evidence.
        cursor = await conn.execute("SELECT COUNT(*) FROM paid_attempts")
        assert await cursor.fetchone() == (0,)
        cursor = await conn.execute("PRAGMA foreign_key_check")
        assert await cursor.fetchall() == []

    isolated = tmp_path / "rollback"
    assert restore_backup(package, isolated) == manifest
    assert verify_backup(isolated).inventory.schema_version == 7
    async with aiosqlite.connect(database) as conn:
        cursor = await conn.execute("PRAGMA user_version")
        # Restore never replaces the upgraded authority.
        assert await cursor.fetchone() == (len(MIGRATIONS),)
