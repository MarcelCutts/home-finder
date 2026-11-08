"""Storage connection lifecycle: context manager, health check, reconnection and close."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from home_finder.db.storage import PropertyStorage


@pytest.mark.asyncio
async def test_context_manager_initializes_and_closes() -> None:
    storage = PropertyStorage(":memory:")
    async with storage:
        cursor = await (await storage._get_connection()).execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] > 0
    assert storage._conn is None


class TestReconnectAfterClose:
    """After close(), _get_connection() transparently reopens the same database file."""

    @pytest.mark.asyncio
    async def test_reconnects_to_the_same_database_after_close(
        self, tmp_path, make_property
    ) -> None:
        storage = PropertyStorage(str(tmp_path / "reconnect.db"))
        await storage.initialize()
        prop = make_property()
        await storage.save_property(prop)
        conn1 = await storage._get_connection()

        await storage.close()
        assert storage._conn is None

        conn2 = await storage._get_connection()
        assert conn2 is not conn1
        assert await storage.get_property(prop.unique_id) is not None
        await storage.close()

    @pytest.mark.asyncio
    async def test_pragmas_applied_after_reconnect(self, tmp_path) -> None:
        storage = PropertyStorage(str(tmp_path / "pragmas.db"))
        await storage.initialize()
        await storage.close()

        conn = await storage._get_connection()

        async def pragma(name: str):
            cursor = await conn.execute(f"PRAGMA {name}")
            return (await cursor.fetchone())[0]

        assert await pragma("journal_mode") == "wal"
        assert await pragma("busy_timeout") == 5000
        # FULL = 2: dispatch reservations must survive a host restart.
        assert await pragma("synchronous") == 2
        assert await pragma("cache_size") == -32000
        assert await pragma("foreign_keys") == 1
        assert await pragma("temp_store") == 2  # MEMORY
        await storage.close()

    @pytest.mark.asyncio
    async def test_optimize_runs_on_connect(self) -> None:
        """PRAGMA optimize=0x10002 should execute on every new connection."""
        import aiosqlite

        storage = PropertyStorage(":memory:")
        await storage.initialize()
        await storage.close()

        # Patch aiosqlite.connect to track SQL on the fresh connection
        executed: list[str] = []
        real_connect = aiosqlite.connect

        async def tracking_connect(*a, **kw):  # type: ignore[no-untyped-def]
            conn = await real_connect(*a, **kw)
            orig_execute = conn.execute

            async def tracking_execute(sql, *args, **kwargs):  # type: ignore[no-untyped-def]
                executed.append(sql)
                return await orig_execute(sql, *args, **kwargs)

            conn.execute = tracking_execute  # type: ignore[method-assign]
            return conn

        with patch(
            "home_finder.db.storage.aiosqlite.connect",
            side_effect=tracking_connect,
        ):
            await storage._get_connection()

        assert "PRAGMA optimize=0x10002" in executed
        assert not any("mmap_size" in s for s in executed)
        await storage.close()


class TestHealthCheckReconnectsOnFailure:
    """When SELECT 1 fails, the health check should drop the connection."""

    @pytest.mark.asyncio
    async def test_reconnects_to_the_same_database_when_the_probe_fails(
        self, tmp_path, make_property
    ) -> None:
        storage = PropertyStorage(str(tmp_path / "probe.db"))
        await storage.initialize()
        prop = make_property()
        await storage.save_property(prop)

        # Force the health check to fire by backdating the timestamp
        storage._last_health_check = time.monotonic() - 60
        old_conn = storage._conn
        assert old_conn is not None
        old_conn.execute = AsyncMock(side_effect=OSError("connection lost"))  # type: ignore[method-assign]

        conn = await storage._get_connection()
        assert conn is not old_conn
        assert await storage.get_property(prop.unique_id) is not None
        await storage.close()

    @pytest.mark.asyncio
    async def test_skips_health_check_within_interval(self) -> None:
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Health check timestamp is fresh — probe should NOT fire
        conn1 = await storage._get_connection()
        with patch.object(conn1, "execute", wraps=conn1.execute) as mock_exec:
            conn2 = await storage._get_connection()
            # Should return the same connection without calling execute
            assert conn2 is conn1
            mock_exec.assert_not_called()

        await storage.close()

    @pytest.mark.asyncio
    async def test_dead_connection_closed_before_replacement(self) -> None:
        """Never abandon a worker which could still own a transaction."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        storage._last_health_check = time.monotonic() - 60

        old_conn = storage._conn
        assert old_conn is not None
        old_conn.execute = AsyncMock(side_effect=OSError("hung"))  # type: ignore[method-assign]
        old_conn.close = AsyncMock(wraps=old_conn.close)  # type: ignore[method-assign]

        await storage._get_connection()

        old_conn.close.assert_awaited_once()

        await storage.close()


class TestClosePragmas:
    """Maintenance PRAGMAs run before connection close."""

    @pytest.mark.asyncio
    async def test_close_runs_maintenance_pragmas(self) -> None:
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        conn = await storage._get_connection()
        executed: list[str] = []
        original_execute = conn.execute

        async def _tracking_execute(sql: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            executed.append(sql)
            return await original_execute(sql, *args, **kwargs)

        conn.execute = _tracking_execute  # type: ignore[method-assign]

        await storage.close()

        assert "PRAGMA optimize" in executed
        assert "PRAGMA wal_checkpoint(PASSIVE)" in executed

    @pytest.mark.asyncio
    async def test_close_succeeds_even_if_pragmas_fail(self) -> None:
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        conn = await storage._get_connection()
        original_close = conn.close

        # Make execute raise for any PRAGMA during close
        conn.execute = AsyncMock(side_effect=OSError("disk error"))  # type: ignore[method-assign]
        conn.close = original_close  # type: ignore[method-assign]

        # close() should complete without raising
        await storage.close()
        assert storage._conn is None
