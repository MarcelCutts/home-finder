"""Buffer property diagnostics and flush them with cancellation-safe cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any

from home_finder.logging import get_logger
from home_finder.utils.async_tasks import finish_cleanup

logger = get_logger(__name__)

if TYPE_CHECKING:
    from home_finder.db.storage import PropertyStorage


@dataclass
class PropertyEvent:
    """A buffered diagnostic event.

    The database created_at field records insertion time, not event occurrence.
    Buffer order preserves local recording order, not a global execution clock.
    """

    property_id: str
    source: str
    event_type: str
    stage: str
    metadata: dict[str, Any] | None = None


class EventRecorder:
    """Buffers property events and flushes them to the database in batches.

    Usage::

        async with EventRecorder(storage, run_id) as recorder:
            recorder.record(PropertyEvent(...))
            # auto-flushes on exit
    """

    def __init__(
        self,
        storage: PropertyStorage,
        run_id: int,
    ) -> None:
        self._storage = storage
        self._run_id = run_id
        self._buffer: list[PropertyEvent] = []

    @property
    def run_id(self) -> int:
        return self._run_id

    def record(self, event: PropertyEvent) -> None:
        """Append a single event to the buffer (sync)."""
        self._buffer.append(event)

    def record_batch(self, events: list[PropertyEvent]) -> None:
        """Append multiple events to the buffer (sync)."""
        self._buffer.extend(events)

    async def flush(self) -> None:
        """Write buffered events to the database and clear the buffer."""
        if not self._buffer:
            return
        to_write = self._buffer
        self._buffer = []

        async def insert_batch() -> None:
            await self._storage.runs.insert_property_events(self._run_id, to_write)
            logger.debug("property_events_flushed", count=len(to_write))

        # Finish this one insertion before propagating cancellation. Retrying a
        # cancelled insert could duplicate a batch whose commit already settled.
        await finish_cleanup(
            insert_batch(), primary_error=None, operation_name="property_event_insert"
        )

    async def __aenter__(self) -> EventRecorder:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await finish_cleanup(self.flush(), primary_error=exc, operation_name="property_event_flush")
