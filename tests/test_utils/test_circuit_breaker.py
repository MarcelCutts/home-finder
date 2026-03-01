"""Tests for shared circuit breaker utilities."""

from __future__ import annotations

import time

import pytest
import structlog.testing

from home_finder.utils.circuit_breaker import (
    APIUnavailableError,
    CircuitBreaker,
    CircuitBreakerOpenError,
    ConsecutiveFailureBreaker,
)

# ---------------------------------------------------------------------------
# CircuitBreaker (3-state)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(threshold=3, cooldown=300, name="test")
        assert not cb.is_open()
        assert cb.failure_count == 0
        assert cb.state == "closed"

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(threshold=3, cooldown=300, name="test")
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open()
        cb.record_failure()
        assert cb.is_open()
        assert cb.failure_count == 3
        assert cb.state == "open"

    def test_success_resets_counter(self):
        cb = CircuitBreaker(threshold=3, cooldown=300, name="test")
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert not cb.is_open()

    def test_success_closes_open_circuit(self):
        cb = CircuitBreaker(threshold=2, cooldown=300, name="test")
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()
        # Simulate half-open by moving time past cooldown
        cb._opened_at = time.monotonic() - 301
        assert not cb.is_open()  # half-open
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == "closed"

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(threshold=2, cooldown=10, name="test")
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()
        # Move past cooldown
        cb._opened_at = time.monotonic() - 11
        assert not cb.is_open()  # half-open allows retry
        assert cb.state == "half-open"

    def test_failure_in_half_open_reopens(self):
        cb = CircuitBreaker(threshold=2, cooldown=10, name="test")
        cb.record_failure()
        cb.record_failure()
        old_time = cb._opened_at
        # Move past cooldown → half-open
        cb._opened_at = time.monotonic() - 11
        assert not cb.is_open()  # half-open
        # Another failure re-opens with fresh timestamp
        cb.record_failure()
        assert cb.is_open()
        assert cb._opened_at is not None
        assert cb._opened_at > old_time  # type: ignore[operator]

    def test_raise_if_open(self):
        cb = CircuitBreaker(threshold=1, cooldown=300, name="test")
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpenError):
            cb.raise_if_open()

    def test_raise_if_open_does_not_raise_when_closed(self):
        cb = CircuitBreaker(threshold=3, cooldown=300, name="test")
        cb.raise_if_open()  # should not raise


# ---------------------------------------------------------------------------
# ConsecutiveFailureBreaker (2-state)
# ---------------------------------------------------------------------------


class TestConsecutiveFailureBreaker:
    def test_starts_not_tripped(self):
        breaker = ConsecutiveFailureBreaker(threshold=5, name="test")
        assert not breaker.is_tripped
        assert breaker.failure_count == 0

    def test_trips_at_threshold(self):
        breaker = ConsecutiveFailureBreaker(threshold=3, name="test")
        breaker.record_failure()
        breaker.record_failure()
        assert not breaker.is_tripped
        breaker.record_failure()
        assert breaker.is_tripped

    def test_success_resets(self):
        breaker = ConsecutiveFailureBreaker(threshold=3, name="test")
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        assert not breaker.is_tripped
        assert breaker.failure_count == 0

    def test_trips_again_after_reset(self):
        breaker = ConsecutiveFailureBreaker(threshold=2, name="test")
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_tripped
        breaker.record_success()
        assert not breaker.is_tripped
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_tripped

    def test_pre_trip_warning_at_threshold_minus_one(self):
        breaker = ConsecutiveFailureBreaker(threshold=5, name="test-source")
        with structlog.testing.capture_logs() as captured:
            for _ in range(3):
                breaker.record_failure()
            # First 3 failures — no warning yet
            assert not any(e.get("event") == "circuit_breaker_approaching" for e in captured)
            # 4th failure (threshold-1) — should warn
            breaker.record_failure()
            approaching = [e for e in captured if e.get("event") == "circuit_breaker_approaching"]
            assert len(approaching) == 1
            assert approaching[0]["name"] == "test-source"
            assert approaching[0]["next_failure_trips"] is True

    def test_no_pre_trip_warning_with_threshold_1(self):
        """With threshold=1, there's no threshold-1 to warn at."""
        breaker = ConsecutiveFailureBreaker(threshold=1, name="test")
        with structlog.testing.capture_logs() as captured:
            breaker.record_failure()
            assert not any(e.get("event") == "circuit_breaker_approaching" for e in captured)


# ---------------------------------------------------------------------------
# APIUnavailableError inheritance
# ---------------------------------------------------------------------------


class TestAPIUnavailableError:
    def test_is_subclass_of_circuit_breaker_open_error(self):
        assert issubclass(APIUnavailableError, CircuitBreakerOpenError)

    def test_caught_by_parent_except(self):
        with pytest.raises(CircuitBreakerOpenError):
            raise APIUnavailableError("API down")

    def test_caught_by_own_type(self):
        with pytest.raises(APIUnavailableError):
            raise APIUnavailableError("API down")
