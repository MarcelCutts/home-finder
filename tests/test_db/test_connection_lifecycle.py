"""Tests for connection health check and reconnection logic."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from home_finder.db.storage import PropertyStorage


class TestReconnectAfterClose:
    """After close(), _get_connection() should transparently reconnect."""

    @pytest.mark.asyncio
    async def test_reconnects_after_close(self) -> None:
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Verify initial connection works
        conn1 = await storage._get_connection()
        cursor = await conn1.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1

        # Close the connection
        await storage.close()
        assert storage._conn is None

        # _get_connection should create a new connection
        conn2 = await storage._get_connection()
        assert conn2 is not None
        assert conn2 is not conn1

        # New connection should work
        cursor = await conn2.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1

        await storage.close()

    @pytest.mark.asyncio
    async def test_pragmas_applied_after_reconnect(self) -> None:
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        await storage.close()

        # Reconnect
        conn = await storage._get_connection()

        # Verify PRAGMAs were re-applied
        cursor = await conn.execute("PRAGMA journal_mode")
        journal = (await cursor.fetchone())[0]
        # In-memory databases return "memory" for journal_mode, not "wal"
        # For file-based DBs this would be "wal". Just verify the PRAGMA ran.
        assert journal in ("wal", "memory")

        cursor = await conn.execute("PRAGMA busy_timeout")
        timeout = (await cursor.fetchone())[0]
        assert timeout == 5000

        cursor = await conn.execute("PRAGMA synchronous")
        # NORMAL = 1
        sync = (await cursor.fetchone())[0]
        assert sync == 1

        cursor = await conn.execute("PRAGMA cache_size")
        cache = (await cursor.fetchone())[0]
        assert cache == -32000

        cursor = await conn.execute("PRAGMA foreign_keys")
        fk = (await cursor.fetchone())[0]
        assert fk == 1

        cursor = await conn.execute("PRAGMA temp_store")
        temp = (await cursor.fetchone())[0]
        assert temp == 2  # MEMORY

        await storage.close()

    @pytest.mark.asyncio
    async def test_optimize_runs_on_connect(self) -> None:
        """PRAGMA optimize=0x10002 should run on every new connection."""
        from home_finder.db.storage import _CONNECTION_PRAGMAS

        assert "PRAGMA optimize=0x10002" in _CONNECTION_PRAGMAS
        assert "PRAGMA mmap_size=268435456" not in _CONNECTION_PRAGMAS


class TestHealthCheckReconnectsOnFailure:
    """When SELECT 1 fails, the health check should drop the connection."""

    @pytest.mark.asyncio
    async def test_reconnects_when_execute_raises(self) -> None:
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Force the health check to fire by backdating the timestamp
        storage._last_health_check = time.monotonic() - 60

        # Make execute raise to simulate a dead connection
        old_conn = storage._conn
        assert old_conn is not None
        old_conn.execute = AsyncMock(side_effect=OSError("connection lost"))  # type: ignore[method-assign]

        # _get_connection should detect the failure and reconnect
        conn = await storage._get_connection()
        assert conn is not old_conn
        assert conn is not None

        # New connection should work
        cursor = await conn.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1

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
    async def test_dead_conn_set_to_none_not_closed(self) -> None:
        """Verify the error path sets _conn = None without awaiting close().

        If the worker thread is hung, close() would also hang because it
        queues through the same SimpleQueue. We just abandon the reference.
        """
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        storage._last_health_check = time.monotonic() - 60

        old_conn = storage._conn
        assert old_conn is not None
        old_conn.execute = AsyncMock(side_effect=OSError("hung"))  # type: ignore[method-assign]
        old_conn.close = AsyncMock()  # type: ignore[method-assign]

        await storage._get_connection()

        # close() should NOT have been called on the dead connection
        old_conn.close.assert_not_awaited()

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
