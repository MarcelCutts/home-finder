"""Cancellation-safe completion of bounded persistence and cleanup work."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

_T = TypeVar("_T")


async def finish_before_cancelling(operation: Awaitable[_T]) -> _T:
    """Wait for cleanup/accounting to finish, then propagate any cancellation.

    Shielding alone leaves a detached task. Retain the task and defer repeated
    cancellations until it settles. Use for bounded cleanup, not new provider work.
    """
    future = asyncio.ensure_future(operation)
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
    result = future.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
