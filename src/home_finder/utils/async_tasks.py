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
    """Settle cleanup, preserving prior errors and cancellation during the wait."""
    future = asyncio.ensure_future(operation)
    cancelled: asyncio.CancelledError | None = None
    try:
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError as exc:
                # An internally cancelled cleanup is handled by result() below.
                # Retain caller cancellation even if the write subsequently fails.
                if cancelled is None and not future.cancelled():
                    cancelled = exc
        future.result()
    except (asyncio.CancelledError, Exception) as exc:
        original = primary_error if primary_error is not None else cancelled
        if original is None:
            raise
        logger.error(
            "cleanup_failed",
            operation=operation_name,
            error_type=type(exc).__name__,
            primary_error_type=type(original).__name__,
            exc_info=True,
        )
    if primary_error is None and cancelled is not None:
        raise cancelled
