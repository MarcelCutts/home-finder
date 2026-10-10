"""Single-host run exclusion shared by CLI, maintenance and serve scheduling."""

import errno
import fcntl
import os
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from enum import StrEnum
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import get_logger

logger = get_logger(__name__)
_P = ParamSpec("_P")
_T = TypeVar("_T")
_memory_owners: set[int] = set()


class RunSkipped(StrEnum):
    ALREADY_RUNNING = "already_running"


@contextmanager
def runner_lock(database_path: str, *, memory_owner: object | None = None) -> Iterator[bool]:
    """Try a nonblocking advisory lock next to the resolved SQLite authority.

    Keep the lock file: unlinking it can give competing starters distinct inodes.
    Closing the descriptor (including process death) releases ownership. This
    is a single-host local-filesystem contract, not a distributed lease.
    """
    if database_path == ":memory:":
        owner = memory_owner if memory_owner is not None else object()
        key = id(owner)
        if key in _memory_owners:
            yield False
            return
        _memory_owners.add(key)
        try:
            yield True
        finally:
            _memory_owners.remove(key)
        return

    path = Path(database_path).expanduser().resolve()
    if path.exists() and path.stat().st_nlink > 1:
        raise ValueError("Runner authority must not have multiple hard links")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".runner.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
        yield acquired
    finally:
        os.close(fd)


def exclusive_runner(
    operation: Callable[_P, Coroutine[Any, Any, _T]],
) -> Callable[_P, Coroutine[Any, Any, _T | RunSkipped]]:
    """Reject overlapping entry points before opening a DB or calling providers."""

    @wraps(operation)
    async def run(*args: _P.args, **kwargs: _P.kwargs) -> _T | RunSkipped:
        settings = args[0] if args else kwargs.get("settings")
        if not isinstance(settings, Settings):
            raise TypeError("Runner entry point requires Settings")
        storage = kwargs.get("storage")
        path = storage.db_path if isinstance(storage, PropertyStorage) else settings.database_path
        with runner_lock(path, memory_owner=storage) as acquired:
            if not acquired:
                logger.info(
                    "runner_skipped", operation=operation.__name__, reason="already_running"
                )
                return RunSkipped.ALREADY_RUNNING
            return await operation(*args, **kwargs)

    return run
