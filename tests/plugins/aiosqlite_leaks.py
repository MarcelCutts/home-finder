"""Close aiosqlite connections that a test left open.

aiosqlite v0.22+ starts a non-daemon worker thread per connection that blocks on
``SimpleQueue.get()`` until the connection closes. A test that leaks a connection
(never awaits ``close()``) leaves a thread that prevents the worker process from
exiting, and an open SQLite handle that can keep holding a lock. Every open
connection owns exactly one such thread, so comparing the threads before and after
a test finds its leaks cheaply. Do not scan the heap after every test instead: that
cost 0.4-3 seconds per test and was most of the suite's run time. The heap is
searched only once a leak has been found, to close the leaked connection properly.
"""

from __future__ import annotations

import gc
import threading
import warnings
from collections.abc import Generator

import pytest
from aiosqlite.core import _STOP_RUNNING_SENTINEL, Connection, _connection_worker_thread


def _is_connection_worker(thread: threading.Thread) -> bool:
    # Thread drops _target when it finishes; the name keeps the target's name.
    target = getattr(thread, "_target", None)
    return target is _connection_worker_thread or "_connection_worker_thread" in thread.name


def connection_worker_threads() -> set[threading.Thread]:
    return {
        thread
        for thread in threading.enumerate()
        if thread.is_alive() and _is_connection_worker(thread)
    }


def _database_of(connection: Connection) -> str:
    # aiosqlite.connect() wraps the database argument in a closure.
    connector = connection._connector
    code = getattr(connector, "__code__", None)
    cells = getattr(connector, "__closure__", None) or ()
    if code is not None and "database" in code.co_freevars:
        return str(cells[code.co_freevars.index("database")].cell_contents)
    return "an unknown database"


def close_leaked_connections(existing: set[threading.Thread]) -> list[str]:
    """Close connections opened since ``existing`` was taken; return their databases.

    Connections that were already open (a longer-lived fixture's) are left alone.
    """
    started = connection_worker_threads() - existing
    # close() returns once the worker acknowledges the stop request, a moment before
    # the thread exits. Under load that moment is visible here; let it finish first.
    for thread in started:
        thread.join(timeout=0.05)
    leaked = {thread for thread in started if thread.is_alive()}
    if not leaked:
        return []

    databases = []
    for obj in gc.get_objects():
        if isinstance(obj, Connection) and obj._thread in leaked:
            databases.append(_database_of(obj))
            # stop() closes the SQLite handle on the worker thread, releasing any
            # lock it holds, then ends the thread.
            obj.stop()
    for thread in leaked:
        thread.join(timeout=1.0)
        if thread.is_alive():  # no Connection object found: end the thread directly
            tx = getattr(thread, "_args", (None,))[0]
            if tx is not None and hasattr(tx, "put_nowait"):
                tx.put_nowait((None, lambda: _STOP_RUNNING_SENTINEL))
                thread.join(timeout=1.0)
    return sorted(databases) or ["an unknown database"]


@pytest.fixture(autouse=True)
def _close_leaked_aiosqlite_connections(request: pytest.FixtureRequest) -> Generator[None]:
    existing = connection_worker_threads()
    yield
    # Browser tests run a uvicorn server in a daemon thread that holds its own
    # aiosqlite connection for the whole session; stopping it breaks later tests.
    if request.node.get_closest_marker("browser"):
        return
    leaked = close_leaked_connections(existing)
    if leaked:
        # PytestWarning, not ResourceWarning: Python's default filters hide the latter.
        warnings.warn(
            f"Test leaked aiosqlite connection(s) to {', '.join(leaked)}; close them in the "
            "test or fixture teardown (a background task may have opened them after its test)",
            pytest.PytestWarning,
            stacklevel=1,
        )
