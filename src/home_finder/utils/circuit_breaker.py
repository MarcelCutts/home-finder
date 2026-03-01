"""Shared circuit breaker utilities.

Provides two breaker variants:

- ``CircuitBreaker`` — 3-state (Closed → Open → Half-Open) with cooldown.
  Used by the quality filter to guard the Anthropic API.
- ``ConsecutiveFailureBreaker`` — simpler 2-state for inline loops.
  Used by the off-market checker per source.
"""

from __future__ import annotations

import time
from typing import Final

from home_finder.logging import get_logger

logger = get_logger(__name__)


class CircuitBreakerOpenError(Exception):
    """Raised when a circuit breaker is open and the call should be skipped."""


class APIUnavailableError(CircuitBreakerOpenError):
    """Raised when the Anthropic API circuit breaker is open.

    Subclass of ``CircuitBreakerOpenError`` for backward compatibility.
    """


class CircuitBreaker:
    """Three-state circuit breaker: Closed → Open → Half-Open.

    After *threshold* consecutive failures the circuit opens.  It stays
    open for *cooldown* seconds, then enters half-open state and allows
    one probe call.  A success closes the circuit; a failure re-opens it
    with a fresh timestamp.

    Thread-safety note: designed for single-threaded asyncio — no lock.
    """

    __slots__ = (
        "_consecutive_failures",
        "_cooldown",
        "_name",
        "_open",
        "_opened_at",
        "_threshold",
    )

    def __init__(self, threshold: int, cooldown: float, name: str) -> None:
        self._threshold: Final = threshold
        self._cooldown: Final = cooldown
        self._name: Final = name
        self._consecutive_failures: int = 0
        self._open: bool = False
        self._opened_at: float | None = None

    # -- state queries --------------------------------------------------------

    @property
    def failure_count(self) -> int:
        return self._consecutive_failures

    @property
    def state(self) -> str:
        """Return human-readable state: 'closed', 'open', or 'half-open'."""
        if not self._open:
            return "closed"
        if self._opened_at is not None and (time.monotonic() - self._opened_at) >= self._cooldown:
            return "half-open"
        return "open"

    def is_open(self) -> bool:
        """Return ``True`` if the circuit is open (within cooldown).

        Returns ``False`` in half-open state (after cooldown expires) to
        allow one probe call.
        """
        if not self._open:
            return False
        elapsed = time.monotonic() - self._opened_at  # type: ignore[operator]
        if elapsed >= self._cooldown:
            logger.info(
                "circuit_breaker_half_open",
                name=self._name,
                cooldown_seconds=self._cooldown,
                elapsed_seconds=round(elapsed, 1),
            )
            return False  # half-open — allow one retry
        return True

    def raise_if_open(self) -> None:
        """Raise ``CircuitBreakerOpenError`` if the circuit is currently open."""
        if self.is_open():
            raise CircuitBreakerOpenError(f"Circuit breaker '{self._name}' is open — call skipped")

    # -- recording ------------------------------------------------------------

    def record_failure(self) -> None:
        """Increment failure counter; open the circuit at threshold."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._open = True
            self._opened_at = time.monotonic()
            logger.warning(
                "circuit_breaker_open",
                name=self._name,
                consecutive_failures=self._consecutive_failures,
            )

    def record_success(self) -> None:
        """Reset failure counter and close the circuit (half-open → closed)."""
        if self._open:
            logger.info("circuit_breaker_closed", name=self._name)
        self._consecutive_failures = 0
        self._open = False
        self._opened_at = None


class ConsecutiveFailureBreaker:
    """Simple 2-state breaker: counts consecutive failures, trips at threshold.

    Any success immediately resets the counter (no cooldown).
    """

    __slots__ = ("_consecutive_failures", "_name", "_threshold")

    def __init__(self, threshold: int, name: str) -> None:
        self._threshold: Final = threshold
        self._name: Final = name
        self._consecutive_failures: int = 0

    @property
    def is_tripped(self) -> bool:
        return self._consecutive_failures >= self._threshold

    @property
    def failure_count(self) -> int:
        return self._consecutive_failures

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures == self._threshold - 1 and self._threshold > 1:
            logger.warning(
                "circuit_breaker_approaching",
                name=self._name,
                failures=f"{self._consecutive_failures}/{self._threshold}",
                next_failure_trips=True,
            )

    def record_success(self) -> None:
        self._consecutive_failures = 0
