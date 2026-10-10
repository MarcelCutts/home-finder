"""Cancellation-safe completion of bounded persistence and cleanup work."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from home_finder.logging import get_logger

_T = TypeVar("_T")
logger = get_logger(__name__)


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


async def finish_cleanup(
    operation: Awaitable[object], *, primary_error: BaseException | None, operation_name: str
) -> None:
    """Settle bounded cleanup without replacing an already propagating error."""
    completed = False

    async def settle() -> None:
        nonlocal completed
        await operation
        completed = True

    try:
        await finish_before_cancelling(settle())
    except (asyncio.CancelledError, Exception) as exc:
        if primary_error is None:
            raise
        if not completed:
            logger.error(
                "cleanup_failed",
                operation=operation_name,
                error_type=type(exc).__name__,
                primary_error_type=type(primary_error).__name__,
                exc_info=True,
            )
